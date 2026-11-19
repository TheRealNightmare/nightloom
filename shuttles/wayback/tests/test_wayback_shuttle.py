"""The Wayback Shuttle against a fake CDX server: no network.

The answers use the CDX server's real JSON shape with fictional captures of
``example.org`` only (``fixtures/``). The fake tells the three queries apart
by their parameters: the domain-wide listing (``matchType=domain``), the
home page's latest capture (``fastLatest``) and its first capture.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    InputRefusedError,
    JobCancelledError,
    Mode,
    RefusalReason,
    RelationshipFinding,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.http import DestinationNotAllowedError, create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_wayback import WaybackShuttle
from nightloom_shuttle_wayback import shuttle as shuttle_module
from nightloom_shuttle_wayback.client import CDX_URL, HOST, retry_after_seconds
from nightloom_shuttle_wayback.filtering import FIELDS
from nightloom_shuttle_wayback.shuttle import (
    MAX_SUBDOMAINS,
    MAX_URLS,
    NOTE_DROPPED,
    NOTE_LISTING_LIMIT,
    NOTE_MALFORMED,
    NOTE_NO_CAPTURES,
    NOTE_SECRETS,
    NOTE_SMALLER_LISTING,
    NOTE_STRIPPED,
    NOTE_SUBDOMAINS_CAPPED,
    NOTE_URLS_CAPPED,
    REQUEST_BUDGET_SECONDS,
    SMALLER_LISTING_LIMIT,
    USAGE_NOTE,
)

D = EntityType.DOMAIN
U = EntityType.URL
R = RelationType
META = WaybackShuttle.meta
FIXTURES = Path(__file__).parent / "fixtures"
HEADER = list(FIELDS)
DOMAIN = "example.org"


def when(text: str) -> datetime:
    return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def fixture(name: str) -> list[Any]:
    data: list[Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def ts(minutes: int) -> str:
    """A CDX timestamp ``minutes`` after the start of 2020."""
    return f"{datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes):%Y%m%d%H%M%S}"


def rows(*captures: list[str]) -> list[Any]:
    return [HEADER, *captures]


FIRST = rows(["20030101000000", "http://example.org:80/", "200", "text/html"])
LATEST = rows(["20261101000000", "http://example.org/", "200", "text/html"])

Reply = list[Any] | bytes | httpx.Response


class Seq:
    """Replies used in turn; the last one repeats."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)

    def next(self) -> Reply:
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class FakeTime:
    """Fake monotonic time: sleeping (backoff, rate limit, Retry-After) moves it forward."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeCdx:
    """Answers the three queries.

    Every request must go to the CDX endpoint on web.archive.org over https;
    a request anywhere else (the domain looked up, above all) fails the test.
    """

    def __init__(
        self,
        *,
        listing: Reply | Seq | None = None,
        first: Reply | Seq | None = None,
        latest: Reply | Seq | None = None,
    ) -> None:
        self.replies: dict[str, Reply | Seq] = {
            "listing": fixture("example_org") if listing is None else listing,
            "first": FIRST if first is None else first,
            "latest": LATEST if latest is None else latest,
        }
        self.requests: list[httpx.Request] = []

    @staticmethod
    def kind(request: httpx.Request) -> str:
        params = request.url.params
        if params.get("matchType") == "domain":
            return "listing"
        return "latest" if params.get("fastLatest") == "true" else "first"

    @property
    def calls(self) -> list[str]:
        return [self.kind(request) for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == HOST, f"request to {request.url.host}"
        assert request.url.path == "/cdx/search/cdx"
        reply = self.replies[self.kind(request)]
        if isinstance(reply, Seq):
            reply = reply.next()
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)


def fake_http(
    handler: Callable[[httpx.Request], httpx.Response], time: FakeTime
) -> httpx.AsyncClient:
    """The guarded client over ``handler``, rate-limited on fake time."""
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    return create_http_client(META, transport=httpx.MockTransport(handler), limiter=limiter)


async def run_shuttle(
    fake: FakeCdx,
    entity: EntityInput | None = None,
    *,
    time: FakeTime | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[list[Finding], ShuttleContext, FakeTime]:
    entity = entity or entity_input(D, DOMAIN)
    time = time or FakeTime()
    ctx = ShuttleContext(META, http=fake_http(fake.handler, time), cancel_event=cancel_event)
    shuttle = WaybackShuttle(sleep=time.sleep, clock=time)
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, found, entity)
    assert_valid(found)
    assert_only_the_archive(fake)
    return found, ctx, time


def assert_valid(found: list[Finding]) -> None:
    """Every entity finding is valid as the worker would store it (value and properties)."""
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)


def assert_only_the_archive(fake: FakeCdx) -> None:
    for request in fake.requests:
        assert str(request.url).startswith(CDX_URL + "?")
        assert request.url.host == HOST
        assert request.url.port in (None, 443)


def entities(found: list[Finding], entity_type: EntityType) -> list[EntityFinding]:
    return [f for f in found if isinstance(f, EntityFinding) and f.type == entity_type]


def relationships(found: list[Finding], relation: RelationType) -> list[RelationshipFinding]:
    return [f for f in found if isinstance(f, RelationshipFinding) and f.type == relation]


def edges(found: list[Finding], relation: RelationType) -> set[tuple[str, str]]:
    return {(f.source.value, f.target.value) for f in relationships(found, relation)}


def urls(found: list[Finding]) -> dict[str, dict[str, Any]]:
    return {f.value: dict(f.properties or {}) for f in entities(found, U)}


def domain_properties(found: list[Finding], value: str = DOMAIN) -> dict[str, Any]:
    (domain,) = [f for f in entities(found, D) if f.value == value]
    return dict(domain.properties or {})


def serialized(found: list[Finding], ctx: ShuttleContext) -> str:
    """Everything a run hands over: every finding (all fields) and every note."""
    parts = [finding.model_dump_json() for finding in found]
    parts.extend(ctx.notes)
    return "\n".join(parts)


def busy(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(status, headers=headers)


# --- metadata and registration ------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "wayback"
    assert META.title == "Archived URLs (Wayback Machine)"
    assert META.input_types == {D}
    assert META.output_entity_types == {U, D}
    assert META.output_relationship_types == {R.HOSTS, R.HAS_SUBDOMAIN}
    assert (META.mode, META.network) == (Mode.PASSIVE, True)
    assert META.sensitivity is Sensitivity.INFRASTRUCTURE
    assert META.rate_limit is not None and META.rate_limit.per_second == pytest.approx(1)
    assert META.timeout_seconds == 300
    assert (META.default_admiralty, META.default_confidence) == ("B2", 0.8)
    assert META.homepage == "https://web.archive.org"
    assert META.terms_url == "https://archive.org/about/terms"
    assert (
        META.usage_note
        == USAGE_NOTE
        == (
            "Data from the Internet Archive. Please keep requests modest; the archive is a "
            "non-profit service."
        )
    )
    assert META.required_keys == frozenset()
    registry = ShuttleRegistry.discover()
    assert registry.get("wayback") is WaybackShuttle
    assert WaybackShuttle in registry.list(D)
    assert WaybackShuttle not in registry.list(EntityType.IP_ADDRESS)
    WaybackShuttle.check_configuration()  # nothing to configure


# --- a full run ---------------------------------------------------------------------------


async def test_full_run() -> None:
    fake = FakeCdx()
    found, ctx, _ = await run_shuttle(fake)
    assert fake.calls == ["first", "latest", "listing"]

    listing = fake.requests[2].url.params
    assert dict(listing) == {
        "url": "example.org",
        "matchType": "domain",
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype",
        "collapse": "urlkey",
        "limit": "10000",
    }
    assert dict(fake.requests[0].url.params) == {
        "url": "example.org/",
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype",
        "limit": "1",
    }
    latest = fake.requests[1].url.params
    assert (latest["url"], latest["fastLatest"], latest["limit"]) == ("example.org/", "true", "-1")

    # The input comes first, with the archive's summary.
    assert found[0] == EntityFinding(
        type=D,
        value=DOMAIN,
        properties={
            "wayback_first_capture": when("20030101000000"),
            "wayback_last_capture": when("20261101000000"),
            "wayback_url_count": 7,
        },
    )

    # Most recently captured first.
    assert list(urls(found)) == [
        "http://example.org/",
        "https://api.example.org/v1/",
        "https://blog.example.org/post/2",
        "https://blog.example.org/post/1",
        "https://www.example.org/news",
        "http://example.org/login",
        "http://example.org/about",
    ]
    home = urls(found)["http://example.org/"]
    assert home == {
        "first_captured": when("20030101000000"),
        "last_captured": when("20261101000000"),  # the home page's latest is known
        "capture_count": 1,
        "last_status": 200,
        "mimetype": "text/html",
        "archive_url": "https://web.archive.org/web/20261101000000/http://example.org/",
    }
    revisit = urls(found)["https://api.example.org/v1/"]
    assert "last_status" not in revisit  # "-" in the archive
    assert revisit["mimetype"] == "warc/revisit"
    assert "last_captured" not in revisit  # the listing gives first captures only
    assert urls(found)["https://blog.example.org/post/1"]["last_status"] == 404

    # Subdomains seen, each once, from the input; HOSTS from each URL's own host.
    subdomains = [d.value for d in entities(found, D)][1:]
    assert subdomains == ["api.example.org", "blog.example.org", "www.example.org"]
    assert edges(found, R.HAS_SUBDOMAIN) == {(DOMAIN, s) for s in subdomains}
    assert edges(found, R.HOSTS) == {
        (DOMAIN, "http://example.org/"),
        (DOMAIN, "http://example.org/login"),
        (DOMAIN, "http://example.org/about"),
        ("api.example.org", "https://api.example.org/v1/"),
        ("blog.example.org", "https://blog.example.org/post/1"),
        ("blog.example.org", "https://blog.example.org/post/2"),
        ("www.example.org", "https://www.example.org/news"),
    }
    assert all(f.admiralty is None and f.confidence is None for f in found)  # B2, 0.8

    # Every entity comes before a relationship that uses it.
    seen: set[tuple[EntityType, str]] = set()
    for finding in found:
        if isinstance(finding, EntityFinding):
            seen.add((finding.type, finding.value))
        else:
            assert (finding.source.type, finding.source.value) in seen
            assert (finding.target.type, finding.target.value) in seen

    assert set(ctx.notes) == {
        NOTE_DROPPED.format(
            total=8,
            details="2 outside the domain, 3 static assets, 2 email in path, 1 invalid",
        ),
        NOTE_STRIPPED.format(count=5),
        NOTE_MALFORMED.format(count=2),
    }


async def test_scope_drops_lookalikes_and_addresses() -> None:
    listing = rows(
        ["20200101000000", "http://evilexample.org/", "200", "text/html"],
        ["20200101000000", "http://example.org.attacker.example/", "200", "text/html"],
        ["20200101000000", "http://notexample.org/x", "200", "text/html"],
        ["20200101000000", "http://192.0.2.1/", "200", "text/html"],
        ["20200101000000", "http://[2001:db8::1]/", "200", "text/html"],
        ["20200101000000", "http://Deep.Sub.EXAMPLE.org/Page", "200", "text/html"],
    )
    found, ctx, _ = await run_shuttle(FakeCdx(listing=listing, first=[], latest=[]))
    assert list(urls(found)) == ["http://deep.sub.example.org/Page"]  # host lower-cased only
    assert edges(found, R.HAS_SUBDOMAIN) == {(DOMAIN, "deep.sub.example.org")}
    assert NOTE_DROPPED.format(total=5, details="5 outside the domain") in ctx.notes
    assert "evilexample" not in serialized(found, ctx)


async def test_a_subdomain_input_keeps_only_names_under_it() -> None:
    found, _, _ = await run_shuttle(FakeCdx(), entity_input(D, "blog.example.org"))
    assert set(urls(found)) == {
        "https://blog.example.org/post/1",
        "https://blog.example.org/post/2",
    }
    assert edges(found, R.HOSTS) == {("blog.example.org", u) for u in urls(found)}
    assert relationships(found, R.HAS_SUBDOMAIN) == []


# --- privacy ------------------------------------------------------------------------------


async def test_query_values_and_emails_never_appear_anywhere() -> None:
    """Neither a query value nor an email address survives into a finding or a note."""
    fake = FakeCdx()
    found, ctx, _ = await run_shuttle(fake)
    login = urls(found)["http://example.org/login"]
    assert login["query_params"] == ["email", "token"]
    assert (
        login["archive_url"]
        == "https://web.archive.org/web/20160101000000/http://example.org/login"
    )
    text = serialized(found, ctx)
    for secret in ("SECRET", "a@b.example", "b.example", "token=", "email=", "?", "#", "@"):
        assert secret not in text, secret
    for value in ("alice", "bob", "%40", "jsessionid", "ABC123", "lang=", "ref=nav", "someone"):
        assert value not in text, value


async def test_email_in_path_is_dropped_plain_and_encoded() -> None:
    listing = rows(
        ["20200101000000", "http://example.org/a@b.example", "200", "text/html"],
        ["20200101000000", "http://example.org/u/a%40b.example", "200", "text/html"],
        ["20200101000000", "http://example.org/u/a%2540b.example/x", "200", "text/html"],
        ["20200101000000", "http://example.org/ok", "200", "text/html"],
    )
    found, ctx, _ = await run_shuttle(FakeCdx(listing=listing, first=[], latest=[]))
    assert list(urls(found)) == ["http://example.org/ok"]
    assert NOTE_DROPPED.format(total=3, details="3 email in path") in ctx.notes
    assert "b.example" not in serialized(found, ctx)


FAKE_TOKENS = (
    "SECRET2",
    "SECRET3",
    "sk_live_SECRET4",
    "AKIAFAKEFAKEFAKEFAKE",
    "AIzaSyFAKE" + "0" * 29,
    "ghp_" + "FAKE" * 9,
    "xoxb-0000000000-FAKEFAKE",
    "SK" + "0123456789abcdef" * 2,
    "eyJGQUtFIjoiMSJ9.eyJGQUtFIjoiMiJ9.FAKESIGNATURE",
    "MIIFAKE",
    "FAKE0000FAKE0000",
    "FAKEtoken0a1b2c3d4e5f6g7h8i9jKLMNOP",
    "pass",
    "a@b.example",
)
SLUG = "/blog/how-we-built-our-new-pipeline-2020"  # long, but words: kept


async def test_secrets_credentials_and_hidden_queries_never_appear_anywhere(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No fake token reaches a finding, a note or a log record; drops are counted only."""
    caplog.set_level(logging.DEBUG)
    listing = rows(
        ["20200101000000", "http://example.org/reset%3Ftoken=SECRET2", "200", "text/html"],
        ["20200101000001", "http://example.org/reset%253Femail=a%2540b.example", "200", "x/y"],
        ["20200101000002", "http://example.org/doc%23SECRET3", "200", "text/html"],
        ["20200101000003", "http://example.org/api/v1/keys/sk_live_SECRET4", "200", "text/html"],
        ["20200101000004", "http://example.org/k/AKIAFAKEFAKEFAKEFAKE", "200", "text/html"],
        ["20200101000005", f"http://example.org/k/{FAKE_TOKENS[4]}", "200", "text/html"],
        ["20200101000006", f"http://example.org/k/{FAKE_TOKENS[5]}", "200", "text/html"],
        ["20200101000007", "http://example.org/k/xoxb-0000000000-FAKEFAKE", "200", "text/html"],
        ["20200101000008", f"http://example.org/k/{FAKE_TOKENS[7]}", "200", "text/html"],
        ["20200101000009", f"http://example.org/s/{FAKE_TOKENS[8]}", "200", "text/html"],
        ["20200101000010", "http://example.org/-----BEGIN%20KEY-----MIIFAKE", "200", "text/html"],
        ["20200101000011", "http://example.org/token/FAKE0000FAKE0000", "200", "text/html"],
        ["20200101000012", f"http://example.org/d/{FAKE_TOKENS[11]}", "200", "text/html"],
        ["20200101000013", "http://user:pass@example.org/", "200", "text/html"],
        ["20200101000014", f"http://example.org{SLUG}", "200", "text/html"],
    )
    found, ctx, _ = await run_shuttle(FakeCdx(listing=listing, first=[], latest=[]))
    assert set(urls(found)) == {
        f"http://example.org{SLUG}",
        "http://example.org/doc",
        "http://example.org/reset",
    }
    assert urls(found)["http://example.org/reset"]["query_params"] == ["token"]
    assert (
        NOTE_DROPPED.format(
            total=12,
            details="1 credentials in URL, 1 email in path, 10 possible secret in path",
        )
        in ctx.notes
    )
    assert NOTE_SECRETS.format(count=10) in ctx.notes
    assert NOTE_SECRETS == (
        "{count:,} archived URL(s) looked like they contained secrets and were not stored."
    )
    text = serialized(found, ctx)
    logged = "\n".join(
        f"{record.getMessage()} {record.args!r} {record.exc_text or ''}"
        for record in caplog.records
    )
    for token in FAKE_TOKENS:
        assert token not in text, token
        assert token not in logged, token
    for encoded in ("%3F", "%253F", "%23", "%2540", "BEGIN", "user:"):
        assert encoded not in text, encoded


