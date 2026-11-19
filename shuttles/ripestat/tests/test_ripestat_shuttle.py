"""The RIPEstat Shuttle against a fake RIPEstat: no network.

The answers are RIPEstat's real envelope with fictional data only
(``fixtures/``): 192.0.2.0/24, 198.51.100.0/24, 2001:db8::/32, AS64496 and
AS64500.
"""

import asyncio
import copy
import json
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
from nightloom_sdk.http import create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_ripestat import RipeStatShuttle
from nightloom_shuttle_ripestat.client import BASE_URL, SOURCE_APP
from nightloom_shuttle_ripestat.shuttle import (
    MAX_HOLDER_LOOKUPS,
    MAX_PREFIXES,
    NOTE_AS_NOT_ANNOUNCED,
    NOTE_COVERED,
    NOTE_HOLDERS_CAPPED,
    NOTE_HOLDERS_FAILED,
    NOTE_IP_NOT_ANNOUNCED,
    NOTE_MALFORMED,
    NOTE_MOAS,
    NOTE_NETBLOCK_NOT_ANNOUNCED,
    NOTE_PREFIXES_CAPPED,
    NOTE_REDACTED,
    REQUEST_BUDGET_SECONDS,
)

IP = EntityType.IP_ADDRESS
NB = EntityType.NETBLOCK
AS = EntityType.ASN
ORG = EntityType.ORGANIZATION
R = RelationType
META = RipeStatShuttle.meta
FIXTURES = Path(__file__).parent / "fixtures"
HOST = "stat.ripe.net"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def envelope(data: dict[str, Any], status: str = "ok") -> dict[str, Any]:
    return {"status": status, "status_code": 200, "messages": [], "data": data}


Reply = dict[str, Any] | bytes | httpx.Response
Route = tuple[str, str]  # (endpoint, resource)


