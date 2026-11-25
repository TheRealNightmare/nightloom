"""The Have I Been Pwned Shuttles against a fake HIBP: no network.

The breaches are fictional, in HIBP's API v3 shape: ExampleShop (verified,
of example.com), ExampleForum (unverified), ExampleLeak (fabricated), a
sensitive ExampleDating, a retired ExampleOld, and a malformed item.
"""

import asyncio
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import unquote

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
from nightloom_shuttle_hibp import HibpAccountShuttle, HibpBreachesShuttle
from nightloom_shuttle_hibp.client import BASE_URL, HOST, KEY_HEADER
from nightloom_shuttle_hibp.config import KEY_VARIABLE, load_api_key
from nightloom_shuttle_hibp.shuttles import (
    FABRICATED,
    NOTE_KEY_REFUSED,
    NOTE_NO_KEY,
    NOTE_NO_SITE_BREACH,
    NOTE_NOT_PWNED,
    NOTE_SITE_LOOKUP,
    UNVERIFIED,
)

DOMAIN = EntityType.DOMAIN
EMAIL = EntityType.EMAIL
BREACH = EntityType.BREACH
R = RelationType
KEY = "0123456789abcdef0123456789abcdef"
ADDRESS = "alex@example.org"


def breach(name: str, domain: str, **flags: Any) -> dict[str, Any]:
    return {
        "Name": name,
        "Title": name.replace("Example", "Example "),
        "Domain": domain,
        "BreachDate": "2024-03-01",
        "AddedDate": "2024-05-02T10:00:00Z",
        "ModifiedDate": "2024-05-02T10:00:00Z",
        "PwnCount": 1200,
        "Description": "<p>In March 2024, <a href='https://example.com'>it</a> was breached.</p>",
        "LogoPath": "https://haveibeenpwned.com/Content/Images/PwnedLogos/List.png",
        "DataClasses": ["Email addresses", "Passwords", "Email addresses"],
        "IsVerified": flags.get("verified", True),
        "IsFabricated": flags.get("fabricated", False),
        "IsSensitive": flags.get("sensitive", False),
        "IsRetired": flags.get("retired", False),
        "IsSpamList": False,
        "IsMalware": False,
        "IsSubscriptionFree": False,
        "IsStealerLog": False,
    }


SHOP = breach("ExampleShop", "example.com")
SITE_BREACHES = [
    SHOP,
    breach("ExampleOld", "example.com", retired=True),
    breach("ExampleShopTwo", "other.example.net"),  # not this site: ignored
]
ACCOUNT_BREACHES = [
    SHOP,
    breach("ExampleForum", "forum.example.net", verified=False),
    breach("ExampleLeak", "", fabricated=True),
    breach("ExampleDating", "dating.example.org", sensitive=True),
    breach("ExampleOld", "example.com", retired=True),
    {"Title": "no name"},
]

Reply = list[Any] | dict[str, Any] | bytes | httpx.Response


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeHibp:
    """Answers by path below ``/api/v3/`` (site lookups: ``breaches?domain=<d>``)."""

    def __init__(self, routes: dict[str, Reply | list[Reply]] | None = None) -> None:
        self.routes: dict[str, Reply | list[Reply]] = {
            "breaches?domain=example.com": SITE_BREACHES,
            f"breachedaccount/{ADDRESS}": ACCOUNT_BREACHES,
        }
        self.routes.update(routes or {})
        self.requests: list[httpx.Request] = []

    @staticmethod
    def route(request: httpx.Request) -> str:
        path = unquote(request.url.raw_path.decode().split("?")[0]).removeprefix("/api/v3/")
        domain = request.url.params.get("domain")
        return f"{path}?domain={domain}" if domain is not None else path

    @property
    def calls(self) -> list[str]:
        return [self.route(request) for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert str(request.url).startswith(BASE_URL)
        assert request.url.host == HOST
        assert request.headers["User-Agent"].startswith("Nightloom/")
        reply = self.routes.get(self.route(request))
        if reply is None:
            return httpx.Response(404 if "breachedaccount" in request.url.path else 200, json=[])
        if isinstance(reply, list) and reply and isinstance(reply[0], httpx.Response):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)


