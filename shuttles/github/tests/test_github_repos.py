"""The GitHub repositories Shuttle against a fake GitHub: no network.

Accounts and repositories are fictional: example-org (Example Org, verified,
website example.org) with three public repositories and one private one;
two unrelated accounts both named "Example Trading".
"""

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from nightloom_core.entities import EntityType, Sensitivity, build_entity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    FailureReason,
    Finding,
    JobCancelledError,
    RelationshipFinding,
    ShuttleConfigurationError,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.http import create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_github import GithubRepositoriesShuttle
from nightloom_shuttle_github.client import ACCEPT, API_VERSION, BASE_URL, HOST
from nightloom_shuttle_github.config import TOKEN_VARIABLE, load_token
from nightloom_shuttle_github.repositories import (
    MAX_REPOSITORIES,
    NAME_MATCH,
    NOTE_BAD_LOGIN,
    NOTE_CAPPED,
    NOTE_NO_MATCH,
    NOTE_RATE_LIMITED,
    NOTE_TOKEN_REFUSED,
    NOTE_UNKNOWN_LOGIN,
    UNVERIFIED_WEBSITE,
    VERIFIED_WEBSITE,
    WEBSITE_NOTE,
)

ORG = EntityType.ORGANIZATION
DOMAIN = EntityType.DOMAIN
REPOSITORY = EntityType.REPOSITORY
R = RelationType
META = GithubRepositoriesShuttle.meta
NOW = datetime(2026, 11, 26, 6, 0, tzinfo=UTC)
TOKEN = "github_pat_" + "A1b2" * 20
WALL = 1_800_000_000.0


def org(login: str, name: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "login": login,
        "id": 1,
        "type": "Organization",
        "name": name,
        "blog": extra.get("blog", ""),
        "is_verified": extra.get("verified", False),
        "public_repos": extra.get("public_repos", 3),
        "email": "press@example.org",
        "location": "Exampleton",
        "description": "We make widgets.",
    }


def repo(full_name: str, **extra: Any) -> dict[str, Any]:
    return {
        "full_name": full_name,
        "private": extra.get("private", False),
        "visibility": "private" if extra.get("private") else "public",
        "html_url": f"https://github.com/{full_name}",
        "description": extra.get("description", "Widgets for everyone"),
        "fork": extra.get("fork", False),
        "archived": extra.get("archived", False),
        "language": "Python",
        "topics": ["osint", "graph"],
        "license": {"spdx_id": extra.get("license", "Apache-2.0")},
        "stargazers_count": 12,
        "forks_count": 3,
        "homepage": extra.get("homepage", "https://widgets.example.org/"),
        "default_branch": "main",
        "created_at": "2020-01-02T03:04:05Z",
        "pushed_at": "2026-11-20T08:00:00Z",
    }


EXAMPLE_ORG = org("Example-Org", "Example Org", blog="www.example.org", verified=True)
REPOS = [
    repo("Example-Org/Widgets"),
    repo("example-org/docs", fork=True, license="NOASSERTION", homepage=""),
    repo("example-org/old-site", archived=True, description="x" * 300),
    repo("example-org/secret-plans", private=True),
    {"full_name": "example-org/bad name!"},
]

Reply = dict[str, Any] | list[Any] | bytes | httpx.Response