def default_routes() -> dict[Route, Reply]:
    return {
        ("network-info", "192.0.2.10"): fixture("network_info_192_0_2_10"),
        ("network-info", "198.51.100.7"): fixture("network_info_unannounced"),
        ("network-info", "2001:db8::1"): fixture("network_info_2001_db8_moas"),
        ("as-overview", "AS64496"): fixture("as_overview_64496"),
        ("as-overview", "AS64500"): fixture("as_overview_64500"),
        ("prefix-overview", "198.51.100.0/24"): fixture("prefix_overview_198_51_100"),
        ("prefix-overview", "192.0.2.128/25"): fixture("prefix_overview_192_0_2_128_covered"),
        ("prefix-overview", "2001:db8:ffff::/48"): fixture("prefix_overview_unannounced"),
        ("announced-prefixes", "AS64496"): fixture("announced_prefixes_64496"),
        ("announced-prefixes", "AS64500"): envelope({"prefixes": []}),
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


class FakeRipeStat:
    """Answers by (endpoint, resource) from ``routes``; a list replies in turn."""

    def __init__(self, routes: dict[Route, Reply | list[Reply]] | None = None) -> None:
        self.routes: dict[Route, Reply | list[Reply]] = dict(default_routes())
        self.routes.update(routes or {})
        self.requests: list[httpx.Request] = []

    @property
    def calls(self) -> list[Route]:
        return [self.route(request) for request in self.requests]

    def count(self, route: Route) -> int:
        return self.calls.count(route)

    @staticmethod
    def route(request: httpx.Request) -> Route:
        path = request.url.path.removeprefix("/data/").removesuffix("/data.json")
        return path, request.url.params.get("resource", "")

    @staticmethod
    def respond(reply: Reply) -> httpx.Response:
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == HOST
        reply = self.routes.get(self.route(request))
        if reply is None:
            return httpx.Response(
                400, json={"status": "error", "status_code": 400, "messages": [], "data": {}}
            )
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        return self.respond(reply)


async def run_shuttle(
    fake: FakeRipeStat,
    entity: EntityInput,
    *,
    time: FakeTime | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[list[Finding], ShuttleContext, FakeTime]:
    time = time or FakeTime()
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(META, transport=httpx.MockTransport(fake.handler), limiter=limiter)
    ctx = ShuttleContext(META, http=http, cancel_event=cancel_event)
    found = await collect(RipeStatShuttle(sleep=time.sleep, clock=time), entity, ctx=ctx)
    assert_declared(META, found, entity)
    assert_valid(found)
    assert_sourceapp(fake)
    return found, ctx, time


def assert_valid(found: list[Finding]) -> None:
    """Every entity finding is valid as the worker would store it (value and properties)."""
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)


def assert_sourceapp(fake: FakeRipeStat) -> None:
    """Every request is a data call over https that names Nightloom as its sourceapp."""
    for request in fake.requests:
        assert str(request.url).startswith(BASE_URL)
        assert request.url.params.get("sourceapp") == SOURCE_APP == "nightloom"


def entities(found: list[Finding], entity_type: EntityType) -> list[EntityFinding]:
    return [f for f in found if isinstance(f, EntityFinding) and f.type == entity_type]


def relationships(found: list[Finding], relation: RelationType) -> list[RelationshipFinding]:
    return [f for f in found if isinstance(f, RelationshipFinding) and f.type == relation]


def edges(found: list[Finding], relation: RelationType) -> set[tuple[str, str]]:
    return {(f.source.value, f.target.value) for f in relationships(found, relation)}


def by_value(found: list[Finding], entity_type: EntityType) -> dict[str, EntityFinding]:
    kept: dict[str, EntityFinding] = {}
    for finding in entities(found, entity_type):
        assert finding.value not in kept, f"{finding.value} yielded twice"
        kept[finding.value] = finding
    return kept


# --- metadata and registration ------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "ripestat"
    assert META.title == "Routing and ASN (RIPEstat)"
    assert META.input_types == {IP, NB, AS}
    assert META.output_entity_types == {NB, AS, ORG}
    assert META.output_relationship_types == {R.WITHIN, R.ANNOUNCED_BY, R.OWNS}
    assert (META.mode, META.network) == (Mode.PASSIVE, True)
    # Organization is an organization-sensitivity type, so "infrastructure" is not allowed.
    assert META.sensitivity is Sensitivity.ORGANIZATION
    assert META.rate_limit is not None and META.rate_limit.per_second == pytest.approx(2)
    assert META.timeout_seconds == 120
    assert (META.default_admiralty, META.default_confidence) == ("B2", 0.8)
    assert META.homepage == "https://stat.ripe.net/"
    assert META.terms_url and "ripestat-service-terms" in META.terms_url
    registry = ShuttleRegistry.discover()
    assert registry.get("ripestat") is RipeStatShuttle
    for entity_type in (IP, NB, AS):
        assert RipeStatShuttle in registry.list(entity_type)
    RipeStatShuttle.check_configuration()  # nothing to configure


# --- IP addresses -------------------------------------------------------------------------


async def test_announced_ip_address() -> None:
    fake = FakeRipeStat()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert fake.calls == [("network-info", "192.0.2.10"), ("as-overview", "AS64496")]

    (block,) = entities(found, NB)
    assert (block.value, block.properties) == ("192.0.2.0/24", {"announced": True})
    assert edges(found, R.WITHIN) == {("192.0.2.10", "192.0.2.0/24")}
    assert edges(found, R.ANNOUNCED_BY) == {("192.0.2.0/24", "AS64496")}

    asn = by_value(found, AS)["AS64496"]  # yielded once, with the as-overview properties
    assert asn.properties == {
        "as_name": "EXAMPLE-NET",
        "announced": True,
        "iana_block": "64496-64511",
        "iana_block_description": (
            "Reserved for use in documentation and sample code (Reference: RFC 5398)"
        ),
    }
    assert [o.value for o in entities(found, ORG)] == ["Example Networks B.V."]
    assert edges(found, R.OWNS) == {("Example Networks B.V.", "AS64496")}
    assert ctx.notes == ()
    # Entities come before the relationships that use them.
    order = [(type(f).__name__, getattr(f, "value", None)) for f in found]
    assert order.index(("EntityFinding", "AS64496")) < next(
        i for i, f in enumerate(found) if isinstance(f, RelationshipFinding) and f.type == R.OWNS
    )


async def test_unannounced_ip_address_is_a_note_not_an_error() -> None:
    fake = FakeRipeStat()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "198.51.100.7"))
    assert found == []
    assert ctx.notes == (NOTE_IP_NOT_ANNOUNCED,)
    assert "not announced in the global routing table" in NOTE_IP_NOT_ANNOUNCED
    assert fake.calls == [("network-info", "198.51.100.7")]  # no holder lookups