async def run_shuttle(
    shuttle_class: type[HibpBreachesShuttle] | type[HibpAccountShuttle],
    fake: FakeHibp,
    entity: EntityInput,
    *,
    api_key: str | None = KEY,
    cancel_event: asyncio.Event | None = None,
) -> tuple[list[Finding], ShuttleContext, FakeTime]:
    time = FakeTime()
    meta = shuttle_class.meta
    assert meta.rate_limit is not None
    limiter = HostRateLimiter(meta.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(meta, transport=httpx.MockTransport(fake.handler), limiter=limiter)
    ctx = ShuttleContext(meta, http=http, cancel_event=cancel_event)
    shuttle = shuttle_class(sleep=time.sleep, clock=time, api_key=api_key)
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(meta, found, entity)
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    text = repr(found) + repr(ctx.notes)
    assert "was breached" not in text and "PwnedLogos" not in text  # no description, no logo
    assert KEY not in text
    return found, ctx, time


def entities(found: list[Finding], entity_type: EntityType) -> dict[str, dict[str, Any]]:
    return {
        f.value: dict(f.properties or {})
        for f in found
        if isinstance(f, EntityFinding) and f.type == entity_type
    }


def edges(found: Iterable[Finding], relation: RelationType) -> set[tuple[str, str]]:
    return {
        (f.source.value, f.target.value)
        for f in found
        if isinstance(f, RelationshipFinding) and f.type == relation
    }


SHOP_PROPERTIES = {
    "title": "Example Shop",
    "breach_date": date(2024, 3, 1),
    "added_at": datetime(2024, 5, 2, 10, 0, tzinfo=UTC),
    "record_count": 1200,
    "data_classes": ["Email addresses", "Passwords"],
    "verified": True,
    "fabricated": False,
    "spam_list": False,
    "malware": False,
}


# --- metadata -----------------------------------------------------------------------------


def test_meta_and_registration() -> None:
    site, account = HibpBreachesShuttle.meta, HibpAccountShuttle.meta
    assert (site.name, account.name) == ("hibp-breaches", "hibp-account")
    assert site.input_types == {DOMAIN} and account.input_types == {EMAIL}
    assert site.sensitivity is Sensitivity.ORGANIZATION
    assert account.sensitivity is Sensitivity.PERSON
    assert site.output_relationship_types == {R.BREACH_OF, R.HAS_SUBDOMAIN}
    assert account.output_relationship_types == {R.EXPOSED_IN, R.BREACH_OF}
    for meta in (site, account):
        assert meta.pivots is False and meta.network is True
        assert meta.cache_ttl_seconds == 24 * 3600
        assert (meta.default_admiralty, meta.default_confidence) == ("A2", 0.9)
        assert meta.usage_note is not None and "CC BY 4.0" in meta.usage_note
        assert meta.required_keys == frozenset()  # the key is the operator's (environment)
    assert account.rate_limit is not None and account.rate_limit.per_second == pytest.approx(1 / 6)
    registry = ShuttleRegistry.discover()
    assert registry.get("hibp-breaches") is HibpBreachesShuttle
    assert registry.get("hibp-account") is HibpAccountShuttle


def test_api_key_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_VARIABLE, raising=False)
    assert load_api_key() is None
    HibpAccountShuttle.check_configuration()  # no key is fine until a run
    monkeypatch.setenv(KEY_VARIABLE, f"  {KEY.upper()} ")
    assert load_api_key() == KEY.upper()
    monkeypatch.setenv(KEY_VARIABLE, "not-a-key")
    with pytest.raises(ShuttleConfigurationError) as info:
        HibpAccountShuttle.check_configuration()
    assert "not-a-key" not in str(info.value)


# --- breaches of a site -------------------------------------------------------------------


