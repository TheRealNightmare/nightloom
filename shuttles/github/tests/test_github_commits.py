"""The GitHub commit authors Shuttle against a fake GitHub: no network.

The commits are fictional, on github.com/example-org/widgets: alex (linked
account, two addresses over three commits), sam (linked account, GitHub's
no-reply address), an unlinked address, a bot, and a malformed item.
"""

from collections.abc import Iterable
from typing import Any

import httpx
import pytest

from nightloom_core.entities import EntityType, Sensitivity, build_entity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    FailureReason,
    Finding,
    RelationshipFinding,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.http import create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_github import GithubCommitsShuttle
from nightloom_shuttle_github.client import BASE_URL, HOST
from nightloom_shuttle_github.commits import (
    LINKED_EMAIL,
    MAX_AUTHORS,
    MAX_COMMITS,
    NOTE_BOTS,
    NOTE_CAPPED,
    NOTE_NO_AUTHORS,
    NOTE_NOT_GITHUB,
    NOTE_RATE_LIMITED,
    NOTE_SKIPPED,
    NOTE_UNKNOWN,
    SELF_ASSERTED,
)

USERNAME = EntityType.USERNAME
EMAIL = EntityType.EMAIL
REPOSITORY = EntityType.REPOSITORY
R = RelationType
META = GithubCommitsShuttle.meta
REPO = "github.com/example-org/widgets"
PATH = "repos/example-org/widgets/commits"


def commit(
    email: str,
    when: str,
    *,
    login: str | None = None,
    account_type: str = "User",
    name: str = "Alex Example",
    message: str = "Fix the widget",
) -> dict[str, Any]:
    person = {"name": name, "email": email, "date": when}
    return {
        "sha": "0" * 40,
        "commit": {"author": person, "committer": person, "message": message},
        "author": None if login is None else {"login": login, "type": account_type},
        "committer": None,
    }


COMMITS = [
    commit("alex@example.org", "2026-11-20T08:00:00Z", login="Alex-Dev"),
    commit("alex@example.org", "2026-11-18T08:00:00+02:00", login="alex-dev"),
    commit("alex.work@example.com", "2026-11-10T08:00:00Z", login="alex-dev"),
    commit("12345+sam@users.noreply.github.com", "2026-11-19T08:00:00Z", login="sam-x", name="Sam"),
    commit("drive-by@example.net", "2026-10-01T08:00:00Z", name="Drive By"),
    commit("bot@example.org", "2026-11-21T08:00:00Z", login="renovate[bot]", account_type="Bot"),
    commit("49699333+dependabot[bot]@users.noreply.github.com", "2026-11-21T08:00:00Z"),
    {"sha": "1" * 40},
]


class FakeGithub:
    def __init__(self, reply: Any = None) -> None:
        self.reply = COMMITS if reply is None else reply
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert str(request.url).startswith(BASE_URL) and request.url.host == HOST
        if request.url.path.removeprefix("/") != PATH:
            return httpx.Response(404, json={"message": "Not Found"})
        if isinstance(self.reply, httpx.Response):
            return self.reply
        return httpx.Response(200, json=self.reply)


async def run_shuttle(
    fake: FakeGithub, value: str = REPO, *, notes_only: bool = False
) -> tuple[list[Finding], ShuttleContext]:
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=lambda: clock[0], sleep=sleep)
    http = create_http_client(META, transport=httpx.MockTransport(fake.handler), limiter=limiter)
    ctx = ShuttleContext(META, http=http)
    entity = entity_input(REPOSITORY, value)
    shuttle = GithubCommitsShuttle(sleep=sleep, clock=lambda: clock[0], wall_clock=lambda: 0.0)
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, found, entity)
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    text = repr(found) + repr(ctx.notes)
    # Never names, messages, no-reply addresses or bots.
    for absent in ("Alex Example", "Drive By", "Fix the widget", "noreply", "[bot]", "renovate"):
        assert absent not in text, absent
    return found, ctx


def entities(found: list[Finding], entity_type: EntityType) -> dict[str, Any]:
    return {
        f.value: (dict(f.properties or {}), f.admiralty, f.confidence)
        for f in found
        if isinstance(f, EntityFinding) and f.type == entity_type
    }


def edges(found: Iterable[Finding], relation: RelationType) -> dict[tuple[str, str], Any]:
    return {
        (f.source.value, f.target.value): (f.note, f.admiralty, f.confidence)
        for f in found
        if isinstance(f, RelationshipFinding) and f.type == relation
    }


def test_meta_and_registration() -> None:
    assert META.name == "github-commits"
    assert META.input_types == {REPOSITORY}
    assert META.output_entity_types == {USERNAME, EMAIL}
    assert META.output_relationship_types == {R.CONTRIBUTED_TO, R.USES_EMAIL}
    # Commit authors are people: person-related, never in an auto-pivot.
    assert META.sensitivity is Sensitivity.PERSON
    assert META.pivots is False
    assert META.cache_ttl_seconds == 24 * 3600
    assert ShuttleRegistry.discover().get("github-commits") is GithubCommitsShuttle


