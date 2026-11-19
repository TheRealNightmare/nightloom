"""The RDAP Shuttle against a fake IANA and fake RDAP servers: no network.

The answers are realistic RDAP JSON with fictional data only (``fixtures/``):
example.org and example.net, 192.0.2.0/24, 2001:db8::/32 and AS64496. Their
contacts carry names of people, email addresses, phone numbers and postal
addresses on purpose, so the tests can check that none of them ever reaches
a finding.
"""

import asyncio
import copy
import json
import re
from collections.abc import Callable
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
from nightloom_sdk.http import create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_rdap import RdapShuttle
from nightloom_shuttle_rdap.bootstrap import CACHE_SECONDS, BootstrapCache
from nightloom_shuttle_rdap.client import ACCEPT
from nightloom_shuttle_rdap.shuttle import (
    NOTE_CIDRS_CAPPED,
    NOTE_NO_RANGE,
    NOTE_NOT_FOUND,
    NOTE_NOT_INSIDE,
    NOTE_REGISTRAR_FAILED,
    NOTE_REGISTRAR_NOT_FOUND,
    NOTE_SUBDOMAIN,
    REQUEST_BUDGET_SECONDS,
)

D = EntityType.DOMAIN
IP = EntityType.IP_ADDRESS
NB = EntityType.NETBLOCK
AS = EntityType.ASN
ORG = EntityType.ORGANIZATION
R = RelationType
META = RdapShuttle.meta
FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


IANA = "https://data.iana.org/rdap/"
ORG_REGISTRY = "https://rdap.org-registry.example/rdap/"
NET_REGISTRY = "https://rdap.net-registry.example/v1/"
REGISTRAR = "https://rdap.registrar.example/rdap/"
ARIN = "https://rdap.arin.net/registry/"
RIPE = "https://rdap.db.ripe.net/"

# Contact data in the fixtures that must never appear in a finding or a note.
PERSONAL = (
    "Jane Example",
    "Pat Example",
    "Chris Example",
    "Abuse Desk Example",
    "1 Fictional Street",
    "2 Imaginary Road",
    "3 Sample Avenue",
    "Exampletown",
    "Sampleville",
    "ZZ1 1ZZ",
    "5555550",
    "555-555",
    "REDACTED FOR PRIVACY",
    "whois.registrar.example",
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"tel:|\+\d[\d .-]{6,}")

Reply = dict[str, Any] | bytes | httpx.Response


def default_routes() -> dict[str, Reply]:
    return {
        IANA + "dns.json": fixture("bootstrap_dns"),
        IANA + "ipv4.json": fixture("bootstrap_ipv4"),
        IANA + "ipv6.json": fixture("bootstrap_ipv6"),
        IANA + "asn.json": fixture("bootstrap_asn"),
        ORG_REGISTRY + "domain/example.org": fixture("domain_example_org"),
        NET_REGISTRY + "domain/example.net": fixture("domain_example_net_registry"),
        REGISTRAR + "domain/EXAMPLE.NET": fixture("domain_example_net_registrar"),
        ARIN + "ip/192.0.2.10": fixture("ip_192_0_2"),
        ARIN + "ip/192.0.2.0/25": fixture("ip_192_0_2"),
        ARIN + "ip/192.0.2.0/24": fixture("ip_192_0_2"),
        RIPE + "ip/2001:db8::1": fixture("ip_2001_db8"),
        ARIN + "autnum/64496": fixture("autnum_64496"),
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


class FakeRdap:
    """Answers by URL (scheme, host and path) from ``routes``; a list replies in turn."""

    def __init__(self, routes: dict[str, Reply | list[Reply]] | None = None) -> None:
        self.routes: dict[str, Reply | list[Reply]] = dict(default_routes())
        self.routes.update(routes or {})
        self.requests: list[httpx.Request] = []

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]

    def count(self, url: str) -> int:
        return self.urls.count(url)

    @staticmethod
    def respond(reply: Reply) -> httpx.Response:
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply, headers={"Content-Type": "application/rdap+json"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url.copy_with(query=None))
        reply = self.routes.get(url)
        if reply is None:
            return httpx.Response(404, json={"errorCode": 404, "title": "Not Found"})
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        return self.respond(reply)