async def test_site_breaches() -> None:
    fake = FakeHibp()
    found, ctx, _ = await run_shuttle(
        HibpBreachesShuttle, fake, entity_input(DOMAIN, "example.com")
    )
    assert fake.calls == ["breaches?domain=example.com"]
    assert KEY_HEADER not in fake.requests[0].headers  # the free list needs no key
    assert entities(found, BREACH) == {"ExampleShop": SHOP_PROPERTIES}
    assert edges(found, R.BREACH_OF) == {("ExampleShop", "example.com")}
    assert "Left out 1 retired breach(es)." in ctx.notes


async def test_subdomain_asks_about_the_site() -> None:
    fake = FakeHibp()
    found, ctx, _ = await run_shuttle(
        HibpBreachesShuttle, fake, entity_input(DOMAIN, "shop.example.com")
    )
    assert fake.calls == ["breaches?domain=example.com"]
    assert NOTE_SITE_LOOKUP.format(domain="example.com") in ctx.notes
    assert edges(found, R.HAS_SUBDOMAIN) == {("example.com", "shop.example.com")}
    assert edges(found, R.BREACH_OF) == {("ExampleShop", "example.com")}


async def test_site_without_breaches() -> None:
    found, ctx, _ = await run_shuttle(
        HibpBreachesShuttle, FakeHibp(), entity_input(DOMAIN, "example.org")
    )
    assert found == []
    assert ctx.notes == (NOTE_NO_SITE_BREACH,)


# --- breaches of an address ---------------------------------------------------------------


async def test_account_breaches() -> None:
    fake = FakeHibp()
    found, ctx, _ = await run_shuttle(HibpAccountShuttle, fake, entity_input(EMAIL, ADDRESS))
    (request,) = fake.requests
    assert request.headers[KEY_HEADER] == KEY
    assert request.url.params["truncateResponse"] == "false"
    assert b"alex%40example.org" in request.url.raw_path  # one encoded path segment

    breaches = entities(found, BREACH)
    assert breaches["ExampleShop"] == SHOP_PROPERTIES
    # Sensitive, retired and malformed breaches are left out and counted.
    assert set(breaches) == {"ExampleShop", "ExampleForum", "ExampleLeak"}
    assert "Left out 1 sensitive breach(es): being in one can be harmful to reveal." in ctx.notes
    assert "Left out 1 retired breach(es)." in ctx.notes
    assert "Ignored 1 malformed breach(es) in HIBP's answer." in ctx.notes
    assert "ExampleDating" not in repr(found) + repr(ctx.notes)

    assert edges(found, R.EXPOSED_IN) == {
        (ADDRESS, "ExampleShop"),
        (ADDRESS, "ExampleForum"),
        (ADDRESS, "ExampleLeak"),
    }
    # The breached sites (ExampleLeak has none).
    assert edges(found, R.BREACH_OF) == {
        ("ExampleShop", "example.com"),
        ("ExampleForum", "forum.example.net"),
    }
    ratings: dict[tuple[object, str], tuple[str | None, float | None]] = {}
    for f in found:
        if isinstance(f, EntityFinding):
            ratings[(f.type, f.value)] = (f.admiralty, f.confidence)
        elif isinstance(f, RelationshipFinding):
            ratings[(f.type, f.target.value)] = (f.admiralty, f.confidence)
    assert ratings[(BREACH, "ExampleShop")] == (None, None)  # A2/0.9 by default
    assert ratings[(BREACH, "ExampleForum")] == UNVERIFIED
    assert ratings[(BREACH, "ExampleLeak")] == FABRICATED
    assert ratings[(R.EXPOSED_IN, "ExampleForum")] == UNVERIFIED


async def test_account_in_no_breach() -> None:
    found, ctx, _ = await run_shuttle(
        HibpAccountShuttle, FakeHibp(), entity_input(EMAIL, "nobody@example.org")
    )
    assert (found, ctx.notes) == ([], (NOTE_NOT_PWNED,))


async def test_account_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_VARIABLE, raising=False)
    fake = FakeHibp()
    meta = HibpAccountShuttle.meta
    ctx = ShuttleContext(
        meta, http=create_http_client(meta, transport=httpx.MockTransport(fake.handler))
    )
    with pytest.raises(ShuttleFailedError) as info:
        await collect(HibpAccountShuttle(), entity_input(EMAIL, ADDRESS), ctx=ctx)
    assert info.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert ctx.notes == (NOTE_NO_KEY,)
    assert fake.requests == []


