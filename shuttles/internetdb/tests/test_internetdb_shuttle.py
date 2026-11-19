"""The InternetDB Shuttle against a fake InternetDB: no network.

The records use InternetDB's real shape with fictional data only
(``fixtures/``), for the documentation addresses 192.0.2.10 and
198.51.100.7. Those are not public addresses, so the tests give the
Shuttle an address filter that also lets the documentation ranges through;
the default filter is tested on its own.
"""

import asyncio
import ipaddress
import json
from datetime import UTC, datetime
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
    JobCancelledError,
    Mode,
    RelationshipFinding,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.http import create_http_client, is_public_address
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_internetdb import InternetDBShuttle
from nightloom_shuttle_internetdb.client import BASE_URL, HOST, retry_after_seconds
from nightloom_shuttle_internetdb.shuttle import (
    HOSTNAME_NOTE,
    MAX_CPES,
    MAX_HOSTNAMES,
    MAX_PORTS,
    MAX_TAGS,
    MAX_VULNS,
    NOTE_CAPPED,
    NOTE_HOSTNAMES_CAPPED,
    NOTE_HOSTNAMES_DROPPED,
    NOTE_IPV6,
    NOTE_MALFORMED,
    NOTE_NO_INFORMATION,
    NOTE_NOT_CVE,
    NOTE_NOT_PUBLIC,
    NOTE_PORTS_CAPPED,
    REQUEST_BUDGET_SECONDS,
    USAGE_NOTE,
)

IP = EntityType.IP_ADDRESS
SERVICE = EntityType.SERVICE
DOMAIN = EntityType.DOMAIN
R = RelationType
META = InternetDBShuttle.meta
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
DOCUMENTATION = [
    ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
]


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def record(ip: str = "192.0.2.10", **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ip": ip,
        "ports": [],
        "hostnames": [],
        "cpes": [],
        "vulns": [],
        "tags": [],
    }
    base.update(fields)
    return base


def public_or_documentation(address: ipaddress.IPv4Address) -> bool:
    """The production filter, widened to the documentation ranges the fixtures use."""
    return is_public_address(address) or any(address in net for net in DOCUMENTATION)


Reply = dict[str, Any] | bytes | httpx.Response


def default_routes() -> dict[str, Reply]:
    return {
        "192.0.2.10": fixture("record_192_0_2_10"),
        "198.51.100.7": fixture("record_198_51_100_7_empty"),
        "203.0.113.5": httpx.Response(404, json=fixture("no_information")),
    }


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


class FakeInternetDB:
    """Answers by the address in the path from ``routes``; a list replies in turn.

    Every request must go to internetdb.shodan.io over https; a request to
    any other host (the address looked up, above all) fails the test.
    """

    def __init__(self, routes: dict[str, Reply | list[Reply]] | None = None) -> None:
        self.routes: dict[str, Reply | list[Reply]] = dict(default_routes())
        self.routes.update(routes or {})
        self.requests: list[httpx.Request] = []

    @property
    def calls(self) -> list[str]:
        return [request.url.path.removeprefix("/") for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == HOST, f"request to {request.url.host}"
        reply = self.routes.get(request.url.path.removeprefix("/"))
        if reply is None:
            return httpx.Response(404, json={"detail": "No information available"})
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)


def shuttle(time: FakeTime, **overrides: Any) -> InternetDBShuttle:
    options: dict[str, Any] = {
        "sleep": time.sleep,
        "clock": time,
        "now": lambda: NOW,
        "address_filter": public_or_documentation,
    }
    options.update(overrides)
    return InternetDBShuttle(**options)


async def run_shuttle(
    fake: FakeInternetDB,
    entity: EntityInput,
    *,
    time: FakeTime | None = None,
    cancel_event: asyncio.Event | None = None,
    **overrides: Any,
) -> tuple[list[Finding], ShuttleContext, FakeTime]:
    time = time or FakeTime()
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(META, transport=httpx.MockTransport(fake.handler), limiter=limiter)
    ctx = ShuttleContext(META, http=http, cancel_event=cancel_event)
    found = await collect(shuttle(time, **overrides), entity, ctx=ctx)
    assert_declared(META, found, entity)
    assert_valid(found)
    assert_only_internetdb(fake, entity)
    return found, ctx, time


def assert_valid(found: list[Finding]) -> None:
    """Every entity finding is valid as the worker would store it (value and properties)."""
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)