async def run_shuttle(
    fake: FakeRdap,
    entity: EntityInput,
    *,
    time: FakeTime | None = None,
    bootstrap: BootstrapCache | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[list[Finding], ShuttleContext, FakeTime]:
    time = time or FakeTime()
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(META, transport=httpx.MockTransport(fake.handler), limiter=limiter)
    ctx = ShuttleContext(META, http=http, cancel_event=cancel_event)
    shuttle = RdapShuttle(
        sleep=time.sleep, clock=time, bootstrap=bootstrap or BootstrapCache(clock=time)
    )
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, found, entity)
    assert_private(found, ctx)
    assert_valid(found)
    return found, ctx, time


def assert_private(found: list[Finding], ctx: ShuttleContext) -> None:
    """No email address, phone number, postal address or person's name anywhere."""
    texts = [finding.model_dump_json() for finding in found] + list(ctx.notes)
    for text in texts:
        assert not EMAIL.search(text), text
        assert not PHONE.search(text), text
        for value in PERSONAL:
            assert value.casefold() not in text.casefold(), (value, text)


def assert_valid(found: list[Finding]) -> None:
    """Every entity finding is valid as the worker would store it (value and properties)."""
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)


def entities(found: list[Finding], entity_type: EntityType) -> list[EntityFinding]:
    return [f for f in found if isinstance(f, EntityFinding) and f.type == entity_type]


def relationships(found: list[Finding], relation: RelationType) -> list[RelationshipFinding]:
    return [f for f in found if isinstance(f, RelationshipFinding) and f.type == relation]


def edges(found: list[Finding], relation: RelationType) -> set[tuple[str, str]]:
    return {(f.source.value, f.target.value) for f in relationships(found, relation)}


# --- metadata and registration ------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "rdap"
    assert META.title == "Registration data (RDAP)"
    assert META.input_types == {D, IP, NB, AS}
    assert META.output_entity_types == {D, NB, AS, ORG}
    assert META.output_relationship_types == {R.REGISTERED_BY, R.HAS_NS, R.WITHIN, R.OWNS}
    assert (META.mode, META.network) == (Mode.PASSIVE, True)
    # Organization is an organization-sensitivity type: not infrastructure, never person.
    assert META.sensitivity is Sensitivity.ORGANIZATION
    assert META.rate_limit is not None and META.rate_limit.per_second == pytest.approx(1)
    assert META.timeout_seconds == 120
    assert META.default_admiralty == "A2"
    registry = ShuttleRegistry.discover()
    assert registry.get("rdap") is RdapShuttle
    for entity_type in (D, IP, NB, AS):
        assert RdapShuttle in registry.list(entity_type)
    RdapShuttle.check_configuration()  # nothing to configure


# --- domains ------------------------------------------------------------------------------