async def test_commit_authors() -> None:
    fake = FakeGithub()
    found, ctx = await run_shuttle(fake)
    (request,) = fake.requests
    assert request.url.params["per_page"] == str(MAX_COMMITS)

    assert entities(found, USERNAME) == {
        "alex-dev": ({"platform": "GitHub"}, None, None),
        "sam-x": ({"platform": "GitHub"}, None, None),
    }
    assert entities(found, EMAIL) == {
        "alex@example.org": ({}, *SELF_ASSERTED),
        "alex.work@example.com": ({}, *SELF_ASSERTED),
        "drive-by@example.net": ({}, *SELF_ASSERTED),
    }
    read = len(COMMITS)
    assert edges(found, R.CONTRIBUTED_TO) == {
        ("alex-dev", REPO): (f"3 of the latest {read} commits, latest 2026-11-20", None, None),
        ("sam-x", REPO): (f"1 of the latest {read} commits, latest 2026-11-19", None, None),
        ("alex@example.org", REPO): (
            f"2 of the latest {read} commits, latest 2026-11-20",
            *SELF_ASSERTED,
        ),
        ("alex.work@example.com", REPO): (
            f"1 of the latest {read} commits, latest 2026-11-10",
            *SELF_ASSERTED,
        ),
        ("drive-by@example.net", REPO): (
            f"1 of the latest {read} commits, latest 2026-10-01",
            *SELF_ASSERTED,
        ),
    }
    assert edges(found, R.USES_EMAIL) == {
        ("alex-dev", "alex@example.org"): (f"in commits to {REPO}", *LINKED_EMAIL),
        ("alex-dev", "alex.work@example.com"): (f"in commits to {REPO}", *LINKED_EMAIL),
    }
    assert NOTE_BOTS.format(count=2) in ctx.notes
    assert NOTE_SKIPPED.format(count=1) in ctx.notes


async def test_authors_are_capped_by_commit_count() -> None:
    many = [
        commit(f"dev{n}@example.org", "2026-11-01T00:00:00Z", login=f"dev{n}")
        for n in range(MAX_AUTHORS + 5)
    ]
    many += [commit("dev7@example.org", "2026-11-02T00:00:00Z", login="dev7")]
    found, ctx = await run_shuttle(FakeGithub(many))
    usernames = entities(found, USERNAME)
    assert len(usernames) == MAX_AUTHORS
    assert "dev7" in usernames  # two commits: kept first
    assert (
        NOTE_CAPPED.format(count=MAX_AUTHORS + 5, what="accounts", limit=MAX_AUTHORS) in ctx.notes
    )
    # Links only between an account and an address that were both kept.
    emails = entities(found, EMAIL)
    links = edges(found, R.USES_EMAIL)
    assert links and all(login in usernames and address in emails for login, address in links)


async def test_only_github_and_empty_repositories() -> None:
    fake = FakeGithub()
    found, ctx = await run_shuttle(fake, "gitlab.com/example/tools")
    assert (found, ctx.notes, fake.requests) == ([], (NOTE_NOT_GITHUB,), [])
    found, ctx = await run_shuttle(
        FakeGithub(httpx.Response(409, json={"message": "Git Repository is empty."}))
    )
    assert (found, ctx.notes) == ([], (NOTE_UNKNOWN,))
    found, ctx = await run_shuttle(FakeGithub(), "github.com/example-org/missing")
    assert (found, ctx.notes) == ([], (NOTE_UNKNOWN,))
    bots_only = [commit("x@users.noreply.github.com", "2026-11-01T00:00:00Z", login="ci[bot]")]
    found, ctx = await run_shuttle(FakeGithub(bots_only))
    assert found == [] and NOTE_NO_AUTHORS in ctx.notes


@pytest.mark.parametrize(
    ("reply", "reason", "note"),
    [
        ({"commits": []}, FailureReason.SOURCE_INVALID_RESPONSE, None),
        (
            httpx.Response(
                403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "999999"}
            ),
            FailureReason.SOURCE_UNAVAILABLE,
            NOTE_RATE_LIMITED,
        ),
        (httpx.Response(503), FailureReason.SOURCE_UNAVAILABLE, None),
    ],
)
async def test_errors(reply: Any, reason: FailureReason, note: str | None) -> None:
    fake = FakeGithub(reply)
    meta = META
    ctx = ShuttleContext(
        meta, http=create_http_client(meta, transport=httpx.MockTransport(fake.handler))
    )

    async def no_sleep(seconds: float) -> None:
        return None

    shuttle = GithubCommitsShuttle(sleep=no_sleep, wall_clock=lambda: 0.0)
    with pytest.raises(ShuttleFailedError) as info:
        await collect(shuttle, entity_input(REPOSITORY, REPO), ctx=ctx)
    assert info.value.reason is reason
    if note is not None:
        assert note in ctx.notes


@pytest.mark.network
async def test_real_github() -> None:
    """The latest commits of github.com/octocat/Hello-World. Only with ``make test-network``."""
    ctx = ShuttleContext(META)
    found = await collect(
        GithubCommitsShuttle(), entity_input(REPOSITORY, "github.com/octocat/hello-world"), ctx=ctx
    )
    assert found or ctx.notes