async def test_multiple_origin_ases() -> None:
    fake = FakeRipeStat()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "2001:db8::1"))
    assert fake.calls == [
        ("network-info", "2001:db8::1"),
        ("as-overview", "AS64496"),
        ("as-overview", "AS64500"),
    ]
    assert edges(found, R.WITHIN) == {("2001:db8::1", "2001:db8::/32")}
    assert edges(found, R.ANNOUNCED_BY) == {
        ("2001:db8::/32", "AS64496"),
        ("2001:db8::/32", "AS64500"),
    }
    asns = by_value(found, AS)
    assert set(asns) == {"AS64496", "AS64500"}
    # "EXAMPLE-TRANSIT  -   Example   Transit Ltd ": trimmed, spaces collapsed.
    assert asns["AS64500"].properties and asns["AS64500"].properties["as_name"] == "EXAMPLE-TRANSIT"
    assert edges(found, R.OWNS) == {
        ("Example Networks B.V.", "AS64496"),
        ("Example Transit Ltd", "AS64500"),
    }
    assert NOTE_MOAS.format(count=2) in ctx.notes


async def test_holder_lookups_are_capped() -> None:
    numbers = [64496 + i for i in range(MAX_HOLDER_LOOKUPS + 2)]
    fake = FakeRipeStat(
        {
            ("network-info", "192.0.2.10"): envelope(
                {"asns": [str(n) for n in numbers], "prefix": "192.0.2.0/24"}
            )
        }
    )
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    looked_up = [resource for endpoint, resource in fake.calls if endpoint == "as-overview"]
    assert looked_up == [f"AS{n}" for n in numbers[:MAX_HOLDER_LOOKUPS]]
    assert len(relationships(found, R.ANNOUNCED_BY)) == len(numbers)  # every origin is kept
    assert NOTE_HOLDERS_CAPPED.format(limit=MAX_HOLDER_LOOKUPS, count=len(numbers)) in ctx.notes
    # Only AS64496 and AS64500 have fixtures; the others answer 400 and cost a note.
    assert NOTE_HOLDERS_FAILED.format(count=MAX_HOLDER_LOOKUPS - 2) in ctx.notes


async def test_malformed_origins_and_prefix_are_counted() -> None:
    fake = FakeRipeStat(
        {
            ("network-info", "192.0.2.10"): envelope(
                {"asns": ["64496", "AS-NOPE", None, True, 0, "64496"], "prefix": "192.0.2.0/24"}
            )
        }
    )
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert edges(found, R.ANNOUNCED_BY) == {("192.0.2.0/24", "AS64496")}  # deduplicated
    assert NOTE_MALFORMED.format(count=4) in ctx.notes

    # A prefix that does not contain the address is not used.
    fake = FakeRipeStat(
        {("network-info", "192.0.2.10"): envelope({"asns": ["64496"], "prefix": "203.0.113.0/24"})}
    )
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert found == []
    assert ctx.notes == (NOTE_IP_NOT_ANNOUNCED, NOTE_MALFORMED.format(count=1))


# --- netblocks ----------------------------------------------------------------------------