async def test_thin_registry_and_registrar_are_merged_registry_first() -> None:
    fake = FakeRdap()
    found, ctx, _ = await run_shuttle(fake, entity_input(D, "example.net"))

    assert fake.urls == [
        IANA + "dns.json",
        NET_REGISTRY + "domain/example.net",
        REGISTRAR + "domain/EXAMPLE.NET",  # one related link, the first
    ]
    assert all(r.headers["Accept"] == ACCEPT for r in fake.requests)

    (first,) = [f for f in entities(found, D) if f.value == "example.net"]
    assert first.admiralty is None  # the Shuttle's default: registry data, A2
    assert first.properties == {
        "registered": build_iso("1999-03-15T05:00:00Z"),
        "expires": build_iso("2027-03-15T04:00:00Z"),  # the registry's, not the registrar's 2028
        "last_changed": build_iso("2026-02-10T16:21:33Z"),
        "status": [
            "client delete prohibited",
            "client transfer prohibited",
            "client update prohibited",
        ],
        "dnssec_signed": False,  # the registry's, not the registrar's True
        "registrar": "Example Registrar, Inc.",
        "registrar_iana_id": 9999,
        "rdap_server": NET_REGISTRY,
    }
    # Everything the registrar's answer says about the domain the registry said too, so
    # there is no second observation of example.net (see the next test for one).

    orgs = {o.value: o for o in entities(found, ORG)}
    assert set(orgs) == {"Example Registrar, Inc.", "Example Network Operations Ltd"}
    assert orgs["Example Registrar, Inc."].admiralty is None  # from the registry: A2
    assert orgs["Example Network Operations Ltd"].admiralty == "B2"  # only the registrar says it
    notes = {r.target.value: r.note for r in relationships(found, R.REGISTERED_BY)}
    assert notes == {
        "Example Registrar, Inc.": "role: registrar",
        "Example Network Operations Ltd": "role: registrant",
    }
    assert edges(found, R.HAS_NS) == {
        ("example.net", "ns1.example.net"),
        ("example.net", "ns2.example.net"),
    }  # the registry's name servers, deduplicated and without the trailing dot
    assert any(note.startswith("Skipped ") for note in ctx.notes)


def build_iso(text: str) -> Any:
    from nightloom_shuttle_rdap.objects import parse_date

    return parse_date(text)


async def test_registrar_only_fields_are_a_second_b2_observation() -> None:
    registry = fixture("domain_example_net_registry")
    del registry["secureDNS"]
    del registry["status"]
    fake = FakeRdap({NET_REGISTRY + "domain/example.net": registry})
    found, _, _ = await run_shuttle(fake, entity_input(D, "example.net"))
    first, second = [f for f in entities(found, D) if f.value == "example.net"]
    assert "dnssec_signed" not in (first.properties or {})
    assert second.properties == {"status": ["active"], "dnssec_signed": True}
    assert (second.admiralty, second.confidence) == ("B2", 0.8)


async def test_registrar_name_servers_when_the_registry_has_none() -> None:
    registry = fixture("domain_example_net_registry")
    del registry["nameservers"]
    fake = FakeRdap({NET_REGISTRY + "domain/example.net": registry})
    found, _, _ = await run_shuttle(fake, entity_input(D, "example.net"))
    assert edges(found, R.HAS_NS) == {("example.net", "ns9.registrar.example")}
    assert {f.admiralty for f in entities(found, D) if f.value == "ns9.registrar.example"} == {"B2"}


async def test_subdomain_is_looked_up_as_its_registrable_domain() -> None:
    fake = FakeRdap()
    entity = entity_input(D, "www.Example.org")
    found, ctx, _ = await run_shuttle(fake, entity)
    assert NOTE_SUBDOMAIN in ctx.notes
    assert fake.urls == [IANA + "dns.json", ORG_REGISTRY + "domain/example.org"]  # https chosen
    domain = entities(found, D)[0]
    assert domain.value == "example.org"
    assert domain.properties and domain.properties["rdap_server"] == ORG_REGISTRY
    # Relationships start at the registrable domain, not the subdomain.
    assert {r.source.value for r in relationships(found, R.REGISTERED_BY)} == {"example.org"}
    assert {r.source.value for r in relationships(found, R.HAS_NS)} == {"example.org"}


async def test_individual_registrant_is_ignored() -> None:
    found, ctx, _ = await run_shuttle(FakeRdap(), entity_input(D, "example.org"))
    assert [o.value for o in entities(found, ORG)] == ["Example Registry Services"]
    assert edges(found, R.REGISTERED_BY) == {("example.org", "Example Registry Services")}
    # One contact with two roles: registrant (an individual) and administrative.
    assert "Skipped 1 contact(s): 1 not an organization." in ctx.notes[0]
    props = entities(found, D)[0].properties or {}
    assert props["last_changed"].isoformat() == "2026-08-14T07:01:44+00:00"  # naive → UTC


