"""The username presence Shuttle against fake platforms: no network."""

import asyncio
from typing import Any

import httpx
import pytest

from nightloom_core.entities import EntityType, Sensitivity, build_entity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    FailureReason,
    Finding,
    JobCancelledError,
    RelationshipFinding,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.http import create_http_client
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_usernames import UsernamePresenceShuttle
from nightloom_shuttle_usernames.platforms import HOSTS, PLATFORMS, Answer
from nightloom_shuttle_usernames.shuttle import LEAD, NOTE_LEAD, NOTE_NOT_FOUND

USERNAME = EntityType.USERNAME
PROFILE = EntityType.SOCIAL_PROFILE
META = UsernamePresenceShuttle.meta
BY_NAME = {platform.name: platform for platform in PLATFORMS}

# How each platform answers for an account that exists.
EXISTS: dict[str, httpx.Response] = {
    "api.github.com": httpx.Response(200, json={"login": "alexdev"}),
    "gitlab.com": httpx.Response(200, json=[{"username": "alexdev"}]),
    "codeberg.org": httpx.Response(200, json={"login": "alexdev"}),
    "hacker-news.firebaseio.com": httpx.Response(200, json={"id": "alexdev", "karma": 1}),
    "hub.docker.com": httpx.Response(200, json={"username": "alexdev"}),
    "registry.npmjs.org": httpx.Response(200, json={"name": "alexdev"}),
    "pypi.org": httpx.Response(200, text="<html>alexdev</html>"),
    "dev.to": httpx.Response(200, json={"username": "alexdev"}),
    "lobste.rs": httpx.Response(200, json={"username": "alexdev"}),
    "keybase.io": httpx.Response(200, json={"status": {"code": 0}, "them": [{"id": "1"}]}),
    "api.chess.com": httpx.Response(200, json={"username": "alexdev"}),
    "lichess.org": httpx.Response(200, json={"id": "alexdev"}),
}
# ...and for one that does not.
MISSING: dict[str, httpx.Response] = {
    "gitlab.com": httpx.Response(200, json=[]),
    "hacker-news.firebaseio.com": httpx.Response(200, content=b"null"),
    "keybase.io": httpx.Response(200, json={"status": {"code": 0}, "them": [None]}),
}