async def test_announced_netblock_with_two_origins() -> None:
    fake = FakeRipeStat()
    found, ctx, _ = await run_shuttle(fake, entity_input(NB, "198.51.100.0/24"))
    assert fake.calls == [("prefix-overview", "198.51.100.0/24")]
    (block,) = entities(found, NB)
    assert block.value == "198.51.100.0/24"
    assert block.properties == {
        "announced": True,
        "iana_block": "198.51.100.0/24",
        "iana_block_description": "Documentation (TEST-NET-2)",
    }
    assert relationships(found, R.WITHIN) == []
    assert edges(found, R.ANNOUNCED_BY) == {
        ("198.51.100.0/24", "AS64496"),
        ("198.51.100.0/24", "AS64500"),
    }
    assert {v: f.properties for v, f in by_value(found, AS).items()} == {
        "AS64496": {"as_name": "EXAMPLE-NET"},
        "AS64500": {"as_name": "EXAMPLE-TRANSIT"},
    }
    assert edges(found, R.OWNS) == {
        ("Example Networks B.V.", "AS64496"),
        ("Example Transit Ltd", "AS64500"),
    }
    assert NOTE_MOAS.format(count=2) in ctx.notes


async def test_more_specific_netblock_gets_the_covering_prefix() -> None:
    fake = FakeRipeStat()
    found, ctx, _ = await run_shuttle(fake, entity_input(NB, "192.0.2.128/25"))
    (covering,) = entities(found, NB)
    assert covering.value == "192.0.2.0/24"
    assert covering.properties and covering.properties["announced"] is True
    # Netblock -> Netblock WITHIN is in the allow-list (assert_declared checked it).
    assert edges(found, R.WITHIN) == {("192.0.2.128/25", "192.0.2.0/24")}
    assert edges(found, R.ANNOUNCED_BY) == {("192.0.2.0/24", "AS64496")}
    assert NOTE_COVERED in ctx.notes


@pytest.mark.parametrize(
    "resource",
    [
        "198.51.100.0/24",  # does not contain the input
        "192.0.2.128/25",  # the input itself
        "2001:db8::/32",  # another address family
        "garbage",
    ],
)
async def test_a_covering_prefix_that_does_not_cover_is_malformed(resource: str) -> None:
    answer = fixture("prefix_overview_192_0_2_128_covered")
    answer["data"]["resource"] = resource
    fake = FakeRipeStat({("prefix-overview", "192.0.2.128/25"): answer})
    found, ctx, _ = await run_shuttle(fake, entity_input(NB, "192.0.2.128/25"))
    assert [(f.value, f.properties) for f in entities(found, NB)] == [
        (
            "192.0.2.128/25",
            {
                "announced": False,
                "iana_block": "192.0.2.0/24",
                "iana_block_description": "Documentation (TEST-NET-1)",
            },
        )
    ]
    assert relationships(found, R.WITHIN) == []
    assert NOTE_MALFORMED.format(count=1) in ctx.notes


async def test_unannounced_netblock() -> None:
    fake = FakeRipeStat()
    found, ctx, _ = await run_shuttle(fake, entity_input(NB, "2001:db8:ffff::/48"))
    (block,) = entities(found, NB)
    assert block.value == "2001:db8:ffff::/48"
    assert block.properties == {
        "announced": False,
        "iana_block": "2001:db8::/32",
        "iana_block_description": "Documentation",
    }
    assert [f for f in found if isinstance(f, RelationshipFinding)] == []
    assert ctx.notes == (NOTE_NETBLOCK_NOT_ANNOUNCED,)


async def test_malformed_and_redacted_origins() -> None:
    answer = fixture("prefix_overview_198_51_100")
    answer["data"]["asns"] += [
        {"asn": "not a number"},
        {"holder": "no asn"},
        "AS64999",
        {"asn": 64496, "holder": "duplicate"},
        {"asn": 64511, "holder": "EXAMPLE-PRIV - REDACTED FOR PRIVACY"},
        {"asn": 64510, "holder": None},
    ]
    fake = FakeRipeStat({("prefix-overview", "198.51.100.0/24"): answer})
    found, ctx, _ = await run_shuttle(fake, entity_input(NB, "198.51.100.0/24"))
    asns = by_value(found, AS)
    assert set(asns) == {"AS64496", "AS64500", "AS64511", "AS64510"}
    assert asns["AS64511"].properties == {"as_name": "EXAMPLE-PRIV"}  # the org part was dropped
    assert asns["AS64510"].properties is None
    assert {o.value for o in entities(found, ORG)} == {
        "Example Networks B.V.",
        "Example Transit Ltd",
    }
    assert NOTE_MALFORMED.format(count=3) in ctx.notes
    assert NOTE_REDACTED.format(count=1) in ctx.notes
    assert NOTE_MOAS.format(count=4) in ctx.notes
    assert all("REDACTED" not in f.model_dump_json() for f in found)