@pytest.mark.parametrize(
    "name",
    [
        "REDACTED FOR PRIVACY",
        "Data Protected, Not Disclosed",
        "GDPR Masked",
        "Privacy service provided by Withheld for Privacy ehf",
        "redacted",
        "hostmaster@example.org",
        "+1 555 555 0100",
        "",
    ],
)
async def test_redacted_or_unusable_registrant_names_are_skipped(name: str) -> None:
    answer = fixture("domain_example_net_registrar")
    answer["entities"][1]["vcardArray"][1][1][3] = name
    fake = FakeRdap({REGISTRAR + "domain/EXAMPLE.NET": answer})
    found, ctx, _ = await run_shuttle(fake, entity_input(D, "example.net"))
    assert [o.value for o in entities(found, ORG)] == ["Example Registrar, Inc."]
    assert any("redacted" in note or "without a usable name" in note for note in ctx.notes)
    if name:
        assert all(name not in finding.model_dump_json() for finding in found)


async def test_registrant_and_registrar_that_are_one_organization() -> None:
    answer = fixture("domain_example_org")
    answer["entities"][1]["vcardArray"][1][1][3] = "Example Registry Services"
    answer["entities"][1]["vcardArray"][1][2][3] = "org"
    found, _, _ = await run_shuttle(
        FakeRdap({ORG_REGISTRY + "domain/example.org": answer}), entity_input(D, "example.org")
    )
    assert [r.note for r in relationships(found, R.REGISTERED_BY)] == [
        "role: registrant, registrar"
    ]


async def test_registrar_failures_keep_the_registry_data() -> None:
    fake = FakeRdap({REGISTRAR + "domain/EXAMPLE.NET": httpx.Response(403)})
    found, ctx, _ = await run_shuttle(fake, entity_input(D, "example.net"))
    assert NOTE_REGISTRAR_FAILED in ctx.notes
    assert [o.value for o in entities(found, ORG)] == ["Example Registrar, Inc."]

    del fake.routes[REGISTRAR + "domain/EXAMPLE.NET"]  # now a 404
    found, ctx, _ = await run_shuttle(fake, entity_input(D, "example.net"))
    assert NOTE_REGISTRAR_NOT_FOUND in ctx.notes

    fake.routes[REGISTRAR + "domain/EXAMPLE.NET"] = {"objectClassName": "entity"}
    found, ctx, _ = await run_shuttle(fake, entity_input(D, "example.net"))
    assert NOTE_REGISTRAR_FAILED in ctx.notes


async def test_unregistered_domain_is_a_quiet_note() -> None:
    fake = FakeRdap()
    found, ctx, _ = await run_shuttle(fake, entity_input(D, "unregistered.org"))
    assert found == []
    assert ctx.notes == (NOTE_NOT_FOUND.format(what="domain (it may not be registered)"),)


async def test_unknown_tld_fails_with_a_clear_error() -> None:
    fake = FakeRdap()
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(D, "example.de"))
    assert failed.value.reason is FailureReason.NOT_COVERED
    assert "no RDAP server is known" in failed.value.message
    assert fake.urls == [IANA + "dns.json"]


@pytest.mark.parametrize("value", ["co.uk", "printer.local"])
async def test_public_suffixes_and_internal_names_are_refused(value: str) -> None:
    fake = FakeRdap()
    entity = EntityInput(type=D, value=value, key=f"domain:{value}")
    with pytest.raises(InputRefusedError) as refused:
        await run_shuttle(fake, entity)
    assert refused.value.reason is RefusalReason.NOT_REGISTRABLE
    assert fake.requests == []


async def test_wrong_object_class_is_an_invalid_response() -> None:
    fake = FakeRdap({NET_REGISTRY + "domain/example.net": {"objectClassName": "nameserver"}})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(D, "example.net"))
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE


# --- IP addresses and netblocks ------------------------------------------------------------


