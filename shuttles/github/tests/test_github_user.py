"""The GitHub profile Shuttle against a fake GitHub: no network, fictional accounts."""

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
    RelationshipFinding,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.http import create_http_client
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_github import GithubProfileShuttle
from nightloom_shuttle_github.profiles import (
    NOTE_ORGANIZATION,
    NOTE_OTHER_PLATFORM,
    NOTE_SAME_NAME,
    NOTE_UNKNOWN,
    UNKNOWN_PLATFORM,
)

USERNAME = EntityType.USERNAME
PROFILE = EntityType.SOCIAL_PROFILE
META = GithubProfileShuttle.meta

ALEX = {
    "login": "Alex-Dev",
    "type": "User",
    "name": "Alex Example",
    "company": "@example-org",
    "blog": "alex.example.org",
    "location": "Exampleton",
    "email": "alex@example.org",
    "bio": "Widgets and long walks.",
    "twitter_username": "alex_x",
    "avatar_url": "https://avatars.githubusercontent.com/u/1",
    "followers": 12,
    "public_repos": 7,
    "created_at": "2019-04-05T06:07:08Z",
}


class FakeGithub:
    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes = {
            "users/alex-dev": ALEX,
            "users/example-org": {"login": "example-org", "type": "Organization"},
        }
        self.routes.update(routes or {})
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "api.github.com"
        reply = self.routes.get(request.url.path.removeprefix("/").lower())
        if reply is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)


async def run_shuttle(
    fake: FakeGithub, entity: EntityInput
) -> tuple[list[Finding], ShuttleContext]:
    async def sleep(seconds: float) -> None:
        return None

    ctx = ShuttleContext(
        META, http=create_http_client(META, transport=httpx.MockTransport(fake.handler))
    )
    shuttle = GithubProfileShuttle(sleep=sleep, clock=lambda: 0.0, wall_clock=lambda: 0.0)
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, found, entity)
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    text = repr(found) + repr(ctx.notes)
    for absent in ("Exampleton", "alex@example.org", "long walks", "alex_x", "avatars"):
        assert absent not in text, absent
    return found, ctx


def username(value: str, platform: str | None = None) -> EntityInput:
    entity = entity_input(USERNAME, value)
    return entity.model_copy(update={"properties": {"platform": platform}}) if platform else entity


def test_meta() -> None:
    assert META.name == "github-user"
    assert META.input_types == {USERNAME}
    assert META.output_relationship_types == {RelationType.HAS_PROFILE}
    assert META.sensitivity is Sensitivity.PERSON and META.pivots is False
    assert ShuttleRegistry.discover().get("github-user") is GithubProfileShuttle


async def test_profile_of_a_github_username() -> None:
    fake = FakeGithub()
    found, ctx = await run_shuttle(fake, username("alex-dev", "GitHub"))
    assert [r.url.path for r in fake.requests] == ["/users/alex-dev"]
    (profile,) = [f for f in found if isinstance(f, EntityFinding)]
    assert profile.value == "https://github.com/alex-dev"
    assert profile.properties == {
        "platform": "GitHub",
        "profile_url": "https://github.com/alex-dev",
        "display_name": "Alex Example",
        "company": "@example-org",
        "website": "https://alex.example.org",
        "created_at": "2019-04-05T06:07:08Z",
        "followers": 12,
        "public_repos": 7,
    }
    assert build_entity(PROFILE, profile.value, profile.properties).stored_properties()[
        "created_at"
    ] == (datetime(2019, 4, 5, 6, 7, 8, tzinfo=UTC).isoformat().replace("+00:00", "Z"))
    (link,) = [f for f in found if isinstance(f, RelationshipFinding)]
    assert (link.source.value, link.admiralty, link.confidence) == ("alex-dev", None, None)
    assert ctx.notes == ()


async def test_username_without_a_platform_is_rated_lower() -> None:
    found, ctx = await run_shuttle(FakeGithub(), username("alex-dev"))
    (link,) = [f for f in found if isinstance(f, RelationshipFinding)]
    assert (link.admiralty, link.confidence) == UNKNOWN_PLATFORM
    assert NOTE_SAME_NAME in ctx.notes


async def test_other_cases() -> None:
    fake = FakeGithub()
    found, ctx = await run_shuttle(fake, username("alex-dev", "Mastodon"))
    assert (found, ctx.notes, fake.requests) == ([], (NOTE_OTHER_PLATFORM,), [])
    found, ctx = await run_shuttle(FakeGithub(), username("example-org", "GitHub"))
    assert (found, ctx.notes) == ([], (NOTE_ORGANIZATION,))
    found, ctx = await run_shuttle(FakeGithub(), username("nobody", "GitHub"))
    assert (found, ctx.notes) == ([], (NOTE_UNKNOWN,))
    found, ctx = await run_shuttle(FakeGithub(), username("not a login", "GitHub"))
    assert (found, ctx.notes) == ([], (NOTE_UNKNOWN,))


async def test_answer_for_another_account_fails() -> None:
    fake = FakeGithub({"users/alex-dev": {**ALEX, "login": "someone-else"}})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake, username("alex-dev", "GitHub"))
    assert info.value.reason is FailureReason.SOURCE_INVALID_RESPONSE


@pytest.mark.network
async def test_real_github() -> None:
    """The real octocat account. Only with ``make test-network``."""
    ctx = ShuttleContext(META)
    found = await collect(GithubProfileShuttle(), username("octocat", "GitHub"), ctx=ctx)
    assert any(
        isinstance(f, EntityFinding) and f.value == "https://github.com/octocat" for f in found
    )