class FakePlatforms:
    """``alexdev`` exists on GitHub, GitLab, Hacker News and Keybase; the rest say it does not,
    except as ``replies`` says (by host)."""

    def __init__(
        self, replies: dict[str, httpx.Response] | None = None, found: set[str] | None = None
    ) -> None:
        self.found = (
            {"api.github.com", "gitlab.com", "hacker-news.firebaseio.com", "keybase.io"}
            if found is None
            else found
        )
        self.replies = replies or {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        assert host in HOSTS, host
        assert request.url.scheme == "https"
        if host in self.replies:
            return self.replies[host]
        if host in self.found:
            return EXISTS[host]
        return MISSING.get(host, httpx.Response(404, json={"error": "not found"}))


async def run_shuttle(
    fake: FakePlatforms, value: str = "alexdev"
) -> tuple[list[Finding], ShuttleContext]:
    ctx = ShuttleContext(
        META, http=create_http_client(META, transport=httpx.MockTransport(fake.handler))
    )
    entity = entity_input(USERNAME, value)
    found = await collect(UsernamePresenceShuttle(), entity, ctx=ctx)
    assert_declared(META, found, entity)
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    return found, ctx


def profiles(found: list[Finding]) -> dict[str, dict[str, Any]]:
    return {f.value: dict(f.properties or {}) for f in found if isinstance(f, EntityFinding)}


def test_meta_and_platforms() -> None:
    assert META.name == "username-presence"
    assert META.input_types == {USERNAME}
    assert META.sensitivity is Sensitivity.PERSON and META.pivots is False
    assert (META.default_admiralty, META.default_confidence) == LEAD
    assert ShuttleRegistry.discover().get("username-presence") is UsernamePresenceShuttle
    assert len(PLATFORMS) == len(HOSTS) == 12
    # Names never carry URL syntax into a check.
    for platform in PLATFORMS:
        for bad in ("a/b", "a?b", "a#b", "a%2fb", "..", "a b", ""):
            assert not platform.accepts(bad), (platform.name, bad)


def test_answer_kinds() -> None:
    gitlab, hn, keybase, github = (
        BY_NAME[n] for n in ("GitLab", "Hacker News", "Keybase", "GitHub")
    )
    assert gitlab.read(200, b"[]") is Answer.MISSING
    assert gitlab.read(200, b'[{"id": 1}]') is Answer.FOUND
    assert gitlab.read(200, b'{"message": "x"}') is Answer.UNCLEAR
    assert hn.read(200, b"null") is Answer.MISSING
    assert hn.read(200, b'{"id": "x"}') is Answer.FOUND
    assert hn.read(200, b"<html>") is Answer.UNCLEAR
    assert keybase.read(200, b'{"them": [null]}') is Answer.MISSING
    assert keybase.read(200, b'{"them": [{"id": "1"}]}') is Answer.FOUND
    assert keybase.read(200, b'{"status": {"code": 205}}') is Answer.UNCLEAR
    for status, answer in (
        (200, Answer.FOUND),
        (404, Answer.MISSING),
        (403, Answer.UNCLEAR),
        (301, Answer.UNCLEAR),
        (429, Answer.UNCLEAR),
    ):
        assert github.read(status, b"") is answer


async def test_found_on_some_platforms() -> None:
    fake = FakePlatforms()
    found, ctx = await run_shuttle(fake)
    assert len(fake.requests) == len(PLATFORMS)
    assert profiles(found) == {
        "https://github.com/alexdev": {
            "platform": "GitHub",
            "profile_url": "https://github.com/alexdev",
        },
        "https://gitlab.com/alexdev": {
            "platform": "GitLab",
            "profile_url": "https://gitlab.com/alexdev",
        },
        "https://news.ycombinator.com/user?id=alexdev": {
            "platform": "Hacker News",
            "profile_url": "https://news.ycombinator.com/user?id=alexdev",
        },
        "https://keybase.io/alexdev": {
            "platform": "Keybase",
            "profile_url": "https://keybase.io/alexdev",
        },
    }
    for finding in found:
        assert (finding.admiralty, finding.confidence) == (None, None)  # C4/0.3 by default
        if isinstance(finding, RelationshipFinding):
            assert finding.type == RelationType.HAS_PROFILE and finding.source.value == "alexdev"
    assert NOTE_LEAD in ctx.notes
    assert (
        "Found the name on 4 of the 12 platforms asked: GitHub, GitLab, Hacker News, Keybase."
        in ctx.notes
    )


async def test_names_are_only_sent_where_they_fit() -> None:
    fake = FakePlatforms(found=set())
    found, ctx = await run_shuttle(fake, "Alex.Dev")
    hosts = {request.url.host for request in fake.requests}
    # GitHub, Hacker News, Docker Hub, npm, DEV, Lobsters, Keybase, Chess.com and Lichess
    # do not allow this name.
    assert hosts == {"gitlab.com", "codeberg.org", "pypi.org"}
    assert found == []
    assert "Not asked: 9 platform(s) whose names cannot look like this one." in ctx.notes
    assert NOTE_NOT_FOUND.format(asked=3) in ctx.notes


async def test_unclear_answers_and_redirects() -> None:
    fake = FakePlatforms(
        replies={
            "api.github.com": httpx.Response(403, json={"message": "rate limited"}),
            "pypi.org": httpx.Response(
                302, headers={"Location": "https://pypi.org/account/login/"}
            ),
            "lichess.org": httpx.Response(429),
        }
    )
    found, ctx = await run_shuttle(fake)
    # The redirect was not followed: one request to pypi.org only.
    assert [r.url.host for r in fake.requests].count("pypi.org") == 1
    assert "https://github.com/alexdev" not in profiles(found)
    assert "https://pypi.org/user/alexdev/" not in profiles(found)
    assert "3 platform(s) did not answer clearly: GitHub, PyPI, Lichess." in ctx.notes


async def test_nothing_answers_clearly() -> None:
    fake = FakePlatforms(replies={host: httpx.Response(503) for host in HOSTS})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake)
    assert info.value.reason is FailureReason.SOURCE_UNAVAILABLE


async def test_cancellation() -> None:
    cancelled = asyncio.Event()
    cancelled.set()
    fake = FakePlatforms()
    ctx = ShuttleContext(
        META,
        http=create_http_client(META, transport=httpx.MockTransport(fake.handler)),
        cancel_event=cancelled,
    )
    with pytest.raises(JobCancelledError):
        await collect(UsernamePresenceShuttle(), entity_input(USERNAME, "alexdev"), ctx=ctx)
    assert fake.requests == []


@pytest.mark.network
async def test_real_platforms() -> None:
    """``octocat`` on the real platforms. Only with ``make test-network``."""
    ctx = ShuttleContext(META)
    found = await collect(UsernamePresenceShuttle(), entity_input(USERNAME, "octocat"), ctx=ctx)
    assert "https://github.com/octocat" in profiles(found), ctx.notes