async def test_ip_address_with_cidr0() -> None:
    fake = FakeRdap()
    found, _, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert fake.urls == [IANA + "ipv4.json", ARIN + "ip/192.0.2.10"]  # longest prefix: ARIN
    (block,) = entities(found, NB)
    assert block.value == "192.0.2.0/24"
    assert block.properties == {
        "handle": "NET-192-0-2-0-1",
        "name": "EXAMPLE-NET",
        "type": "DIRECT ALLOCATION",
        "country": "US",
        "registry": "ARIN",
        "parent_handle": "NET-192-0-0-0-0",
        "registered": build_iso("2010-06-01T14:00:00Z"),
        "last_changed": build_iso("2025-01-20T14:30:00Z"),
    }
    assert edges(found, R.WITHIN) == {("192.0.2.10", "192.0.2.0/24")}
    assert edges(found, R.OWNS) == {("Example Hosting LLC", "192.0.2.0/24")}
    assert [o.value for o in entities(found, ORG)] == ["Example Hosting LLC"]


async def test_ipv6_range_without_cidr0() -> None:
    fake = FakeRdap()
    found, _, _ = await run_shuttle(fake, entity_input(IP, "2001:db8::1"))
    assert fake.urls == [IANA + "ipv6.json", RIPE + "ip/2001:db8::1"]
    (block,) = entities(found, NB)
    assert block.value == "2001:db8::/32"
    assert block.properties and block.properties["registry"] == "RIPE NCC"
    assert edges(found, R.WITHIN) == {("2001:db8::1", "2001:db8::/32")}


async def test_non_aligned_range_becomes_several_blocks() -> None:
    answer = fixture("ip_192_0_2")
    del answer["cidr0_cidrs"]
    answer["endAddress"] = "192.0.2.95"
    fake = FakeRdap({ARIN + "ip/192.0.2.10": answer})
    found, _, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert [b.value for b in entities(found, NB)] == ["192.0.2.0/26", "192.0.2.64/27"]
    assert edges(found, R.WITHIN) == {("192.0.2.10", "192.0.2.0/26")}  # only the one it is in
    assert edges(found, R.OWNS) == {
        ("Example Hosting LLC", "192.0.2.0/26"),
        ("Example Hosting LLC", "192.0.2.64/27"),
    }


async def test_a_range_needing_too_many_blocks_is_capped() -> None:
    answer = fixture("ip_192_0_2")
    del answer["cidr0_cidrs"]
    answer["startAddress"], answer["endAddress"] = "192.0.2.1", "192.0.2.254"  # 14 blocks
    fake = FakeRdap({ARIN + "ip/192.0.2.10": answer})
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert len(entities(found, NB)) == 14
    assert not any("CIDR" in note for note in ctx.notes)
    assert edges(found, R.WITHIN) == {("192.0.2.10", "192.0.2.8/29")}

    answer["startAddress"], answer["endAddress"] = "2001:db8::1", "2001:db8::fffe"
    fake = FakeRdap({ARIN + "ip/192.0.2.10": answer})
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert len(entities(found, NB)) == 16
    assert NOTE_CIDRS_CAPPED.format(limit=16) in ctx.notes
    assert relationships(found, R.WITHIN) == []  # an IPv4 address is in no IPv6 block


async def test_netblock_inside_the_registered_network() -> None:
    fake = FakeRdap()
    found, ctx, _ = await run_shuttle(fake, entity_input(NB, "192.0.2.0/25"))
    assert fake.urls[-1] == ARIN + "ip/192.0.2.0/25"
    assert edges(found, R.WITHIN) == {("192.0.2.0/25", "192.0.2.0/24")}
    assert ctx.notes[-1].startswith("Skipped")


async def test_netblock_equal_to_the_registered_network_gets_the_properties() -> None:
    found, ctx, _ = await run_shuttle(FakeRdap(), entity_input(NB, "192.0.2.0/24"))
    (block,) = entities(found, NB)
    assert block.value == "192.0.2.0/24" and block.properties
    assert relationships(found, R.WITHIN) == []  # not WITHIN itself
    assert edges(found, R.OWNS) == {("Example Hosting LLC", "192.0.2.0/24")}
    assert NOTE_NOT_INSIDE not in ctx.notes