async def test_query_variants_become_one_url() -> None:
    listing = rows(
        ["20150101000000", "http://example.org/search?q=one", "200", "text/html"],
        ["20120101000000", "http://example.org:80/search?q=two&page=2", "200", "text/html"],
        ["20180101000000", "http://example.org/search?sort=asc#results", "500", "text/html"],
        ["20100101000000", "http://example.org/search", "200", "text/html"],
    )
    found, ctx, _ = await run_shuttle(FakeCdx(listing=listing, first=[], latest=[]))
    assert urls(found) == {
        "http://example.org/search": {
            "first_captured": when("20100101000000"),  # the earliest
            "capture_count": 4,
            "last_status": 500,  # of the latest capture seen
            "mimetype": "text/html",
            "archive_url": "https://web.archive.org/web/20180101000000/http://example.org/search",
            "query_params": ["page", "q", "sort"],
        }
    }
    assert NOTE_STRIPPED.format(count=3) in ctx.notes


async def test_static_assets_are_skipped() -> None:
    listing = rows(
        ["20200101000000", "http://example.org/a.CSS", "200", "text/css"],
        ["20200101000000", "http://example.org/b", "200", "image/svg+xml"],
        ["20200101000000", "http://example.org/c.mp4", "200", "text/html"],
        ["20200101000000", "http://example.org/d.zip?v=1", "200", "text/html"],
        ["20200101000000", "http://example.org/report.pdf", "200", "application/pdf"],
    )
    found, ctx, _ = await run_shuttle(FakeCdx(listing=listing, first=[], latest=[]))
    assert list(urls(found)) == ["http://example.org/report.pdf"]
    assert NOTE_DROPPED.format(total=4, details="4 static assets") in ctx.notes
    # Assets are still captures of the domain.
    assert domain_properties(found)["wayback_url_count"] == 1
    assert domain_properties(found)["wayback_first_capture"] == when("20200101000000")