class Replies:
    """Replies given in turn (the last one repeats)."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)

    def next(self) -> Reply:
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeGithub:
    """Answers by path (searches: ``search:<q>``); unknown paths are 404."""

    def __init__(self, routes: dict[str, Reply | Replies] | None = None) -> None:
        self.routes: dict[str, Reply | Replies] = {
            "orgs/example-org": EXAMPLE_ORG,
            "orgs/example-org/repos": REPOS,
            "search:Example Org type:org": {
                "items": [{"login": "example-org"}, {"login": "example-fans"}]
            },
            "orgs/example-fans": org("example-fans", "Fans of Example Org"),
            "search:Example Trading type:org": {
                "items": [{"login": "example-trading"}, {"login": "exampletrading"}]
            },
            "orgs/example-trading": org("example-trading", "Example Trading"),
            "orgs/exampletrading": org("exampletrading", "EXAMPLE  TRADING"),
        }
        self.routes.update(routes or {})
        self.requests: list[httpx.Request] = []

    @staticmethod
    def route(request: httpx.Request) -> str:
        path = request.url.path.removeprefix("/")
        if path == "search/users":
            return f"search:{request.url.params['q']}"
        return path.lower()

    @property
    def calls(self) -> list[str]:
        return [self.route(request) for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert str(request.url).startswith(BASE_URL)
        assert request.url.host == HOST
        assert request.headers["Accept"] == ACCEPT
        assert request.headers["X-GitHub-Api-Version"] == API_VERSION
        assert request.headers["User-Agent"].startswith("Nightloom/")
        reply = self.routes.get(self.route(request))
        if reply is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if isinstance(reply, Replies):
            reply = reply.next()
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)


async def run_shuttle(
    fake: FakeGithub,
    entity: EntityInput,
    *,
    token: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[list[Finding], ShuttleContext, FakeTime]:
    time = FakeTime()
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(META, transport=httpx.MockTransport(fake.handler), limiter=limiter)
    ctx = ShuttleContext(META, http=http, cancel_event=cancel_event)
    shuttle = GithubRepositoriesShuttle(
        token=token, sleep=time.sleep, clock=time, wall_clock=lambda: WALL, now=lambda: NOW
    )
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, found, entity)
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    text = repr(found) + repr(ctx.notes)
    for private in ("press@example.org", "Exampleton", "secret-plans", TOKEN):
        assert private not in text, private
    return found, ctx, time


def organization(value: str, **properties: Any) -> EntityInput:
    entity = entity_input(ORG, value)
    return entity.model_copy(update={"properties": properties}) if properties else entity


def entities(found: list[Finding], entity_type: EntityType) -> dict[str, dict[str, Any]]:
    return {
        f.value: dict(f.properties or {})
        for f in found
        if isinstance(f, EntityFinding) and f.type == entity_type
    }


def edges(found: Iterable[Finding], relation: RelationType) -> dict[tuple[str, str], Any]:
    return {
        (f.source.value, f.target.value): (f.admiralty, f.confidence, f.note)
        for f in found
        if isinstance(f, RelationshipFinding) and f.type == relation
    }


# --- metadata and configuration ------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "github-repos"
    assert META.input_types == {ORG}
    assert META.output_entity_types == {ORG, DOMAIN, REPOSITORY}
    assert META.output_relationship_types == {R.OWNS, R.HAS_REPOSITORY}
    assert META.sensitivity is Sensitivity.ORGANIZATION
    assert (META.pivots, META.network, META.cache_ttl_seconds) == (False, True, 24 * 3600)
    assert META.usage_note is not None and TOKEN_VARIABLE in META.usage_note
    assert ShuttleRegistry.discover().get("github-repos") is GithubRepositoriesShuttle


def test_token_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
    assert load_token() is None
    GithubRepositoriesShuttle.check_configuration()
    monkeypatch.setenv(TOKEN_VARIABLE, f" {TOKEN} ")
    assert load_token() == TOKEN
    monkeypatch.setenv(TOKEN_VARIABLE, "ghp_" + "x" * 36)
    assert load_token() is not None
    monkeypatch.setenv(TOKEN_VARIABLE, "password123")
    with pytest.raises(ShuttleConfigurationError) as info:
        GithubRepositoriesShuttle.check_configuration()
    assert "password123" not in str(info.value)


# --- finding the account --------------------------------------------------------------------


async def test_by_github_login() -> None:
    fake = FakeGithub()
    found, ctx, _ = await run_shuttle(
        fake, organization("Example Org", github_login="Example-Org"), token=TOKEN
    )
    assert fake.calls == ["orgs/example-org", "orgs/example-org/repos"]
    assert fake.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    repos_request = fake.requests[1]
    assert dict(repos_request.url.params) == {
        "type": "public",
        "sort": "pushed",
        "direction": "desc",
        "per_page": str(MAX_REPOSITORIES),
    }

    assert entities(found, ORG) == {
        "Example Org": {"github_login": "example-org", "github_checked_at": NOW}
    }
    repos = entities(found, REPOSITORY)
    assert set(repos) == {
        "github.com/example-org/widgets",
        "github.com/example-org/docs",
        "github.com/example-org/old-site",
    }
    assert repos["github.com/example-org/widgets"] == {
        "description": "Widgets for everyone",
        "language": "Python",
        "topics": ["osint", "graph"],
        "license": "Apache-2.0",
        "stars": 12,
        "forks": 3,
        "fork": False,
        "archived": False,
        "homepage": "https://widgets.example.org/",
        "default_branch": "main",
        "created_at": datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC),
        "pushed_at": datetime(2026, 11, 20, 8, 0, tzinfo=UTC),
        "github_checked_at": NOW,
    }
    docs = repos["github.com/example-org/docs"]
    assert docs["fork"] is True and "license" not in docs and "homepage" not in docs
    description = repos["github.com/example-org/old-site"]["description"]
    assert len(description) == 200 and description.endswith("…")

    links = edges(found, R.HAS_REPOSITORY)
    assert set(links) == {("Example Org", value) for value in repos}
    assert all(rating == (None, None, None) for rating in links.values())  # A2 by default
    # The verified account's website.
    assert edges(found, R.OWNS) == {
        ("Example Org", "example.org"): (*VERIFIED_WEBSITE, WEBSITE_NOTE)
    }
    assert "Ignored 2 private, malformed or unusable item(s) in GitHub's answer." in ctx.notes


async def test_by_name() -> None:
    fake = FakeGithub()
    found, _, _ = await run_shuttle(fake, organization("Example Org"))
    assert fake.calls == [
        "search:Example Org type:org",
        "orgs/example-org",
        "orgs/example-fans",  # read, but its name is not "Example Org"
        "orgs/example-org/repos",
    ]
    assert "Authorization" not in fake.requests[0].headers  # no token
    assert entities(found, ORG)["Example Org"]["github_login"] == "example-org"
    org_finding = next(f for f in found if isinstance(f, EntityFinding) and f.type == ORG)
    assert (org_finding.admiralty, org_finding.confidence) == NAME_MATCH
    assert all(rating[:2] == NAME_MATCH for rating in edges(found, R.HAS_REPOSITORY).values())
    assert edges(found, R.OWNS)[("Example Org", "example.org")][:2] == UNVERIFIED_WEBSITE


async def test_login_as_the_name() -> None:
    fake = FakeGithub({"search:example-org type:org": {"items": [{"login": "example-org"}]}})
    found, _, _ = await run_shuttle(fake, organization("example-org"))
    assert entities(found, ORG)["example-org"]["github_login"] == "example-org"


async def test_several_and_no_matches() -> None:
    found, ctx, _ = await run_shuttle(FakeGithub(), organization("Example Trading"))
    assert found == []
    assert ctx.notes[0].startswith("2 GitHub organization accounts have this name")
    assert "example-trading, exampletrading" in ctx.notes[0]
    found, ctx, _ = await run_shuttle(FakeGithub(), organization("Nobody Ltd"))
    assert (found, ctx.notes) == ([], (NOTE_NO_MATCH,))


async def test_unknown_and_bad_login() -> None:
    found, ctx, _ = await run_shuttle(FakeGithub(), organization("X", github_login="nobody"))
    assert (found, ctx.notes) == ([], (NOTE_UNKNOWN_LOGIN.format(login="nobody"),))
    fake = FakeGithub()
    found, ctx, _ = await run_shuttle(fake, organization("Example Org", github_login="-bad-"))
    assert NOTE_BAD_LOGIN in ctx.notes
    assert fake.calls[0] == "search:Example Org type:org"


async def test_user_accounts_are_not_organizations() -> None:
    person = {**org("example-org", "Example Org"), "type": "User"}
    found, ctx, _ = await run_shuttle(
        FakeGithub({"orgs/example-org": person}), organization("X", github_login="example-org")
    )
    assert found == []
    assert NOTE_UNKNOWN_LOGIN.format(login="example-org") in ctx.notes


async def test_many_repositories_are_capped() -> None:
    big = {**EXAMPLE_ORG, "public_repos": 250}
    _, ctx, _ = await run_shuttle(
        FakeGithub({"orgs/example-org": big}), organization("X", github_login="example-org")
    )
    assert NOTE_CAPPED.format(count=250, limit=MAX_REPOSITORIES) in ctx.notes


# --- errors -----------------------------------------------------------------------------------


async def test_rate_limit_used_up() -> None:
    limited = httpx.Response(
        403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(WALL) + 1800)},
        json={"message": "API rate limit exceeded"},
    )
    fake = FakeGithub({"orgs/example-org": limited})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake, organization("X", github_login="example-org"))
    assert info.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert len(fake.requests) == 1


async def test_rate_limit_note_and_short_waits() -> None:
    limited = httpx.Response(429, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "x"})
    fake = FakeGithub({"orgs/example-org": limited})
    ctx = ShuttleContext(
        META, http=create_http_client(META, transport=httpx.MockTransport(fake.handler))
    )
    with pytest.raises(ShuttleFailedError):
        await collect(
            GithubRepositoriesShuttle(token=None),
            organization("X", github_login="example-org"),
            ctx=ctx,
        )
    assert NOTE_RATE_LIMITED in ctx.notes

    soon = httpx.Response(
        403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(WALL) + 10)}
    )
    secondary = httpx.Response(403, headers={"Retry-After": "3"})
    fake = FakeGithub(
        {"orgs/example-org": Replies(soon, secondary, httpx.Response(502), EXAMPLE_ORG)}
    )
    found, ctx, time = await run_shuttle(fake, organization("X", github_login="example-org"))
    assert entities(found, ORG)["X"]["github_login"] == "example-org"
    assert time.sleeps[:3] == [10.0, 3.0, 2.0]
    assert ctx.last_progress is not None and "1 retried, 2 rate-limited" in ctx.last_progress


async def test_refused_token() -> None:
    fake = FakeGithub({"orgs/example-org": httpx.Response(401, json={"message": TOKEN})})
    meta = META
    ctx = ShuttleContext(
        meta, http=create_http_client(meta, transport=httpx.MockTransport(fake.handler))
    )
    with pytest.raises(ShuttleFailedError) as info:
        await collect(
            GithubRepositoriesShuttle(token=TOKEN),
            organization("X", github_login="example-org"),
            ctx=ctx,
        )
    assert info.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert ctx.notes == (NOTE_TOKEN_REFUSED,)
    assert TOKEN not in repr(info.value) + repr(info.value.__cause__)


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (httpx.Response(403, json={"message": "Forbidden"}), FailureReason.SOURCE_INVALID_RESPONSE),
        (b"<html>", FailureReason.SOURCE_INVALID_RESPONSE),
        ({"repos": []}, FailureReason.SOURCE_INVALID_RESPONSE),
        (httpx.Response(503), FailureReason.SOURCE_UNAVAILABLE),
    ],
)
async def test_bad_repository_lists_fail_the_job(reply: Reply, reason: FailureReason) -> None:
    fake = FakeGithub({"orgs/example-org/repos": reply})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake, organization("X", github_login="example-org"))
    assert info.value.reason is reason


async def test_cancellation_stops_before_a_request() -> None:
    cancelled = asyncio.Event()
    cancelled.set()
    fake = FakeGithub()
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, organization("Example Org"), cancel_event=cancelled)
    assert fake.requests == []


@pytest.mark.network
async def test_real_github() -> None:
    """The real ``github`` organization account. Only with ``make test-network``."""
    ctx = ShuttleContext(META)
    found = await collect(
        GithubRepositoriesShuttle(), organization("GitHub", github_login="github"), ctx=ctx
    )
    assert entities(found, ORG)["GitHub"]["github_login"] == "github", ctx.notes
    assert any(value.startswith("github.com/github/") for value in entities(found, REPOSITORY))