def assert_only_internetdb(fake: FakeInternetDB, entity: EntityInput) -> None:
    """Every request went to InternetDB's record URL; none to the address looked up."""
    for request in fake.requests:
        assert str(request.url).startswith(BASE_URL)
        assert request.url.host == HOST != entity.value
        assert request.url.port in (None, 443)


def entities(found: list[Finding], entity_type: EntityType) -> list[EntityFinding]:
    return [f for f in found if isinstance(f, EntityFinding) and f.type == entity_type]


def relationships(found: list[Finding], relation: RelationType) -> list[RelationshipFinding]:
    return [f for f in found if isinstance(f, RelationshipFinding) and f.type == relation]


def edges(found: list[Finding], relation: RelationType) -> set[tuple[str, str]]:
    return {(f.source.value, f.target.value) for f in relationships(found, relation)}


def address_properties(found: list[Finding], value: str) -> dict[str, Any]:
    (ip,) = [f for f in entities(found, IP) if f.value == value]
    assert ip.properties is not None
    return ip.properties


# --- metadata and registration ------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "internetdb"
    assert META.title == "Open ports and exposures (Shodan InternetDB)"
    assert META.input_types == {IP}
    # IPAddress: the record's properties go onto the input itself.
    assert META.output_entity_types == {SERVICE, DOMAIN, IP}
    assert META.output_relationship_types == {R.RUNS_ON, R.RESOLVES_TO}
    assert (META.mode, META.network) == (Mode.PASSIVE, True)
    assert META.sensitivity is Sensitivity.INFRASTRUCTURE
    assert META.rate_limit is not None and META.rate_limit.per_second == pytest.approx(1)
    assert META.timeout_seconds == 60
    assert (META.default_admiralty, META.default_confidence) == ("B2", 0.8)
    assert META.homepage == "https://internetdb.shodan.io/"
    assert META.terms_url == "https://account.shodan.io/legal/tos"
    assert META.usage_note == USAGE_NOTE
    assert "non-commercial" in USAGE_NOTE and "Shodan license" in USAGE_NOTE
    assert META.required_keys == frozenset()
    registry = ShuttleRegistry.discover()
    assert registry.get("internetdb") is InternetDBShuttle
    assert InternetDBShuttle in registry.list(IP)
    assert InternetDBShuttle not in registry.list(DOMAIN)
    InternetDBShuttle.check_configuration()  # nothing to configure


# --- a full record ------------------------------------------------------------------------


async def test_full_record() -> None:
    fake = FakeInternetDB()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert fake.calls == ["192.0.2.10"]
    assert fake.requests[0].headers["Accept"] == "application/json"

    # The input address, with the record's properties (and nothing else, so an
    # existing scope property is kept when the worker merges the observation).
    assert address_properties(found, "192.0.2.10") == {
        "open_ports": [22, 80, 443, 8443],
        "cpes": [
            "cpe:/a:nginx:nginx:1.18.0",
            "cpe:/a:openbsd:openssh:8.2p1",
            "cpe:/o:canonical:ubuntu_linux",
        ],
        "vulns": ["CVE-2021-3618", "CVE-2021-23017", "CVE-2023-38408"],  # by year, then number
        "tags": ["self-signed", "starttls"],
        "internetdb_checked_at": NOW,
    }
    (ip,) = entities(found, IP)
    assert (ip.admiralty, ip.confidence) == (None, None)  # the Shuttle's B2, 0.8

    services = [s.value for s in entities(found, SERVICE)]
    assert services == [f"192.0.2.10:{p}/tcp" for p in (22, 80, 443, 8443)]
    assert edges(found, R.RUNS_ON) == {(s, "192.0.2.10") for s in services}

    domains = entities(found, DOMAIN)
    assert [d.value for d in domains] == ["api.example.net", "mail.example.org", "www.example.org"]
    assert {(d.admiralty, d.confidence) for d in domains} == {("B3", 0.6)}
    resolves = relationships(found, R.RESOLVES_TO)
    assert {(r.source.value, r.target.value) for r in resolves} == {
        (d.value, "192.0.2.10") for d in domains
    }
    assert {(r.admiralty, r.confidence, r.note) for r in resolves} == {("B3", 0.6, HOSTNAME_NOTE)}
    assert ctx.notes == ()

    # The address comes first, and every entity before the relationship that uses it.
    assert found[0] == ip
    seen: set[tuple[EntityType, str]] = {(IP, "192.0.2.10")}
    for finding in found:
        if isinstance(finding, EntityFinding):
            seen.add((finding.type, finding.value))
        else:
            assert (finding.source.type, finding.source.value) in seen