async def test_netblock_not_inside_the_answer() -> None:
    answer = fixture("ip_192_0_2")
    answer["cidr0_cidrs"] = [{"v4prefix": "192.0.2.0", "length": 26}]
    fake = FakeRdap({ARIN + "ip/192.0.2.0/25": answer})
    found, ctx, _ = await run_shuttle(fake, entity_input(NB, "192.0.2.0/25"))
    assert relationships(found, R.WITHIN) == []
    assert NOTE_NOT_INSIDE in ctx.notes


async def test_network_without_a_usable_range() -> None:
    answer = {"objectClassName": "ip network", "handle": "NET-X", "startAddress": "nonsense"}
    fake = FakeRdap({ARIN + "ip/192.0.2.10": answer})
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert found == []
    assert ctx.notes == (NOTE_NO_RANGE,)


async def test_owner_must_be_an_organization() -> None:
    answer = fixture("ip_192_0_2")
    answer["entities"][0]["vcardArray"][1][2][3] = "individual"
    fake = FakeRdap({ARIN + "ip/192.0.2.10": answer})
    found, _, _ = await run_shuttle(fake, entity_input(IP, "192.0.2.10"))
    assert entities(found, ORG) == []
    assert relationships(found, R.OWNS) == []


async def test_unallocated_address_and_unknown_range() -> None:
    fake = FakeRdap()
    found, ctx, _ = await run_shuttle(fake, entity_input(IP, "198.51.100.7"))
    assert found == []
    assert ctx.notes == (NOTE_NOT_FOUND.format(what="address range"),)

    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(IP, "203.0.113.5"))
    assert failed.value.reason is FailureReason.NOT_COVERED


# --- AS numbers ---------------------------------------------------------------------------


async def test_asn() -> None:
    fake = FakeRdap()
    found, _, _ = await run_shuttle(fake, entity_input(AS, "as64496"))
    assert fake.urls == [IANA + "asn.json", ARIN + "autnum/64496"]  # smallest range: ARIN
    (asn,) = entities(found, AS)
    assert asn.value == "AS64496"
    assert asn.properties == {
        "handle": "AS64496",
        "name": "EXAMPLE-AS",
        "country": "US",
        "registry": "ARIN",
        "registered": build_iso("2001-09-11T00:00:00Z"),
        "last_changed": build_iso("2024-12-01T00:00:00Z"),
    }
    assert edges(found, R.OWNS) == {("Example Hosting LLC", "AS64496")}


async def test_unknown_asn() -> None:
    fake = FakeRdap()
    _, ctx, _ = await run_shuttle(fake, entity_input(AS, "AS64500"))  # APNIC, not found
    assert fake.urls[-1] == "https://rdap.apnic.net/autnum/64500"
    assert ctx.notes == (NOTE_NOT_FOUND.format(what="AS number"),)
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(AS, "AS4200000000"))
    assert failed.value.reason is FailureReason.NOT_COVERED


# --- the bootstrap cache ------------------------------------------------------------------


async def test_bootstrap_is_fetched_once_and_cached_for_a_day() -> None:
    fake = FakeRdap()
    time = FakeTime()
    cache = BootstrapCache(clock=time)
    await run_shuttle(fake, entity_input(D, "example.org"), time=time, bootstrap=cache)
    await run_shuttle(fake, entity_input(D, "example.net"), time=time, bootstrap=cache)
    assert fake.count(IANA + "dns.json") == 1
    time.now += CACHE_SECONDS
    await run_shuttle(fake, entity_input(D, "example.org"), time=time, bootstrap=cache)
    assert fake.count(IANA + "dns.json") == 2
    assert cache.fetches == 2


async def test_concurrent_jobs_fetch_the_bootstrap_once() -> None:
    fake = FakeRdap()
    cache = BootstrapCache()
    entities_ = [entity_input(D, "example.org"), entity_input(D, "example.net")] * 3
    results = await asyncio.gather(
        *(run_shuttle(fake, entity, bootstrap=cache) for entity in entities_)
    )
    assert all(found for found, _, _ in results)
    assert fake.count(IANA + "dns.json") == 1
    assert cache.fetches == 1