# --- AS numbers ---------------------------------------------------------------------------


async def test_asn_with_prefixes_in_a_stable_order() -> None:
    fake = FakeRipeStat()
    found, ctx, _ = await run_shuttle(fake, entity_input(AS, "as64496"))
    assert fake.calls == [("as-overview", "AS64496"), ("announced-prefixes", "AS64496")]

    (asn,) = entities(found, AS)
    assert asn.value == "AS64496"
    assert asn.properties and asn.properties["as_name"] == "EXAMPLE-NET"
    assert edges(found, R.OWNS) == {("Example Networks B.V.", "AS64496")}

    blocks = entities(found, NB)
    # Shortest prefix first, then IPv4 before IPv6, then by address.
    assert [b.value for b in blocks] == [
        "192.0.2.0/24",
        "198.51.100.0/24",
        "192.0.2.128/25",
        "2001:db8::/32",
        "2001:db8:1000::/36",
    ]
    assert {r.target.value for r in relationships(found, R.ANNOUNCED_BY)} == {"AS64496"}
    assert len(relationships(found, R.ANNOUNCED_BY)) == len(blocks)
    props = {b.value: b.properties for b in blocks}
    # Two timelines: the earliest start and the latest end, as UTC.
    test_net_2 = props["198.51.100.0/24"] or {}
    assert test_net_2["bgp_first_seen"].isoformat() == "2026-11-04T16:00:00+00:00"
    assert test_net_2["bgp_last_seen"].isoformat() == "2026-11-18T16:00:00+00:00"
    assert props["2001:db8::/32"] is None  # no timelines given
    # "not-a-prefix" and the item without a prefix.
    assert NOTE_MALFORMED.format(count=2) in ctx.notes


async def test_prefixes_are_capped_at_500() -> None:
    # Documentation ranges only: 400 IPv4 /32s, 300 IPv6 /48s and one /24.
    v4 = [f"192.0.2.{i}/32" for i in range(256)] + [f"198.51.100.{i}/32" for i in range(144)]
    v6 = [f"2001:db8:{i:x}::/48" for i in range(300)]
    items = [{"prefix": p, "timelines": []} for p in [*v6, *reversed(v4), "203.0.113.0/24"]]
    fake = FakeRipeStat({("announced-prefixes", "AS64496"): envelope({"prefixes": items})})
    found, ctx, _ = await run_shuttle(fake, entity_input(AS, "AS64496"))
    values = [b.value for b in entities(found, NB)]
    assert len(values) == MAX_PREFIXES
    assert values[0] == "203.0.113.0/24"  # the shortest prefix first
    assert values[1:401] == v4  # then the /32s, by address (192.0.2.255 before 198.51.100.0)
    assert values[401] == "2001:db8::/48" and values[-1] == "2001:db8:62::/48"
    assert NOTE_PREFIXES_CAPPED.format(count=701, limit=MAX_PREFIXES) in ctx.notes
    assert len(found) < 5_000
    # The same answer in another order gives the same result.
    shuffled = copy.deepcopy(items)
    shuffled.reverse()
    fake = FakeRipeStat({("announced-prefixes", "AS64496"): envelope({"prefixes": shuffled})})
    again, _, _ = await run_shuttle(fake, entity_input(AS, "AS64496"))
    assert [b.value for b in entities(again, NB)] == values