# --- caps ---------------------------------------------------------------------------------


async def test_url_cap_keeps_the_most_recently_captured() -> None:
    listing = rows(
        *[
            [ts(i), f"http://example.org/page{i:04d}", "200", "text/html"]
            for i in range(MAX_URLS + 100)
        ]
    )
    found, ctx, _ = await run_shuttle(FakeCdx(listing=listing, first=[], latest=[]))
    kept = list(urls(found))
    assert len(kept) == MAX_URLS
    # The 100 oldest (page0000..page0099) are left out.
    assert kept[0] == f"http://example.org/page{MAX_URLS + 99:04d}"
    assert "http://example.org/page0099" not in kept and "http://example.org/page0100" in kept
    assert NOTE_URLS_CAPPED.format(found=MAX_URLS + 100, kept=MAX_URLS) in ctx.notes
    assert domain_properties(found)["wayback_url_count"] == MAX_URLS + 100
    assert len(found) == 1 + 2 * MAX_URLS < 5_000


async def test_subdomain_cap_keeps_every_url_on_a_kept_host() -> None:
    listing = rows(
        *[
            [ts(i), f"http://h{i:03d}.example.org/", "200", "text/html"]
            for i in range(MAX_SUBDOMAINS + 10)
        ]
    )
    found, ctx, _ = await run_shuttle(FakeCdx(listing=listing, first=[], latest=[]))
    subdomains = [d.value for d in entities(found, D)][1:]
    assert len(subdomains) == len(relationships(found, R.HAS_SUBDOMAIN)) == MAX_SUBDOMAINS
    hosts = {source for source, _ in edges(found, R.HOSTS)}
    assert hosts == set(subdomains)  # every URL's host is stored
    assert NOTE_SUBDOMAINS_CAPPED.format(count=10, limit=MAX_SUBDOMAINS) in ctx.notes
    assert len(found) <= 1 + 2 * MAX_SUBDOMAINS + 2 * MAX_URLS < 5_000


