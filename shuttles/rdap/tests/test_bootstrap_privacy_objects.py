"""Unit tests: the bootstrap lookups, the privacy rules, RDAP field parsing and Retry-After."""

import asyncio
import ipaddress
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nightloom_sdk import JobCancelledError
from nightloom_shuttle_rdap import objects
from nightloom_shuttle_rdap.bootstrap import (
    BootstrapCache,
    BootstrapError,
    BootstrapFile,
    Registry,
    Service,
)
from nightloom_shuttle_rdap.client import retry_after_seconds
from nightloom_shuttle_rdap.privacy import (
    MAX_CONTACTS,
    REDACTION_MARKERS,
    SkipReason,
    extract_contacts,
    is_redacted,
)

FIXTURES = Path(__file__).parent / "fixtures"


def bootstrap(registry: Registry) -> BootstrapFile:
    name = f"bootstrap_{registry.value.removesuffix('.json')}"
    return BootstrapFile.parse(registry, json.loads((FIXTURES / f"{name}.json").read_text()))


# --- bootstrap ----------------------------------------------------------------------------


def test_domain_lookup_prefers_the_longest_suffix_and_https() -> None:
    dns = bootstrap(Registry.DNS)
    assert dns.for_domain("example.org") == Service("https://rdap.org-registry.example/rdap/")
    assert dns.for_domain("WWW.EXAMPLE.NET.") == Service("https://rdap.net-registry.example/v1/")
    assert dns.for_domain("shop.co.example") == Service("https://rdap.co-registry.example/")
    assert dns.for_domain("example.de") is None
    assert dns.for_domain("example.ftp-only") is None  # no http(s) URL: entry skipped


def test_network_lookup_takes_the_longest_prefix() -> None:
    ipv4, ipv6 = bootstrap(Registry.IPV4), bootstrap(Registry.IPV6)
    arin = ipv4.for_network(ipaddress.ip_network("192.0.2.0/25"))
    assert arin is not None and arin.rir == "ARIN" and arin.base_url.startswith("https://")
    lacnic = ipv4.for_network(ipaddress.ip_network("192.0.0.1/32"))
    assert lacnic is not None and lacnic.rir == "LACNIC"
    assert ipv4.for_network(ipaddress.ip_network("192.0.0.0/7")) is None  # wider than any entry
    ripe = ipv6.for_network(ipaddress.ip_network("2001:db8:1::/48"))
    assert ripe is not None and ripe.rir == "RIPE NCC"
    apnic = ipv6.for_network(ipaddress.ip_network("2001:db9::/32"))
    assert apnic is not None and apnic.rir == "APNIC"
    assert ipv6.for_network(ipaddress.ip_network("192.0.2.0/24")) is None  # other version


def test_asn_lookup_takes_the_smallest_range() -> None:
    asn = bootstrap(Registry.ASN)
    assert (asn.for_asn(64496) or Service("")).rir == "ARIN"
    assert (asn.for_asn(64511) or Service("")).rir == "APNIC"
    assert (asn.for_asn(65540) or Service("")).rir == "RIPE NCC"
    assert asn.for_asn(1) is None


def test_service_helpers() -> None:
    service = Service("https://rdap.registry.example/v1/")
    assert service.host == "rdap.registry.example"
    assert service.rir is None
    assert (
        service.url("domain/example.org") == "https://rdap.registry.example/v1/domain/example.org"
    )


@pytest.mark.parametrize(
    "data",
    [{}, {"services": "nope"}, {"services": [[["org"], ["https://x.example/"]]] * 10_001}],
)
def test_malformed_bootstrap_files(data: dict[str, Any]) -> None:
    with pytest.raises(BootstrapError):
        BootstrapFile.parse(Registry.DNS, data)