async def test_a_stale_bootstrap_is_used_when_iana_fails() -> None:
    fake = FakeRdap()
    time = FakeTime()
    cache = BootstrapCache(clock=time)
    await run_shuttle(fake, entity_input(D, "example.org"), time=time, bootstrap=cache)
    time.now += CACHE_SECONDS + 1
    fake.routes[IANA + "dns.json"] = httpx.Response(503)
    found, _, _ = await run_shuttle(
        fake, entity_input(D, "example.org"), time=time, bootstrap=cache
    )
    assert entities(found, D)
    assert fake.count(IANA + "dns.json") == 1 + 3  # one try, two retries


async def test_bootstrap_unavailable_or_malformed() -> None:
    fake = FakeRdap({IANA + "dns.json": httpx.Response(503)})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(D, "example.org"))
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE

    fake = FakeRdap({IANA + "dns.json": {"services": "nope"}})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(D, "example.org"))
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE

    fake = FakeRdap()
    del fake.routes[IANA + "dns.json"]  # a 404 for a bootstrap file is not "no record"
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(D, "example.org"))
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE


# --- HTTP errors --------------------------------------------------------------------------


def busy(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(status, headers=headers)


async def test_429_waits_for_retry_after() -> None:
    url = ORG_REGISTRY + "domain/example.org"
    fake = FakeRdap({url: [busy(429, "7"), busy(429), fixture("domain_example_org")]})
    found, _, time = await run_shuttle(fake, entity_input(D, "example.org"))
    assert entities(found, D)
    assert fake.count(url) == 3
    assert 7.0 in time.sleeps and 5.0 in time.sleeps  # Retry-After, then the default


@pytest.mark.parametrize(
    "replies",
    [
        [busy(429, "1"), busy(429, "1"), busy(429, "1")],  # a third wait is not allowed
        [busy(429, "31")],  # longer than 30 seconds: give up at once
    ],
)
async def test_429_gives_up(replies: list[Reply]) -> None:
    url = ORG_REGISTRY + "domain/example.org"
    fake = FakeRdap({url: [*replies]})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(D, "example.org"))
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert fake.count(url) == len(replies)


async def test_5xx_is_retried_twice_with_backoff() -> None:
    url = ORG_REGISTRY + "domain/example.org"
    fake = FakeRdap({url: [busy(502), busy(503), fixture("domain_example_org")]})
    found, _, time = await run_shuttle(fake, entity_input(D, "example.org"))
    assert entities(found, D)
    assert [s for s in time.sleeps if s in (2.0, 5.0)] == [2.0, 5.0]

    fake = FakeRdap({url: [busy(500), busy(504), busy(503)]})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(D, "example.org"))
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert fake.count(url) == 3


async def test_network_errors_are_retried() -> None:
    url = ARIN + "autnum/64496"
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if str(request.url) == url:
            calls += 1
            if calls < 3:
                raise httpx.ConnectError("refused", request=request)
        return fake.handler(request)

    fake = FakeRdap()
    time = FakeTime()
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(META, transport=httpx.MockTransport(flaky), limiter=limiter)
    ctx = ShuttleContext(META, http=http)
    shuttle = RdapShuttle(sleep=time.sleep, clock=time, bootstrap=BootstrapCache(clock=time))
    found = await collect(shuttle, entity_input(AS, "AS64496"), ctx=ctx)
    assert entities(found, AS) and calls == 3


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(400),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json=["a", "list"]),
        httpx.Response(200, content=b"{" + b" " * 6_000_000 + b"}"),  # over the 5 MB cap
    ],
)
async def test_unreadable_answers_fail_at_once(reply: httpx.Response) -> None:
    url = ARIN + "autnum/64496"
    fake = FakeRdap({url: reply})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(AS, "AS64496"))
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE
    assert fake.count(url) == 1