async def test_account_key_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_VARIABLE, KEY)
    fake = FakeHibp()
    found, _, _ = await run_shuttle(
        HibpAccountShuttle, fake, entity_input(EMAIL, ADDRESS), api_key=None
    )
    assert fake.requests[0].headers[KEY_HEADER] == KEY
    assert "ExampleShop" in entities(found, BREACH)


async def test_refused_key() -> None:
    fake = FakeHibp({f"breachedaccount/{ADDRESS}": httpx.Response(401, json={"message": KEY})})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(HibpAccountShuttle, fake, entity_input(EMAIL, ADDRESS))
    assert info.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert KEY not in str(info.value) and KEY not in repr(info.value.__cause__)


async def test_refused_key_note() -> None:
    fake = FakeHibp({f"breachedaccount/{ADDRESS}": httpx.Response(401)})
    meta = HibpAccountShuttle.meta
    ctx = ShuttleContext(
        meta, http=create_http_client(meta, transport=httpx.MockTransport(fake.handler))
    )
    with pytest.raises(ShuttleFailedError):
        await collect(HibpAccountShuttle(api_key=KEY), entity_input(EMAIL, ADDRESS), ctx=ctx)
    assert ctx.notes == (NOTE_KEY_REFUSED,)


# --- errors -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (httpx.Response(400), FailureReason.SOURCE_INVALID_RESPONSE),
        (httpx.Response(403), FailureReason.SOURCE_INVALID_RESPONSE),
        (b"<html>no</html>", FailureReason.SOURCE_INVALID_RESPONSE),
        ({"Name": "not a list"}, FailureReason.SOURCE_INVALID_RESPONSE),
        (httpx.Response(503), FailureReason.SOURCE_UNAVAILABLE),
    ],
)
async def test_bad_answers_fail_the_job(reply: Reply, reason: FailureReason) -> None:
    fake = FakeHibp({"breaches?domain=example.com": reply})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(HibpBreachesShuttle, fake, entity_input(DOMAIN, "example.com"))
    assert info.value.reason is reason


async def test_retries_and_rate_limits() -> None:
    fake = FakeHibp(
        {
            f"breachedaccount/{ADDRESS}": [
                httpx.Response(502),
                httpx.Response(429, headers={"Retry-After": "4"}),
                httpx.Response(200, json=[SHOP]),
            ]
        }
    )
    found, ctx, time = await run_shuttle(HibpAccountShuttle, fake, entity_input(EMAIL, ADDRESS))
    assert set(entities(found, BREACH)) == {"ExampleShop"}
    assert 2.0 in time.sleeps and 4.0 in time.sleeps
    assert ctx.last_progress is not None and "1 retried, 1 rate-limited" in ctx.last_progress


async def test_too_long_retry_after_gives_up() -> None:
    fake = FakeHibp(
        {"breaches?domain=example.com": httpx.Response(429, headers={"Retry-After": "120"})}
    )
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(HibpBreachesShuttle, fake, entity_input(DOMAIN, "example.com"))
    assert info.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert len(fake.requests) == 1


async def test_cancellation_stops_before_a_request() -> None:
    cancelled = asyncio.Event()
    cancelled.set()
    fake = FakeHibp()
    with pytest.raises(JobCancelledError):
        await run_shuttle(
            HibpAccountShuttle, fake, entity_input(EMAIL, ADDRESS), cancel_event=cancelled
        )
    assert fake.requests == []


# --- the real HIBP ------------------------------------------------------------------------


@pytest.mark.network
async def test_real_public_breach_list() -> None:
    """Adobe's breach, from HIBP's real public list. Only with ``make test-network``."""
    ctx = ShuttleContext(HibpBreachesShuttle.meta)
    found = await collect(HibpBreachesShuttle(), entity_input(DOMAIN, "adobe.com"), ctx=ctx)
    adobe = entities(found, BREACH)["Adobe"]
    assert adobe["breach_date"] == date(2013, 10, 4), ctx.notes
    assert adobe["record_count"] > 100_000_000