async def test_a_full_listing_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shuttle_module, "LISTING_LIMIT", 3)
    listing = rows(
        *[[f"2020010100000{i}", f"http://example.org/{i}", "200", "text/html"] for i in range(3)]
    )
    fake = FakeCdx(listing=listing)
    _, ctx, _ = await run_shuttle(fake)
    assert fake.requests[2].url.params["limit"] == "3"
    assert NOTE_LISTING_LIMIT.format(limit=3) in ctx.notes


async def test_a_too_large_listing_is_asked_again_for_fewer() -> None:
    huge = httpx.Response(200, content=b"[" + b" " * 6_000_000 + b"]")
    fake = FakeCdx(listing=Seq(huge, fixture("example_org")))
    found, ctx, _ = await run_shuttle(fake)
    assert fake.calls == ["first", "latest", "listing", "listing"]
    assert fake.requests[3].url.params["limit"] == str(SMALLER_LISTING_LIMIT)
    assert NOTE_SMALLER_LISTING in ctx.notes
    assert urls(found)

    fake = FakeCdx(listing=huge)
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake)
    assert failed.value.reason is FailureReason.RESULT_TOO_LARGE
    assert fake.calls == ["first", "latest", "listing", "listing"]


# --- first and last capture ---------------------------------------------------------------