async def test_empty_lists_still_update_the_address() -> None:
    fake = FakeInternetDB()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "198.51.100.7"))
    # Empty lists are kept: they replace what an older run found.
    assert found == [
        EntityFinding(
            type=IP,
            value="198.51.100.7",
            properties={
                "open_ports": [],
                "cpes": [],
                "vulns": [],
                "tags": [],
                "internetdb_checked_at": NOW,
            },
        )
    ]
    assert ctx.notes == ()


async def test_missing_lists_are_empty() -> None:
    fake = FakeInternetDB({"192.0.2.10": {"ip": "192.0.2.10", "ports": [80]}})
    found, _, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    props = address_properties(found, "192.0.2.10")
    assert (props["open_ports"], props["cpes"], props["vulns"], props["tags"]) == ([80], [], [], [])
    assert [s.value for s in entities(found, SERVICE)] == ["192.0.2.10:80/tcp"]


# --- addresses that are not looked up -----------------------------------------------------


async def test_no_information_is_a_note_not_an_error() -> None:
    fake = FakeInternetDB()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "203.0.113.5"))
    assert found == []
    assert ctx.notes == (NOTE_NO_INFORMATION,)
    assert fake.calls == ["203.0.113.5"]


@pytest.mark.parametrize("value", ["2001:db8::1", "2606:4700:4700::1111"])
async def test_ipv6_is_skipped_without_a_request(value: str) -> None:
    fake = FakeInternetDB()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, value))
    assert found == []
    assert ctx.notes == (NOTE_IPV6,)
    assert NOTE_IPV6 == "InternetDB covers IPv4 addresses only."
    assert fake.requests == []


@pytest.mark.parametrize(
    "value", ["10.0.0.5", "172.16.1.1", "192.168.1.1", "127.0.0.1", "100.64.0.1", "169.254.1.1"]
)
async def test_private_and_reserved_addresses_are_skipped_without_a_request(value: str) -> None:
    fake = FakeInternetDB()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, value))
    assert found == []
    assert ctx.notes == (NOTE_NOT_PUBLIC,)
    assert fake.requests == []


async def test_the_default_filter_skips_documentation_addresses() -> None:
    """In production only public addresses are looked up: the fixtures' are not."""
    fake = FakeInternetDB()
    found, ctx, _ = await run_shuttle(
        fake, entity_input(IP, "192.0.2.10"), address_filter=is_public_address
    )
    assert (found, ctx.notes, fake.requests) == ([], (NOTE_NOT_PUBLIC,), [])
    assert InternetDBShuttle()._address_filter is is_public_address


async def test_an_ipv4_mapped_address_is_looked_up_as_ipv4() -> None:
    fake = FakeInternetDB()
    found, _, _ = await run_shuttle(fake, entity_input(IP, "::ffff:192.0.2.10"))
    assert fake.calls == ["192.0.2.10"]
    assert entities(found, SERVICE)


# --- filtering and caps -------------------------------------------------------------------


async def test_invalid_cve_strings_are_dropped_and_counted() -> None:
    vulns = [
        "CVE-2024-12345",
        "CVE-2021-0001",
        "cve-2022-1111",  # not upper-case
        "CVE-2021-123",  # too few digits
        "GHSA-abcd-efgh-ijkl",
        "CVE-2021-0001",  # duplicate
        " CVE-2020-9999 ",  # trimmed, then kept
        "CVE-2021-0001; DROP",
        42,  # malformed
    ]
    fake = FakeInternetDB({"192.0.2.10": record(vulns=vulns)})
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    props = address_properties(found, "192.0.2.10")
    assert props["vulns"] == ["CVE-2020-9999", "CVE-2021-0001", "CVE-2024-12345"]
    assert NOTE_NOT_CVE.format(count=4) in ctx.notes
    assert NOTE_MALFORMED.format(count=1) in ctx.notes
    # Notes give counts only, never the dropped values.
    assert not any("GHSA" in note or "DROP" in note for note in ctx.notes)


async def test_hostnames_are_normalized_and_filtered() -> None:
    hostnames = [
        "WWW.Example.org.",
        "www.example.org",  # the same name again
        "xn--bcher-kva.example.org",
        "Bücher.example.net",  # becomes an A-label
        "printer.local",  # internal
        "fileserver.corp",  # internal
        "localhost",  # single label: not a valid domain
        "10.2.0.192.in-addr.arpa",  # reverse DNS
        "hidden.onion",
        "*.example.org",  # wildcard
        "not a hostname",
        "-bad-.example.org",
        "192.0.2.10",  # an address, not a name
        "",
        None,
    ]
    fake = FakeInternetDB({"192.0.2.10": record(hostnames=hostnames)})
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert [d.value for d in entities(found, DOMAIN)] == [
        "www.example.org",
        "xn--bcher-kva.example.net",
        "xn--bcher-kva.example.org",
    ]
    assert len(relationships(found, R.RESOLVES_TO)) == 3
    assert NOTE_HOSTNAMES_DROPPED.format(invalid=7, internal=4) in ctx.notes
    assert not any("local" in note or "corp" in note for note in ctx.notes)