async def test_asn_that_announces_nothing() -> None:
    fake = FakeRipeStat()
    found, ctx, _ = await run_shuttle(fake, entity_input(AS, "AS64500"))
    assert [a.value for a in entities(found, AS)] == ["AS64500"]
    assert entities(found, NB) == []
    assert NOTE_AS_NOT_ANNOUNCED in ctx.notes


async def test_asn_without_a_holder() -> None:
    answer = fixture("as_overview_64496")
    answer["data"].update(holder="", announced=False, block=None)
    fake = FakeRipeStat({("as-overview", "AS64496"): answer})
    found, _, _ = await run_shuttle(fake, entity_input(AS, "AS64496"))
    (asn,) = entities(found, AS)
    assert asn.properties == {"announced": False}
    assert entities(found, ORG) == []


async def test_partial_holder_block_and_timelines() -> None:
    overview = fixture("as_overview_64496")
    overview["data"]["holder"] = "EXAMPLE-NET - "  # a network name, no organization
    overview["data"]["block"] = {"resource": "64496-64511", "desc": None}
    prefixes = envelope(
        {
            "prefixes": [
                {"prefix": "192.0.2.0/24", "timelines": [{"starttime": None, "endtime": None}]},
                {"prefix": "198.51.100.0/24", "timelines": [{"endtime": "2026-11-18T16:00:00"}]},
            ]
        }
    )
    fake = FakeRipeStat(
        {("as-overview", "AS64496"): overview, ("announced-prefixes", "AS64496"): prefixes}
    )
    found, _, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert by_value(found, AS)["AS64496"].properties == {
        "as_name": "EXAMPLE-NET",
        "announced": True,
        "iana_block": "64496-64511",
    }
    assert entities(found, ORG) == [] and relationships(found, R.OWNS) == []

    found, _, _ = await run_shuttle(fake, entity_input(AS, "AS64496"))
    props = {b.value: b.properties for b in entities(found, NB)}
    assert props["192.0.2.0/24"] is None
    assert set(props["198.51.100.0/24"] or {}) == {"bgp_last_seen"}


# --- answers that cannot be used ----------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        envelope({}, status="error"),  # status not ok
        envelope({}, status="maintenance"),
        {"status": "ok", "messages": []},  # no data
        {"status": "ok", "data": ["a", "list"]},
        {"data": {}},  # no status
        httpx.Response(400, json={"status": "error", "status_code": 400, "data": {}}),
        httpx.Response(404),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json=["a", "list"]),
        envelope({"is_less_specific": False, "asns": []}),  # prefix-overview without resource
        httpx.Response(200, content=b"{" + b" " * 6_000_000 + b"}"),  # over the 5 MB cap
    ],
)
async def test_unusable_answers_fail_at_once(reply: Reply) -> None:
    route = ("prefix-overview", "198.51.100.0/24")
    fake = FakeRipeStat({route: reply})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(NB, "198.51.100.0/24"))
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE
    assert fake.count(route) == 1


async def test_a_data_object_of_the_wrong_shape_fails() -> None:
    fake = FakeRipeStat(
        {("network-info", "192.0.2.10"): envelope({"asns": "64496", "prefix": "192.0.2.0/24"})}
    )
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE


def busy(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(status, headers=headers)


async def test_429_waits_for_retry_after() -> None:
    route = ("as-overview", "AS64496")
    fake = FakeRipeStat({route: [busy(429, "7"), busy(429), fixture("as_overview_64496")]})
    found, _, time = await run_shuttle(fake, entity_input(AS, "AS64496"))
    assert entities(found, AS)
    assert fake.count(route) == 3
    assert 7.0 in time.sleeps and 5.0 in time.sleeps  # Retry-After, then the default


@pytest.mark.parametrize(
    "replies",
    [
        [busy(429, "1"), busy(429, "1"), busy(429, "1")],  # a third wait is not allowed
        [busy(429, "31")],  # longer than 30 seconds: give up at once
    ],
)
async def test_429_gives_up(replies: list[Reply]) -> None:
    route = ("as-overview", "AS64496")
    fake = FakeRipeStat({route: [*replies]})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(AS, "AS64496"))
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert fake.count(route) == len(replies)