async def test_first_and_last_capture_cover_the_whole_domain() -> None:
    listing = rows(
        ["19990101000000", "http://old.example.org/", "200", "text/html"],  # older than home
        ["20240101000000", "http://example.org/new", "200", "text/html"],
    )
    found, _, _ = await run_shuttle(FakeCdx(listing=listing))
    props = domain_properties(found)
    assert props["wayback_first_capture"] == when("19990101000000")
    assert props["wayback_last_capture"] == when("20261101000000")  # the home page's latest
    # The home page was not in the listing: it is added from its own captures.
    assert urls(found)["http://example.org/"]["first_captured"] == when("20030101000000")
    assert urls(found)["http://example.org/"]["capture_count"] == 1


async def test_no_captures_is_a_note_not_an_error() -> None:
    fake = FakeCdx(listing=[], first=b"", latest=b"  \n")
    found, ctx, _ = await run_shuttle(fake)
    assert found == [EntityFinding(type=D, value=DOMAIN, properties={"wayback_url_count": 0})]
    assert ctx.notes == (NOTE_NO_CAPTURES,)


# --- malformed rows and unusable answers --------------------------------------------------


async def test_malformed_rows_are_counted() -> None:
    listing = [
        HEADER,
        ["20200101000000", "http://example.org/ok", "200", "text/html"],
        ["2020", "http://example.org/a", "200", "text/html"],
        ["20201301000000", "http://example.org/b", "200", "text/html"],  # month 13
        ["20200101000000", "http://example.org/c", 200, "text/html"],
        ["20200101000000", "", "200", "text/html"],
        "not a row",
        None,
        ["20200101000000", "http://example.org/d"],
    ]
    found, ctx, _ = await run_shuttle(FakeCdx(listing=listing, first=[], latest=[]))
    assert list(urls(found)) == ["http://example.org/ok"]
    assert NOTE_MALFORMED.format(count=7) in ctx.notes