async def test_malformed_ports_cpes_and_tags_are_counted() -> None:
    fake = FakeInternetDB(
        {
            "192.0.2.10": record(
                ports=[443, 443, 0, 65536, "80", True, None, 22],
                cpes=["cpe:/a:x:y", "nginx", "", "cpe:/a:x:y", "c" * 301],
                tags=["cdn", " cdn ", "", "x" * 201, "bad\ntag", 7],
            )
        }
    )
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    props = address_properties(found, "192.0.2.10")
    assert props["open_ports"] == [22, 443]
    assert [s.value for s in entities(found, SERVICE)] == [
        "192.0.2.10:22/tcp",
        "192.0.2.10:443/tcp",
    ]
    assert props["cpes"] == ["cpe:/a:x:y"]
    assert props["tags"] == ["cdn"]
    # ports: 0, 65536, "80", True, None; cpes: nginx, "", too long; tags: "", too long, \n, 7
    assert NOTE_MALFORMED.format(count=12) in ctx.notes


async def test_caps() -> None:
    ports = list(range(1, 251))
    hostnames = [f"host{i:03d}.example.org" for i in range(150)]
    cpes = [f"cpe:/a:vendor:product{i}" for i in range(120)]
    vulns = [f"CVE-2024-{i:05d}" for i in range(600, 0, -1)]
    tags = [f"tag{i}" for i in range(60)]
    fake = FakeInternetDB(
        {
            "192.0.2.10": record(
                ports=list(reversed(ports)), hostnames=hostnames, cpes=cpes, vulns=vulns, tags=tags
            )
        }
    )
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    props = address_properties(found, "192.0.2.10")
    assert props["open_ports"] == ports[:MAX_PORTS]  # the lowest 200
    assert len(entities(found, SERVICE)) == len(relationships(found, R.RUNS_ON)) == MAX_PORTS
    assert [d.value for d in entities(found, DOMAIN)] == sorted(hostnames)[:MAX_HOSTNAMES]
    assert props["cpes"] == cpes[:MAX_CPES]
    assert props["vulns"] == [f"CVE-2024-{i:05d}" for i in range(1, MAX_VULNS + 1)]
    assert props["tags"] == tags[:MAX_TAGS]
    assert set(ctx.notes) == {
        NOTE_PORTS_CAPPED.format(count=250, limit=MAX_PORTS),
        NOTE_HOSTNAMES_CAPPED.format(count=150, limit=MAX_HOSTNAMES),
        NOTE_CAPPED.format(count=120, what="CPEs", limit=MAX_CPES),
        NOTE_CAPPED.format(count=600, what="vulnerabilities", limit=MAX_VULNS),
        NOTE_CAPPED.format(count=60, what="tags", limit=MAX_TAGS),
    }
    assert len(found) == 1 + 2 * MAX_PORTS + 2 * MAX_HOSTNAMES < 5_000


# --- answers that cannot be used ----------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        record(ip="192.0.2.11"),  # another address
        {"ports": [80]},  # no ip
        record(ports="80"),  # not a list
        record(hostnames={"a": 1}),
        ["a", "list"],
        httpx.Response(400, json={"detail": "Invalid IP"}),
        httpx.Response(403),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, content=b"{" + b" " * 6_000_000 + b"}"),  # over the 5 MB cap
    ],
)
async def test_unusable_answers_fail_at_once(reply: Reply) -> None:
    fake = FakeInternetDB({"192.0.2.10": reply})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE
    assert fake.calls == ["192.0.2.10"]


