"""The Gravatar Shuttle against a fake Gravatar: no network, fictional profile."""

import hashlib
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
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_gravatar import GravatarShuttle
from nightloom_shuttle_gravatar.shuttle import (
    BASE_URL,
    MAX_ACCOUNTS,
    NOTE_CAPPED,
    NOTE_NONE,
    NOTE_SKIPPED,
    VERIFIED,
    profile_hash,
)

EMAIL = EntityType.EMAIL
PROFILE = EntityType.SOCIAL_PROFILE
R = RelationType
META = GravatarShuttle.meta
ADDRESS = "alex@example.org"
KEY = hashlib.sha256(ADDRESS.encode()).hexdigest()

PROFILE_BODY = {
    "hash": KEY,
    "display_name": "Alex Example",
    "profile_url": "https://gravatar.com/alexexample",
    "avatar_url": "https://0.gravatar.com/avatar/" + KEY,
    "avatar_alt_text": "A photo of Alex",
    "location": "Exampleton, Exampleland",
    "description": "I like widgets and long walks.",
    "job_title": "Engineer",
    "company": "Example Org",
    "pronouns": "they/them",
    "pronunciation": "AL-ex",
    "links": [{"label": "Blog", "url": "https://unverified.example.org"}],
    "verified_accounts": [
        {
            "service_type": "github",
            "service_label": "GitHub",
            "url": "https://github.com/alex-dev",
            "is_hidden": False,
        },
        {
            "service_type": "mastodon",
            "service_label": "Mastodon",
            "url": "https://social.example.org/@alex",
            "is_hidden": False,
        },
        {
            "service_type": "x",
            "service_label": "X",
            "url": "https://x.com/alex_hidden",
            "is_hidden": True,
        },
        {"service_type": "bad", "service_label": "Bad", "url": "javascript:alert(1)"},
    ],
}


class FakeGravatar:
    def __init__(self, reply: Any = None) -> None:
        self.replies = [reply] if not isinstance(reply, list) else reply
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert str(request.url).startswith(BASE_URL)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if reply is None:
            return (
                httpx.Response(200, json=PROFILE_BODY)
                if request.url.path.endswith(KEY)
                else httpx.Response(404)
            )
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)


async def run_shuttle(
    fake: FakeGravatar, address: str = ADDRESS
) -> tuple[list[Finding], ShuttleContext]:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    ctx = ShuttleContext(
        META, http=create_http_client(META, transport=httpx.MockTransport(fake.handler))
    )
    entity = entity_input(EMAIL, address)
    found = await collect(GravatarShuttle(sleep=sleep, clock=lambda: 0.0), entity, ctx=ctx)
    assert_declared(META, found, entity)
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    text = repr(found) + repr(ctx.notes)
    for absent in (
        "/avatar/",
        "A photo of",
        "Exampleton",
        "long walks",
        "they/them",
        "AL-ex",
        "unverified.example.org",
        "alex_hidden",
    ):
        assert absent not in text, absent
    return found, ctx


def test_meta_and_hash() -> None:
    assert META.name == "gravatar"
    assert META.input_types == {EMAIL}
    assert META.sensitivity is Sensitivity.PERSON and META.pivots is False
    assert profile_hash("  Alex@Example.org ") == KEY
    assert ShuttleRegistry.discover().get("gravatar") is GravatarShuttle


async def test_profile() -> None:
    fake = FakeGravatar()
    found, ctx = await run_shuttle(fake)
    (request,) = fake.requests
    assert request.url.path == f"/v3/profiles/{KEY}"  # only the hash is sent
    assert ADDRESS not in str(request.url)

    profiles = {
        f.value: (dict(f.properties or {}), f.admiralty)
        for f in found
        if isinstance(f, EntityFinding)
    }
    assert profiles["https://gravatar.com/alexexample"] == (
        {
            "platform": "Gravatar",
            "profile_url": "https://gravatar.com/alexexample",
            "display_name": "Alex Example",
            "job_title": "Engineer",
            "company": "Example Org",
        },
        None,
    )
    assert profiles["https://github.com/alex-dev"] == (
        {"platform": "GitHub", "profile_url": "https://github.com/alex-dev"},
        VERIFIED[0],
    )
    assert set(profiles) == {
        "https://gravatar.com/alexexample",
        "https://github.com/alex-dev",
        "https://social.example.org/@alex",
    }
    links = {
        (f.type, f.source.value, f.target.value)
        for f in found
        if isinstance(f, RelationshipFinding)
    }
    assert (R.HAS_PROFILE, ADDRESS, "https://gravatar.com/alexexample") in links
    assert (
        R.LINKED_ACCOUNT,
        "https://gravatar.com/alexexample",
        "https://github.com/alex-dev",
    ) in links
    assert NOTE_SKIPPED.format(count=2) in ctx.notes


async def test_no_profile_and_cap() -> None:
    found, ctx = await run_shuttle(FakeGravatar(), "nobody@example.org")
    assert (found, ctx.notes) == ([], (NOTE_NONE,))
    many = {
        **PROFILE_BODY,
        "verified_accounts": [
            {"service_label": "Site", "url": f"https://site{n}.example.org/alex"}
            for n in range(MAX_ACCOUNTS + 3)
        ],
    }
    found, ctx = await run_shuttle(FakeGravatar(many))
    assert (
        sum(isinstance(f, RelationshipFinding) and f.type == R.LINKED_ACCOUNT for f in found)
        == MAX_ACCOUNTS
    )
    assert NOTE_CAPPED.format(count=MAX_ACCOUNTS + 3, limit=MAX_ACCOUNTS) in ctx.notes


async def test_retries() -> None:
    fake = FakeGravatar(
        [httpx.Response(503), httpx.Response(429, headers={"Retry-After": "2"}), PROFILE_BODY]
    )
    found, _ = await run_shuttle(fake)
    assert len(fake.requests) == 3 and found


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (httpx.Response(400), FailureReason.SOURCE_INVALID_RESPONSE),
        ([1, 2], FailureReason.SOURCE_INVALID_RESPONSE),
        ({"display_name": "No URL"}, FailureReason.SOURCE_INVALID_RESPONSE),
        (httpx.Response(429, headers={"Retry-After": "3600"}), FailureReason.SOURCE_UNAVAILABLE),
        (httpx.Response(502), FailureReason.SOURCE_UNAVAILABLE),
    ],
)
async def test_errors(reply: Any, reason: FailureReason) -> None:
    fake = FakeGravatar([reply] if isinstance(reply, list) else reply)
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake)
    assert info.value.reason is reason


@pytest.mark.network
async def test_real_gravatar() -> None:
    """A lookup against the real API. Only with ``make test-network``."""
    ctx = ShuttleContext(META)
    await collect(GravatarShuttle(), entity_input(EMAIL, "nobody-at-all@example.org"), ctx=ctx)
    assert ctx.notes == (NOTE_NONE,)