@pytest.mark.parametrize(
    "reply",
    [
        {"rows": []},
        b"<html>Wayback Machine</html>",
        httpx.Response(400, text="bad request"),
        httpx.Response(403),
    ],
)
async def test_unusable_answers_fail_at_once(reply: Reply) -> None:
    fake = FakeCdx(first=reply)
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake)
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE
    assert fake.calls == ["first"]


# --- an unwell archive --------------------------------------------------------------------


async def test_5xx_is_retried_twice_with_backoff() -> None:
    fake = FakeCdx(listing=Seq(busy(502), busy(503), fixture("example_org")))
    found, _, time = await run_shuttle(fake)
    assert urls(found)
    assert fake.calls == ["first", "latest", "listing", "listing", "listing"]
    assert [s for s in time.sleeps if s >= 5] == [5.0, 15.0]

    fake = FakeCdx(listing=Seq(busy(500), busy(504), busy(503)))
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert fake.calls.count("listing") == 3


async def test_timeouts_are_retried_then_fail_cleanly() -> None:
    calls: list[str] = []
    fake = FakeCdx()

    def slow(request: httpx.Request) -> httpx.Response:
        calls.append(FakeCdx.kind(request))
        if FakeCdx.kind(request) == "listing" and calls.count("listing") < 3:
            raise httpx.ReadTimeout("timed out", request=request)
        return fake.handler(request)

    time = FakeTime()
    ctx = ShuttleContext(META, http=fake_http(slow, time))
    found = await collect(
        WaybackShuttle(sleep=time.sleep, clock=time), entity_input(D, DOMAIN), ctx=ctx
    )
    assert urls(found) and calls.count("listing") == 3

    def always(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    ctx = ShuttleContext(META, http=fake_http(always, time))
    with pytest.raises(ShuttleFailedError) as failed:
        await collect(
            WaybackShuttle(sleep=time.sleep, clock=time), entity_input(D, DOMAIN), ctx=ctx
        )
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert "timed out" not in str(failed.value)


async def test_429_waits_for_retry_after() -> None:
    fake = FakeCdx(first=Seq(busy(429, "7"), busy(429), FIRST))
    found, _, time = await run_shuttle(fake)
    assert urls(found)
    assert fake.calls[:3] == ["first", "first", "first"]
    assert 7.0 in time.sleeps and 10.0 in time.sleeps  # Retry-After, then the default


@pytest.mark.parametrize(
    "replies",
    [
        [busy(429, "1"), busy(429, "1"), busy(429, "1")],  # a third wait is not allowed
        [busy(429, "61")],  # longer than 60 seconds: give up at once
    ],
)
async def test_429_gives_up(replies: list[Reply]) -> None:
    fake = FakeCdx(first=Seq(*replies))
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert len(fake.calls) == len(replies)


async def test_no_request_after_the_budget() -> None:
    time = FakeTime()
    fake = FakeCdx(listing=Seq(busy(503), fixture("example_org")))
    original = fake.handler

    def slow(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if FakeCdx.kind(request) == "listing":
            time.now += REQUEST_BUDGET_SECONDS  # the listing took all the time
        return response

    fake.handler = slow  # type: ignore[method-assign]
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, time=time)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert fake.calls.count("listing") == 1


async def test_a_refused_destination_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise DestinationNotAllowedError("refused")

    http = create_http_client(META, transport=httpx.MockTransport(handler))
    ctx = ShuttleContext(META, http=http)
    with pytest.raises(ShuttleFailedError) as failed:
        await collect(WaybackShuttle(sleep=FakeTime().sleep), entity_input(D, DOMAIN), ctx=ctx)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert calls == 1


# --- inputs, cancellation, clients --------------------------------------------------------


@pytest.mark.parametrize("value", ["co.uk", "printer.local", "example"])
async def test_public_suffixes_and_internal_names_are_refused(value: str) -> None:
    fake = FakeCdx()
    http = create_http_client(META, transport=httpx.MockTransport(fake.handler))
    ctx = ShuttleContext(META, http=http)
    entity = EntityInput(type=D, value=value, key=f"domain:{value}")
    with pytest.raises(InputRefusedError) as refused:
        await collect(WaybackShuttle(), entity, ctx=ctx)
    assert refused.value.reason is RefusalReason.NOT_REGISTRABLE
    assert fake.requests == []


async def test_cancellation_is_checked_before_requests() -> None:
    fake = FakeCdx()
    event = asyncio.Event()
    event.set()
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, cancel_event=event)
    assert fake.requests == []