def test_malformed_entries_are_skipped() -> None:
    parsed = BootstrapFile.parse(
        Registry.ASN,
        {
            "services": [
                [["1-10", 5, "x-y", "10-1", "4294967296"], ["https://rdap.arin.net/registry/"]],
                [["20"], [None, "mailto:rdap@example.org"]],
                "not a list",
            ]
        },
    )
    assert [(first, last) for first, last, _ in parsed.asns] == [(1, 10)]


async def test_cache_is_per_file_and_never_swallows_cancellation() -> None:
    cache = BootstrapCache()
    fetched: list[str] = []

    async def fetch(url: str) -> dict[str, Any]:
        fetched.append(url)
        return {"services": []}

    await cache.get(Registry.DNS, fetch)
    await cache.get(Registry.ASN, fetch)
    await cache.get(Registry.DNS, fetch)
    assert fetched == [Registry.DNS.url, Registry.ASN.url]

    cache = BootstrapCache(ttl=0)
    await cache.get(Registry.DNS, fetch)

    async def cancelled(url: str) -> dict[str, Any]:
        raise JobCancelledError("cancelled")

    with pytest.raises(JobCancelledError):  # even with a stale copy to fall back on
        await cache.get(Registry.DNS, cancelled)
    cache.clear()
    with pytest.raises(JobCancelledError):
        await cache.get(Registry.DNS, cancelled)


def test_cache_locks_are_per_event_loop() -> None:
    """The worker runs every job in a fresh event loop; the cache must work in each."""
    cache = BootstrapCache(ttl=0)

    async def fetch(url: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"services": []}

    for _ in range(2):
        asyncio.run(cache.get(Registry.DNS, fetch))
    assert cache.fetches == 2


# --- privacy ------------------------------------------------------------------------------


def vcard(*props: list[Any]) -> list[Any]:
    return ["vcard", [["version", {}, "text", "4.0"], *props]]


def contact(roles: list[str], name: str | None, kind: str | None = None) -> dict[str, Any]:
    props: list[list[Any]] = []
    if name is not None:
        props.append(["fn", {}, "text", name])
    if kind is not None:
        props.append(["kind", {}, "text", kind])
    props.append(["email", {}, "text", "someone@example.org"])
    return {"objectClassName": "entity", "roles": roles, "vcardArray": vcard(*props)}


@pytest.mark.parametrize("marker", REDACTION_MARKERS)
def test_every_redaction_marker_matches_in_any_case(marker: str) -> None:
    assert is_redacted(f"Name {marker.upper()} here")
    assert is_redacted(marker.title())


@pytest.mark.parametrize(
    "name", ["Example Hosting LLC", "Example Private Limited", "Registry Services, Inc."]
)
def test_ordinary_organization_names_are_not_redacted(name: str) -> None:
    assert not is_redacted(name)


def test_roles_and_kinds() -> None:
    contacts = extract_contacts(
        {
            "entities": [
                contact(["registrar"], "Example Registrar, Inc."),  # no kind: fine for a registrar
                contact(["registrant"], "Example Holdings"),  # no kind: maybe a person, skipped
                contact(["registrant"], "Example Org Ltd", "ORG"),
                contact(["Technical"], "Example NOC", "org"),
                contact(["registrar"], "Someone", "individual"),
                "not an entity",
            ]
        }
    )
    assert contacts.organizations == {
        "registrar": "Example Registrar, Inc.",
        "registrant": "Example Org Ltd",
    }
    assert contacts.skipped == {
        SkipReason.NOT_AN_ORGANIZATION: 2,
        SkipReason.UNUSED_ROLE: 1,
    }


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        (None, SkipReason.NO_NAME),
        ("   ", SkipReason.NO_NAME),
        ("x" * 201, SkipReason.NO_NAME),
        ("noc@example.org", SkipReason.NO_NAME),
        ("https://registrant.example", SkipReason.NO_NAME),
        ("+1 (555) 555-0100", SkipReason.NO_NAME),
        ("Redacted For Privacy", SkipReason.REDACTED),
        ("Contact Privacy Inc. Customer 1234", SkipReason.REDACTED),
    ],
)
def test_unusable_names(name: str | None, reason: SkipReason) -> None:
    contacts = extract_contacts({"entities": [contact(["registrant"], name, "org")]})
    assert contacts.organizations == {}
    assert contacts.skipped == {reason: 1}