def busy(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(status, headers=headers)


async def test_429_waits_for_retry_after() -> None:
    fake = FakeInternetDB({"192.0.2.10": [busy(429, "7"), busy(429), fixture("record_192_0_2_10")]})
    found, _, time = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert entities(found, SERVICE)
    assert len(fake.calls) == 3
    assert 7.0 in time.sleeps and 5.0 in time.sleeps  # Retry-After, then the default


@pytest.mark.parametrize(
    "replies",
    [
        [busy(429, "1"), busy(429, "1"), busy(429, "1")],  # a third wait is not allowed
        [busy(429, "31")],  # longer than 30 seconds: give up at once
    ],
)
async def test_429_gives_up(replies: list[Reply]) -> None:
    fake = FakeInternetDB({"192.0.2.10": [*replies]})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert len(fake.calls) == len(replies)


async def test_5xx_is_retried_twice_with_backoff() -> None:
    fake = FakeInternetDB({"192.0.2.10": [busy(502), busy(503), fixture("record_192_0_2_10")]})
    found, _, time = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert entities(found, SERVICE)
    assert [s for s in time.sleeps if s in (2.0, 5.0)] == [2.0, 5.0]

    fake = FakeInternetDB({"192.0.2.10": [busy(500), busy(504), busy(503)]})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert len(fake.calls) == 3


async def test_network_errors_are_retried() -> None:
    fake = FakeInternetDB()
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("refused", request=request)
        return fake.handler(request)

    time = FakeTime()
    http = create_http_client(META, transport=httpx.MockTransport(flaky))
    ctx = ShuttleContext(META, http=http)
    found = await collect(shuttle(time), entity_input(IP, "192.0.2.10"), ctx=ctx)
    assert entities(found, SERVICE) and calls == 3


async def test_no_request_after_the_budget() -> None:
    time = FakeTime()
    fake = FakeInternetDB({"192.0.2.10": [busy(503), fixture("record_192_0_2_10")]})
    original = fake.handler

    def slow(request: httpx.Request) -> httpx.Response:
        response = original(request)
        time.now += REQUEST_BUDGET_SECONDS  # the first try took all the time
        return response

    fake.handler = slow  # type: ignore[method-assign]
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(IP, "192.0.2.10"), time=time)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert len(fake.calls) == 1


async def test_a_refused_destination_is_not_retried() -> None:
    from nightloom_sdk.http import DestinationNotAllowedError

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise DestinationNotAllowedError("refused")

    http = create_http_client(META, transport=httpx.MockTransport(handler))
    ctx = ShuttleContext(META, http=http)
    with pytest.raises(ShuttleFailedError) as failed:
        await collect(shuttle(FakeTime()), entity_input(IP, "192.0.2.10"), ctx=ctx)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert calls == 1


async def test_cancellation_is_checked_before_requests() -> None:
    fake = FakeInternetDB()
    event = asyncio.Event()
    event.set()
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, entity_input(IP, "192.0.2.10"), cancel_event=event)
    assert fake.requests == []


async def test_the_shuttle_can_use_a_given_client() -> None:
    fake = FakeInternetDB()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        ctx = ShuttleContext(META)
        found = await collect(
            shuttle(FakeTime(), http=http), entity_input(IP, "192.0.2.10"), ctx=ctx
        )
    assert entities(found, SERVICE)
    assert_only_internetdb(fake, entity_input(IP, "192.0.2.10"))


async def test_never_a_request_to_the_address_itself() -> None:
    """Whatever the input, the only destination is InternetDB."""
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json=record(request.url.path.removeprefix("/"), ports=[80]))

    for value in ("192.0.2.10", "198.51.100.7", "203.0.113.200"):
        http = create_http_client(META, transport=httpx.MockTransport(handler))
        ctx = ShuttleContext(META, http=http)
        await collect(shuttle(FakeTime()), entity_input(IP, value), ctx=ctx)
    assert hosts == [HOST] * 3


def test_retry_after_parsing() -> None:
    assert retry_after_seconds(None) is None
    assert retry_after_seconds(" 12 ") == 12.0
    assert retry_after_seconds("not a date") is None
    now = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
    assert retry_after_seconds("Thu, 19 Nov 2026 06:00:20 GMT", now=now) == 20.0
    assert retry_after_seconds("Thu, 19 Nov 2026 05:59:00 GMT", now=now) == 0.0
    # An HTTP date without a zone is read as UTC.
    assert retry_after_seconds("Thu, 19 Nov 2026 06:00:10 -0000", now=now) == 10.0


@pytest.mark.network
async def test_real_internetdb() -> None:
    """Asks the real InternetDB about 1.1.1.1. `make test-network` only."""
    entity = entity_input(IP, "1.1.1.1")
    ctx = ShuttleContext(META)
    found = await collect(InternetDBShuttle(), entity, ctx=ctx)
    assert_declared(META, found, entity)
    assert_valid(found)
    props = address_properties(found, "1.1.1.1")
    assert 53 in props["open_ports"], ctx.notes
    assert edges(found, R.RUNS_ON), ctx.notes