async def test_the_shuttle_can_use_a_given_client() -> None:
    fake = FakeCdx()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        ctx = ShuttleContext(META)
        found = await collect(
            WaybackShuttle(http=http, sleep=FakeTime().sleep), entity_input(D, DOMAIN), ctx=ctx
        )
    assert urls(found)
    assert_only_the_archive(fake)


def test_retry_after_parsing() -> None:
    assert retry_after_seconds(None) is None
    assert retry_after_seconds(" 12 ") == 12.0
    assert retry_after_seconds("not a date") is None
    now = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
    assert retry_after_seconds("Thu, 19 Nov 2026 06:00:20 GMT", now=now) == 20.0
    assert retry_after_seconds("Thu, 19 Nov 2026 05:59:00 GMT", now=now) == 0.0
    assert retry_after_seconds("Thu, 19 Nov 2026 06:00:10 -0000", now=now) == 10.0


@pytest.mark.network
async def test_real_wayback_machine() -> None:
    """Asks the real Wayback Machine about example.org. `make test-network` only."""
    entity = entity_input(D, DOMAIN)
    ctx = ShuttleContext(META)
    found = await collect(WaybackShuttle(), entity, ctx=ctx)
    assert_declared(META, found, entity)
    assert_valid(found)
    props = domain_properties(found)
    assert props["wayback_first_capture"].year <= 2005, ctx.notes
    assert urls(found), ctx.notes
    assert "?" not in serialized(found, ctx)