async def test_5xx_is_retried_twice_with_backoff() -> None:
    route = ("network-info", "192.0.2.10")
    fake = FakeRipeStat({route: [busy(502), busy(503), fixture("network_info_192_0_2_10")]})
    found, _, time = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert entities(found, NB)
    assert [s for s in time.sleeps if s in (2.0, 5.0)] == [2.0, 5.0]

    fake = FakeRipeStat({route: [busy(500), busy(504), busy(503)]})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert fake.count(route) == 3


async def test_network_errors_are_retried() -> None:
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if FakeRipeStat.route(request) == ("as-overview", "AS64496"):
            calls += 1
            if calls < 3:
                raise httpx.ConnectError("refused", request=request)
        return fake.handler(request)

    fake = FakeRipeStat()
    time = FakeTime()
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(META, transport=httpx.MockTransport(flaky), limiter=limiter)
    ctx = ShuttleContext(META, http=http)
    shuttle = RipeStatShuttle(sleep=time.sleep, clock=time)
    found = await collect(shuttle, entity_input(AS, "AS64496"), ctx=ctx)
    assert entities(found, AS) and calls == 3


async def test_a_failed_holder_lookup_keeps_the_prefix() -> None:
    fake = FakeRipeStat({("as-overview", "AS64496"): [busy(503), busy(503), busy(503)]})
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert edges(found, R.ANNOUNCED_BY) == {("192.0.2.0/24", "AS64496")}
    assert entities(found, ORG) == []
    assert NOTE_HOLDERS_FAILED.format(count=1) in ctx.notes


async def test_no_request_after_the_budget() -> None:
    time = FakeTime()
    fake = FakeRipeStat()
    original = fake.handler

    def slow(request: httpx.Request) -> httpx.Response:
        response = original(request)
        time.now += REQUEST_BUDGET_SECONDS  # the first call took all the time
        return response

    fake.handler = slow  # type: ignore[method-assign]
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(AS, "AS64496"), time=time)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert fake.calls == [("as-overview", "AS64496")]


async def test_a_refused_destination_is_not_retried() -> None:
    from nightloom_sdk.http import DestinationNotAllowedError

    def handler(request: httpx.Request) -> httpx.Response:
        raise DestinationNotAllowedError("refused")

    http = create_http_client(META, transport=httpx.MockTransport(handler))
    ctx = ShuttleContext(META, http=http)
    with pytest.raises(ShuttleFailedError) as failed:
        await collect(RipeStatShuttle(), entity_input(AS, "AS64496"), ctx=ctx)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE


async def test_cancellation_is_checked_before_requests() -> None:
    fake = FakeRipeStat()
    event = asyncio.Event()
    event.set()
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, entity_input(IP, "192.0.2.10"), cancel_event=event)
    assert fake.requests == []


async def test_the_shuttle_can_use_a_given_client() -> None:
    fake = FakeRipeStat()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        ctx = ShuttleContext(META)
        found = await collect(RipeStatShuttle(http=http), entity_input(AS, "AS64496"), ctx=ctx)
    assert entities(found, AS)
    assert_sourceapp(fake)


@pytest.mark.network
async def test_real_ripestat() -> None:
    """Asks the real RIPEstat about 1.1.1.1 and AS13335. `make test-network` only."""
    for entity in (entity_input(IP, "1.1.1.1"), entity_input(AS, "AS13335")):
        ctx = ShuttleContext(META)
        found = await collect(RipeStatShuttle(), entity, ctx=ctx)
        assert_declared(META, found, entity)
        assert_valid(found)
        assert "AS13335" in {a.value for a in entities(found, AS)}, ctx.notes
        assert relationships(found, R.OWNS), ctx.notes
        if entity.type == IP:
            assert edges(found, R.WITHIN), ctx.notes
        else:
            assert len(entities(found, NB)) == MAX_PREFIXES, ctx.notes