async def test_no_request_after_the_budget() -> None:
    url = ORG_REGISTRY + "domain/example.org"
    time = FakeTime()

    def slow(_: httpx.Request) -> None:
        time.now += REQUEST_BUDGET_SECONDS  # the bootstrap took all the time

    fake = FakeRdap()
    fake.routes[url] = fixture("domain_example_org")
    original: Callable[[httpx.Request], httpx.Response] = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        response = original(request)
        slow(request)
        return response

    fake.handler = handler  # type: ignore[method-assign]
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, entity_input(D, "example.org"), time=time)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert fake.count(url) == 0


async def test_a_refused_destination_is_not_retried() -> None:
    from nightloom_sdk.http import DestinationNotAllowedError

    url = ARIN + "autnum/64496"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == url:
            raise DestinationNotAllowedError("refused")
        return fake.handler(request)

    fake = FakeRdap()
    http = create_http_client(META, transport=httpx.MockTransport(handler))
    ctx = ShuttleContext(META, http=http)
    with pytest.raises(ShuttleFailedError) as failed:
        await collect(RdapShuttle(bootstrap=BootstrapCache()), entity_input(AS, "AS64496"), ctx=ctx)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE


async def test_wildcard_input_and_a_domain_that_is_its_own_name_server() -> None:
    answer = fixture("domain_example_org")
    answer["nameservers"].append({"ldhName": "EXAMPLE.ORG"})
    fake = FakeRdap({ORG_REGISTRY + "domain/example.org": answer})
    entity = EntityInput(type=D, value="*.example.org", key="domain:*.example.org")
    found, ctx, _ = await run_shuttle(fake, entity)
    assert NOTE_SUBDOMAIN in ctx.notes
    assert ("example.org", "example.org") not in edges(found, R.HAS_NS)
    assert len(edges(found, R.HAS_NS)) == 2


async def test_cancellation_is_checked_before_requests() -> None:
    fake = FakeRdap()
    event = asyncio.Event()
    event.set()
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, entity_input(D, "example.org"), cancel_event=event)
    assert fake.requests == []


# --- privacy over every path ---------------------------------------------------------------


async def test_the_fixtures_really_contain_contact_data() -> None:
    """The privacy assertions above would be vacuous otherwise."""
    text = "".join(path.read_text() for path in FIXTURES.glob("*.json"))
    assert EMAIL.search(text) and PHONE.search(text)
    for value in PERSONAL:
        assert value.casefold() in text.casefold(), value


async def test_nothing_personal_even_when_every_role_is_an_organization() -> None:
    """Contacts of other roles are never used, even when they are organizations."""
    answer = copy.deepcopy(fixture("domain_example_net_registrar"))
    for entity in answer["entities"][2:]:
        entity["roles"] = ["technical", "billing", "abuse"]
        entity["vcardArray"][1] = [
            item for item in entity["vcardArray"][1] if item[0] != "kind"
        ] + [["kind", {}, "text", "org"]]
    fake = FakeRdap({REGISTRAR + "domain/EXAMPLE.NET": answer})
    found, ctx, _ = await run_shuttle(fake, entity_input(D, "example.net"))
    assert {o.value for o in entities(found, ORG)} == {
        "Example Registrar, Inc.",
        "Example Network Operations Ltd",
    }
    assert any("other roles (not used)" in note for note in ctx.notes)


@pytest.mark.network
async def test_real_rdap_servers() -> None:
    """Asks the real RDAP servers about example.org and 1.1.1.1. `make test-network` only."""
    for entity in (entity_input(D, "example.org"), entity_input(IP, "1.1.1.1")):
        ctx = ShuttleContext(META)
        found = await collect(RdapShuttle(bootstrap=BootstrapCache()), entity, ctx=ctx)
        assert_declared(META, found, entity)
        assert_private(found, ctx)
        if entity.type == D:
            assert entities(found, D)[0].properties, ctx.notes
            assert relationships(found, R.REGISTERED_BY), ctx.notes
        else:
            assert edges(found, R.WITHIN), ctx.notes
            assert relationships(found, R.OWNS), ctx.notes