def test_registrar_iana_id() -> None:
    def registrar(public_ids: object) -> dict[str, Any]:
        entity = contact(["registrar"], "Example Registrar")
        entity["publicIds"] = public_ids
        return entity

    def iana_id(public_ids: object) -> int | None:
        return extract_contacts({"entities": [registrar(public_ids)]}).registrar_iana_id

    assert iana_id([{"type": "IANA Registrar ID", "identifier": "292"}]) == 292
    assert iana_id([{"type": "iana registrar id", "identifier": 7}]) == 7
    assert iana_id([{"type": "other", "identifier": "1"}, "x"]) is None
    assert iana_id([{"type": "IANA Registrar ID", "identifier": "not-a-number"}]) is None
    assert iana_id([{"type": "IANA Registrar ID", "identifier": "0"}]) is None
    assert iana_id("not a list") is None


def test_nested_and_malformed_contacts() -> None:
    nested = contact(["registrar"], "Example Registrar")
    nested["entities"] = [contact(["registrant"], "Nested Org", "org")]  # nested: never used
    contacts = extract_contacts(
        {
            "entities": [
                nested,
                {"roles": "registrant", "vcardArray": vcard(["fn", {}, "text", "X"])},
                {"roles": ["registrant"], "vcardArray": ["vcard"]},
                {"roles": ["registrant"], "vcardArray": vcard(["fn", {}, "text"])},
            ]
        }
    )
    assert contacts.organizations == {"registrar": "Example Registrar"}
    assert contacts.skipped == {
        SkipReason.UNUSED_ROLE: 2,
        SkipReason.NOT_AN_ORGANIZATION: 2,
    }


def test_contacts_are_capped() -> None:
    many = [contact(["technical"], "Example NOC", "org")] * (MAX_CONTACTS + 10)
    assert extract_contacts({"entities": many}).skipped[SkipReason.UNUSED_ROLE] == MAX_CONTACTS
    assert extract_contacts({"entities": "not a list"}).skipped == {}


# --- RDAP fields --------------------------------------------------------------------------


def test_range_to_cidrs() -> None:
    def cidrs(start: str, end: str) -> list[str]:
        return [n.with_prefixlen for n in objects.range_to_cidrs(start, end)]

    assert cidrs("192.0.2.0", "192.0.2.255") == ["192.0.2.0/24"]
    assert cidrs("192.0.2.0", "192.0.2.95") == ["192.0.2.0/26", "192.0.2.64/27"]
    assert cidrs("192.0.2.7", "192.0.2.9") == ["192.0.2.7/32", "192.0.2.8/31"]
    assert cidrs("2001:db8::", "2001:db8::1:0") == ["2001:db8::/112", "2001:db8::1:0/128"]
    assert cidrs("192.0.2.9", "192.0.2.7") == []  # reversed
    assert cidrs("192.0.2.0", "2001:db8::") == []  # mixed versions
    assert cidrs("2001:db8::", "192.0.2.0") == []
    assert cidrs("nonsense", "192.0.2.0") == []


def test_networks_prefer_cidr0() -> None:
    blocks, capped = objects.networks(
        {
            "cidr0_cidrs": [
                {"v6prefix": "2001:db8::", "length": 32},
                {"v4prefix": "192.0.2.0", "length": 24},
                {"v4prefix": "192.0.2.0", "length": 24},  # duplicate
                {"v4prefix": "192.0.2.0", "length": True},
                {"v4prefix": "bad", "length": 24},
                {"length": 24},
                "x",
            ],
            "startAddress": "198.51.100.0",
            "endAddress": "198.51.100.255",
        }
    )
    assert [b.with_prefixlen for b in blocks] == ["2001:db8::/32", "192.0.2.0/24"]
    assert not capped
    assert objects.networks({"cidr0_cidrs": "x"}) == ([], False)


def test_events_and_dates() -> None:
    found = objects.events(
        {
            "events": [
                {"eventAction": "Registration", "eventDate": "2020-01-02T03:04:05.123Z"},
                {"eventAction": "registration", "eventDate": "1990-01-01T00:00:00Z"},  # first wins
                {"eventAction": "expiration", "eventDate": "not a date"},
                {"eventAction": "last changed", "eventDate": "2021-06-01T12:00:00+02:00"},
                {"eventAction": "transfer", "eventDate": "2021-06-01T12:00:00Z"},
                {"eventAction": 5},
                "x",
            ]
        }
    )
    assert found == {
        "registered": datetime(2020, 1, 2, 3, 4, 5, 123000, tzinfo=UTC),
        "last_changed": datetime(2021, 6, 1, 10, 0, tzinfo=UTC),
    }
    assert objects.events({"events": "x"}) == {}
    assert objects.parse_date(20200101) is None


def test_status_dnssec_nameservers_country_and_text() -> None:
    assert objects.statuses({"status": ["Active", "active", "", 3, "x" * 201]}) == ["active"]
    assert objects.statuses({"status": "active"}) == []
    assert objects.statuses({"status": [f"status {i}" for i in range(40)]})[-1] == "status 29"
    assert objects.dnssec_signed({"secureDNS": {"delegationSigned": True}}) is True
    assert objects.dnssec_signed({"secureDNS": {"delegationSigned": "yes"}}) is None
    assert objects.nameservers({"nameservers": "x"}) == []
    assert objects.nameservers({"nameservers": ["x", {"ldhName": "."}, {"ldhName": 1}]}) == []
    many = {"nameservers": [{"ldhName": f"ns{i}.example.net"} for i in range(30)]}
    assert len(objects.nameservers(many)) == objects.MAX_NAMESERVERS
    assert objects.country({"country": "nl"}) == "NL"
    assert objects.country({"country": "N1"}) is None
    assert objects.country({"country": "NLD"}) is None
    assert objects.text("  a \n b  ") == "a b"
    assert objects.text("   ") is None
    assert objects.object_class({"objectClassName": " Domain "}) == "domain"
    assert objects.object_class({"objectClassName": 1}) is None


def test_related_link() -> None:
    url = "https://rdap.registry.example/domain/example.net"

    def link(**fields: Any) -> dict[str, Any]:
        return {"links": [{"rel": "related", **fields}]}

    assert objects.related_link({"links": "x"}, url) is None
    assert objects.related_link(link(href=url), url) is None  # itself
    assert objects.related_link(link(href="ftp://r.example/x"), url) is None
    assert objects.related_link(link(href="https:///no-host"), url) is None
    assert objects.related_link(link(href="https://r.example/a", type="text/html"), url) is None
    assert objects.related_link(link(href=5), url) is None
    assert (
        objects.related_link({"links": ["x", {"rel": "self", "href": "https://r.example"}]}, url)
        is None
    )
    assert (
        objects.related_link(link(href=" https://r.example/domain/x "), url)
        == "https://r.example/domain/x"
    )


# --- Retry-After --------------------------------------------------------------------------


def test_retry_after() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    assert retry_after_seconds(None) is None
    assert retry_after_seconds(" 12 ") == 12.0
    assert retry_after_seconds("Wed, 18 Nov 2026 12:00:20 GMT", now=now) == 20.0
    assert retry_after_seconds("Wed, 18 Nov 2026 11:00:00 GMT", now=now) == 0.0  # in the past
    assert retry_after_seconds("2026-11-18T12:00:20", now=now) is None
    assert retry_after_seconds("soon") is None
    assert retry_after_seconds("Wed, 18 Nov 2026 12:00:20", now=now) == 20.0  # no zone: UTC
    assert retry_after_seconds("Wed, 18 Nov 2026 12:00:20 GMT") is not None  # real clock
