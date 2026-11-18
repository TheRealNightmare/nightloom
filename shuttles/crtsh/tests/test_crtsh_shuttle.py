"""The crt.sh Shuttle against a fake crt.sh: no network.

The search answers are realistic crt.sh JSON with fictional example.org data
(``fixtures/example_org.json``); the certificates crt.sh would serve are made
here with ``cryptography``, signed by a throwaway test CA.
"""

import asyncio
import ipaddress
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

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
from nightloom_shuttle_crtsh import CrtShShuttle
from nightloom_shuttle_crtsh import shuttle as shuttle_module
from nightloom_shuttle_crtsh.certificates import parse_certificate, parse_entries
from nightloom_shuttle_crtsh.shuttle import (
    DOWNLOAD_BUDGET_SECONDS,
    MAX_CERTIFICATES,
    MAX_SUBDOMAINS,
    NOTE_EXPIRED_EXCLUDED,
)

D = EntityType.DOMAIN
C = EntityType.CERTIFICATE
META = CrtShShuttle.meta
FIXTURE: dict[str, list[Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "example_org.json").read_text()
)
TOO_LARGE = b"[" + b" " * 6_000_000 + b"]"  # over the SDK's 5 MB response cap

# --- test certificates ----------------------------------------------------------------------

_KEY = ec.generate_private_key(ec.SECP256R1())
_CA_KEY = ec.generate_private_key(ec.SECP256R1())
_CA_NAME = x509.Name(
    [
        x509.NameAttribute(NameOID.COUNTRY_NAME, "XX"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Nightloom Test CA"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Nightloom Test Issuing CA"),
        x509.NameAttribute(NameOID.EMAIL_ADDRESS, "ca-admin@example.org"),
    ]
)


def make_certificate(
    common_name: str | None,
    dns_names: Sequence[str] = (),
    *,
    emails: Sequence[str] = (),
    ips: Sequence[str] = (),
    not_before: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    self_signed: bool = False,
    serial: int = 0x5AA,
    alt_names: bool = True,
) -> x509.Certificate:
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, common_name)] if common_name else []
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject if self_signed else _CA_NAME)
        .public_key(_KEY.public_key())
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_before + timedelta(days=90))
    )
    if alt_names:
        names: list[x509.GeneralName] = [x509.DNSName(n) for n in dns_names]
        names += [x509.RFC822Name(e) for e in emails]
        names += [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    return builder.sign(_KEY if self_signed else _CA_KEY, hashes.SHA256())


def pem(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(Encoding.PEM)


def fingerprint(certificate: x509.Certificate) -> str:
    """Computed independently of the Shuttle (cryptography's own fingerprint)."""
    return certificate.fingerprint(hashes.SHA256()).hex()


# The certificates behind the fixture's rows, by crt.sh ID.
CERTIFICATES = {
    9000000005: make_certificate("*.example.org", ["*.example.org", "example.org"], serial=0x05AA),
    9000000004: make_certificate(
        "www.example.org",
        ["example.org", "www.example.org", "xn--bcher-kva.example.org"],
        serial=0x04AA,
    ),
    9000000003: make_certificate(
        "mail.example.org",
        ["mail.example.org"],
        emails=["hostmaster@example.org"],
        ips=["192.0.2.25"],
        serial=0x03AA,
    ),
    9000000002: make_certificate(
        "shop.example.org",
        ["shop.example.org", "*.dev.example.org", "shop.example.net", "cdn.example.com"],
        serial=0x02AA,
    ),
    9000000001: make_certificate(
        "legacy.example.org",
        ["legacy.example.org", "old.legacy.example.org"],
        self_signed=True,
        serial=0x01AA,
    ),
}

# --- a fake crt.sh --------------------------------------------------------------------------


class FakeTime:
    """Fake monotonic time: sleeping (backoff, rate limit) moves it forward, instantly."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


Reply = list[Any] | bytes | httpx.Response


class FakeCrtSh:
    """Answers searches from ``searches`` (by query, and with ``exclude=expired``) and
    downloads from ``certificates``; records every request."""

    def __init__(
        self,
        searches: dict[str, Reply] | None = None,
        certificates: Mapping[int, bytes | x509.Certificate] | None = None,
        *,
        expired_excluded: dict[str, Reply] | None = None,
        on_download: Callable[[int], None] | None = None,
    ) -> None:
        self.searches = searches if searches is not None else default_searches()
        self.expired_excluded = expired_excluded or {}
        certs = CERTIFICATES if certificates is None else certificates
        self.certificates = {
            i: pem(c) if isinstance(c, x509.Certificate) else c for i, c in certs.items()
        }
        self.on_download = on_download
        self.queries: list[tuple[str, str | None]] = []
        self.downloads: list[int] = []
        self.urls: list[str] = []

    @staticmethod
    def respond(reply: Reply) -> httpx.Response:
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "crt.sh"
        self.urls.append(str(request.url))
        params = request.url.params
        if "d" in params:
            crtsh_id = int(params["d"])
            self.downloads.append(crtsh_id)
            if self.on_download is not None:
                self.on_download(crtsh_id)
            body = self.certificates.get(crtsh_id)
            return httpx.Response(404) if body is None else httpx.Response(200, content=body)
        assert params["output"] == "json"
        query, exclude = params["q"], params.get("exclude")
        self.queries.append((query, exclude))
        table = self.expired_excluded if exclude == "expired" else self.searches
        return self.respond(table.get(query, []))


def default_searches() -> dict[str, Reply]:
    return {"example.org": FIXTURE["exact"], "%.example.org": FIXTURE["wildcard"]}


async def run_shuttle(
    fake: FakeCrtSh,
    value: str = "example.org",
    *,
    time: FakeTime | None = None,
    cancel_event: asyncio.Event | None = None,
    entity: EntityInput | None = None,
) -> tuple[list[Finding], ShuttleContext, FakeTime]:
    time = time or FakeTime()
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(META, transport=httpx.MockTransport(fake.handler), limiter=limiter)
    ctx = ShuttleContext(META, http=http, cancel_event=cancel_event)
    entity = entity or entity_input(D, value)
    found = await collect(CrtShShuttle(sleep=time.sleep, clock=time), entity, ctx=ctx)
    assert_declared(META, found, entity)
    return found, ctx, time


def entities(found: list[Finding], entity_type: EntityType) -> list[EntityFinding]:
    return [f for f in found if isinstance(f, EntityFinding) and f.type == entity_type]


def relationships(found: list[Finding], relation: RelationType) -> list[RelationshipFinding]:
    return [f for f in found if isinstance(f, RelationshipFinding) and f.type == relation]


def edges(found: list[Finding], relation: RelationType) -> set[tuple[str, str]]:
    return {(f.source.value, f.target.value) for f in relationships(found, relation)}


# --- metadata and registration ------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "crtsh"
    assert META.title == "Certificate Transparency (crt.sh)"
    assert META.input_types == {D}
    assert META.output_entity_types == {D, C}
    assert META.output_relationship_types == {RelationType.HAS_SUBDOMAIN, RelationType.COVERS}
    assert (META.mode, META.sensitivity, META.network) == (
        Mode.PASSIVE,
        Sensitivity.INFRASTRUCTURE,
        True,
    )
    assert META.rate_limit is not None and META.rate_limit.per_second == pytest.approx(1 / 3)
    assert META.timeout_seconds == 300
    assert META.homepage == "https://crt.sh/"
    registry = ShuttleRegistry.discover()
    assert registry.get("crtsh") is CrtShShuttle
    assert CrtShShuttle in registry.list(D)
    assert CrtShShuttle not in registry.list(EntityType.IP_ADDRESS)
    CrtShShuttle.check_configuration()  # nothing to configure


# --- the happy path -------------------------------------------------------------------------


async def test_subdomains_and_certificates_from_the_fixture() -> None:
    fake = FakeCrtSh()
    found, ctx, time = await run_shuttle(fake)

    # Both queries, URL-encoded as crt.sh expects.
    assert fake.queries == [("example.org", None), ("%.example.org", None)]
    assert fake.urls[1] == "https://crt.sh/?q=%25.example.org&output=json"

    # Subdomains: in scope only, normalized, deduplicated, most recently certified first.
    domains = [f.value for f in entities(found, D)]
    assert domains == [
        "www.example.org",
        "xn--bcher-kva.example.org",
        "mail.example.org",
        "dev.example.org",
        "shop.example.org",
        "legacy.example.org",
        "old.legacy.example.org",
    ]
    subdomain_edges = relationships(found, RelationType.HAS_SUBDOMAIN)
    assert [(e.source.value, e.target.value) for e in subdomain_edges] == [
        ("example.org", d) for d in domains
    ]
    # Each Domain comes before the relationship that uses it.
    assert found.index(entities(found, D)[0]) < found.index(subdomain_edges[0])
    wildcard_notes = {e.target.value: e.note for e in subdomain_edges if e.note}
    assert wildcard_notes == {
        "dev.example.org": "wildcard: true (certificates name *.dev.example.org)"
    }

    # Every name that is not the domain or under it is gone: lookalikes, other
    # customers' names, email addresses and IP addresses.
    every_value = {f.value for f in found if isinstance(f, EntityFinding)} | {
        f.target.value for f in found if isinstance(f, RelationshipFinding)
    }
    for dropped in (
        "notexample.org",
        "example.org.attacker.example",
        "shop.example.net",
        "cdn.example.com",
        "192.0.2.25",
    ):
        assert dropped not in every_value
    assert not any("@" in value for value in every_value)
    assert all(v == "example.org" or v.endswith(".example.org") for v in domains)

    # Certificates: all five downloaded (fewer than 50), most recent first.
    assert fake.downloads == sorted(CERTIFICATES, reverse=True)
    certificates = entities(found, C)
    assert [c.value for c in certificates] == [
        fingerprint(CERTIFICATES[i]) for i in sorted(CERTIFICATES, reverse=True)
    ]
    covers = edges(found, RelationType.COVERS)
    by_id = {i: fingerprint(c) for i, c in CERTIFICATES.items()}
    assert covers == {
        (by_id[9000000005], "example.org"),
        (by_id[9000000004], "example.org"),
        (by_id[9000000004], "www.example.org"),
        (by_id[9000000004], "xn--bcher-kva.example.org"),
        (by_id[9000000003], "mail.example.org"),
        (by_id[9000000002], "shop.example.org"),
        (by_id[9000000002], "dev.example.org"),
        (by_id[9000000001], "legacy.example.org"),
        (by_id[9000000001], "old.legacy.example.org"),
    }
    wildcard_covers = {
        (e.source.value, e.target.value): e.note
        for e in relationships(found, RelationType.COVERS)
        if e.note
    }
    assert wildcard_covers == {
        (by_id[9000000005], "example.org"): "wildcard: true (certificates name *.example.org)",
        (by_id[9000000002], "dev.example.org"): (
            "wildcard: true (certificates name *.dev.example.org)"
        ),
    }

    # Notes: what was dropped and why, and the malformed rows.
    assert ctx.notes == (
        "Ignored 4 malformed crt.sh row(s).",
        "Dropped 7 name(s): 1 email addresses, 1 not valid domain names, 1 IP addresses, "
        "4 outside the searched domain.",
    )
    # 7 requests, at most one every 3 seconds.
    assert len(fake.urls) == 7
    assert time.now >= 3 * 6


async def test_certificate_properties_and_fingerprint() -> None:
    found, _, _ = await run_shuttle(FakeCrtSh())
    certificates = {c.properties["crtsh_id"]: c for c in entities(found, C) if c.properties}
    wildcard = certificates[9000000005]
    assert wildcard.value == fingerprint(CERTIFICATES[9000000005])
    assert wildcard.properties == {
        "issuer": "CN=Nightloom Test Issuing CA,O=Nightloom Test CA,C=XX",  # no email
        "subject_cn": "*.example.org",
        "serial_number": "5aa",
        "not_before": datetime(2026, 1, 1, tzinfo=UTC),
        "not_after": datetime(2026, 4, 1, tzinfo=UTC),
        "crtsh_id": 9000000005,
        "san_count": 2,
        "self_signed": False,
    }
    assert (wildcard.admiralty, wildcard.confidence) == ("B1", 0.95)
    assert certificates[9000000003].properties is not None
    assert certificates[9000000003].properties["san_count"] == 3  # DNS, email and IP SANs
    legacy = certificates[9000000001].properties
    assert legacy is not None
    assert legacy["self_signed"] is True
    assert legacy["issuer"] == "CN=legacy.example.org"
    # The properties pass the core model the worker validates them with.
    for certificate in certificates.values():
        stored = build_entity(C, certificate.value, certificate.properties).stored_properties()
        assert stored["crtsh_id"] == certificate.properties["crtsh_id"]  # type: ignore[index]
        assert build_entity(C, certificate.value).key == f"certificate:{certificate.value}"


def test_parse_certificate_der_pem_and_edge_cases() -> None:
    certificate = make_certificate("www.example.org", ["www.example.org"])
    der = certificate.public_bytes(Encoding.DER)
    assert parse_certificate(der) == parse_certificate(pem(certificate))
    assert parse_certificate(der).fingerprint == fingerprint(certificate)
    assert parse_certificate(der).names == ("www.example.org",)

    no_san = parse_certificate(pem(make_certificate("only-cn.example.org", alt_names=False)))
    assert (no_san.san_count, no_san.names) == (0, ("only-cn.example.org",))
    # An email address as the common name is person data: not kept.
    email_cn = parse_certificate(pem(make_certificate("someone@example.org", ["a.example.org"])))
    assert email_cn.subject_cn is None
    assert email_cn.names == ("a.example.org",)
    no_cn = parse_certificate(pem(make_certificate(None, ["b.example.org"])))
    assert no_cn.subject_cn is None
    # Same subject and issuer, but not signed by its own key: not self-signed.
    impostor = (
        x509.CertificateBuilder()
        .subject_name(_CA_NAME)
        .issuer_name(_CA_NAME)
        .public_key(_KEY.public_key())
        .serial_number(7)
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2026, 2, 1, tzinfo=UTC))
        .sign(_CA_KEY, hashes.SHA256())
    )
    assert parse_certificate(pem(impostor)).self_signed is False
    with pytest.raises(ValueError):
        parse_certificate(b"not a certificate")
    with pytest.raises(ValueError):
        parse_certificate(b"-----BEGIN CERTIFICATE-----\ngarbage\n-----END CERTIFICATE-----\n")


def test_parse_entries() -> None:
    entries, malformed = parse_entries(FIXTURE["wildcard"] + FIXTURE["exact"])
    assert malformed == 4
    assert [e.id for e in entries] == sorted(CERTIFICATES, reverse=True)  # unique, newest first
    assert entries[0].not_before == datetime(2026, 10, 1, tzinfo=UTC)
    assert list(entries[0].identities()) == ["*.example.org", "example.org", "*.example.org"]
    ties, _ = parse_entries(
        [
            {"id": 1, "name_value": "a.example.org", "not_before": "2026-01-01T00:00:00+00:00"},
            {"id": 2, "name_value": "b.example.org", "not_before": "2026-01-01T00:00:00Z"},
            {"id": 3, "name_value": "c.example.org", "not_before": "2026-01-01T06:00:00+06:00"},
        ]
    )
    assert [e.id for e in ties] == [3, 2, 1]
    assert all(e.not_before.tzinfo is UTC for e in ties)


async def test_names_are_deduplicated_across_rows_and_queries() -> None:
    row = FIXTURE["exact"][1]
    copies = [dict(row, id=row["id"] + n) for n in range(1, 4)]
    fake = FakeCrtSh({"example.org": [row, *copies], "%.example.org": [*copies, row]}, {})
    found, _, _ = await run_shuttle(fake)
    assert [f.value for f in entities(found, D)] == [
        "www.example.org",
        "xn--bcher-kva.example.org",
    ]
    assert len(relationships(found, RelationType.HAS_SUBDOMAIN)) == 2
    assert len(fake.downloads) == 3  # 4 unique IDs; 3 failures in a row stop the downloads


async def test_no_results() -> None:
    found, ctx, _ = await run_shuttle(FakeCrtSh({}, {}))
    assert found == []
    assert ctx.notes == ()


async def test_a_subdomain_input_keeps_only_names_under_it() -> None:
    fake = FakeCrtSh(
        {"%.legacy.example.org": FIXTURE["wildcard"], "legacy.example.org": []},
    )
    found, _, _ = await run_shuttle(fake, "legacy.example.org")
    assert fake.queries == [("legacy.example.org", None), ("%.legacy.example.org", None)]
    assert [f.value for f in entities(found, D)] == ["old.legacy.example.org"]
    assert edges(found, RelationType.COVERS) == {
        (fingerprint(CERTIFICATES[9000000001]), "legacy.example.org"),
        (fingerprint(CERTIFICATES[9000000001]), "old.legacy.example.org"),
    }


async def test_a_wildcard_input_searches_its_base_name() -> None:
    entity = EntityInput(type=D, value="*.example.org", key="domain:*.example.org")
    fake = FakeCrtSh()
    found, _, _ = await run_shuttle(fake, entity=entity)
    assert fake.queries == [("example.org", None), ("%.example.org", None)]
    assert {f.source.value for f in relationships(found, RelationType.HAS_SUBDOMAIN)} == {
        "*.example.org"
    }
    assert "*.example.org" in {f.target.value for f in relationships(found, RelationType.COVERS)}


@pytest.mark.parametrize("value", ["co.uk", "printer.local", "fileserver.corp", "x.internal"])
async def test_public_suffixes_and_internal_names_are_refused(value: str) -> None:
    fake = FakeCrtSh()
    with pytest.raises(InputRefusedError) as refused:
        await run_shuttle(fake, value)
    assert refused.value.reason is RefusalReason.NOT_REGISTRABLE
    assert fake.urls == []


async def test_an_unnormalizable_stored_value_is_refused() -> None:
    fake = FakeCrtSh()
    entity = EntityInput(type=D, value="bad..example.org", key="domain:bad..example.org")
    with pytest.raises(InputRefusedError):
        await run_shuttle(fake, entity=entity)
    assert fake.urls == []


# --- limits ---------------------------------------------------------------------------------


async def test_subdomains_are_capped() -> None:
    names = [f"host{n:04}.example.org" for n in range(MAX_SUBDOMAINS + 100)]
    row = {"id": 1, "name_value": "\n".join(names), "not_before": "2026-01-01T00:00:00"}
    found, ctx, _ = await run_shuttle(FakeCrtSh({"%.example.org": [row]}, {}))
    domains = entities(found, D)
    assert len(domains) == MAX_SUBDOMAINS == 2_000
    assert domains[0].value == "host0000.example.org"
    assert len(relationships(found, RelationType.HAS_SUBDOMAIN)) == 2_000
    assert (
        ctx.notes[0]
        == "Found 2,100 subdomains; kept the 2,000 most recently certified (the limit)."
    )


async def test_at_most_50_certificates_are_downloaded_most_recent_first() -> None:
    count = MAX_CERTIFICATES + 10
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "id": 100 + n,
            "name_value": f"n{n}.example.org",
            "not_before": (start + timedelta(days=n)).isoformat(),
        }
        for n in range(count)
    ]
    certificates = {
        100 + n: make_certificate(f"n{n}.example.org", [f"n{n}.example.org"], serial=n + 1)
        for n in range(count)
    }
    fake = FakeCrtSh({"%.example.org": rows}, certificates)
    found, ctx, time = await run_shuttle(fake)
    assert fake.downloads == [100 + n for n in range(count - 1, count - 1 - MAX_CERTIFICATES, -1)]
    assert len(entities(found, C)) == MAX_CERTIFICATES == 50
    assert len(entities(found, D)) == count  # older certificates still contribute names
    assert len(relationships(found, RelationType.COVERS)) == MAX_CERTIFICATES
    assert ctx.notes == ()
    # The rate limit held: 52 requests, one every 3 seconds.
    assert len(fake.urls) == 52
    assert time.now >= 3 * 51


async def test_covers_relationships_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shuttle_module, "MAX_COVERS", 3)
    found, ctx, _ = await run_shuttle(FakeCrtSh())
    assert len(relationships(found, RelationType.COVERS)) == 3
    assert len(entities(found, C)) == 5
    assert "Stopped adding COVERS relationships at 3 (the limit)." in ctx.notes


async def test_downloads_stop_at_the_time_budget() -> None:
    time = FakeTime()
    fake = FakeCrtSh()
    slow = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if "q" in request.url.params:
            time.now += DOWNLOAD_BUDGET_SECONDS / 2  # a slow crt.sh
        return slow(request)

    fake.handler = handler  # type: ignore[method-assign]
    found, ctx, _ = await run_shuttle(fake, time=time)
    assert fake.downloads == []
    assert len(entities(found, D)) == 7  # the names are still there
    assert entities(found, C) == []
    assert (
        "Reached the time limit after 0 of 5 certificate downloads; the rest contribute names only."
        in ctx.notes
    )


async def test_failed_downloads_are_skipped_then_stop() -> None:
    certificates: dict[int, bytes | x509.Certificate] = dict(CERTIFICATES)
    certificates[9000000005] = b"not a certificate"
    del certificates[9000000004]
    found, ctx, _ = await run_shuttle(FakeCrtSh(certificates=certificates))
    assert len(entities(found, C)) == 3
    assert "2 certificate download(s) failed; those contribute names only." in ctx.notes

    fake = FakeCrtSh(certificates={})
    found, ctx, _ = await run_shuttle(fake)
    assert len(fake.downloads) == 3  # three failures in a row: crt.sh is not serving them
    assert entities(found, C) == []
    assert "5 certificate download(s) failed; those contribute names only." in ctx.notes


# --- large domains and an unwell crt.sh ---------------------------------------------------


async def test_too_large_is_asked_again_without_expired_certificates() -> None:
    fake = FakeCrtSh(
        {"example.org": [], "%.example.org": TOO_LARGE},
        expired_excluded={"%.example.org": FIXTURE["wildcard"]},
    )
    found, ctx, _ = await run_shuttle(fake)
    assert fake.queries == [
        ("example.org", None),
        ("%.example.org", None),
        ("%.example.org", "expired"),
    ]
    assert fake.urls[2] == "https://crt.sh/?q=%25.example.org&output=json&exclude=expired"
    assert NOTE_EXPIRED_EXCLUDED in ctx.notes
    assert ctx.notes[0] == "Expired certificates excluded (result too large)."
    assert len(entities(found, D)) == 7


async def test_still_too_large_fails_with_a_clear_message() -> None:
    fake = FakeCrtSh(
        {"example.org": [], "%.example.org": TOO_LARGE},
        expired_excluded={"%.example.org": TOO_LARGE},
    )
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake)
    assert failed.value.reason is FailureReason.RESULT_TOO_LARGE
    assert "narrower" in failed.value.message
    assert "subdomains" in failed.value.message
    assert fake.downloads == []


@pytest.mark.parametrize("status", [502, 503])
async def test_server_errors_are_retried_then_fail(status: int) -> None:
    fake = FakeCrtSh({"example.org": httpx.Response(status)})
    time = FakeTime()
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake, time=time)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert "Try again later" in failed.value.message
    assert fake.queries == [("example.org", None)] * 4  # the first try and 3 retries
    assert time.now >= 2 + 5 + 10  # backed off between them


async def test_a_flaky_crtsh_recovers() -> None:
    replies = iter([httpx.Response(503), httpx.Response(502)])
    fake = FakeCrtSh()
    inner = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        reply = next(replies, None)
        return reply if reply is not None else inner(request)

    fake.handler = handler  # type: ignore[method-assign]
    found, _, _ = await run_shuttle(fake)
    assert len(entities(found, C)) == 5


async def test_an_unreadable_answer_fails_with_a_clear_message() -> None:
    fake = FakeCrtSh({"example.org": httpx.Response(200, content=b"<html>oops</html>")})
    with pytest.raises(ShuttleFailedError) as failed:
        await run_shuttle(fake)
    assert failed.value.reason is FailureReason.SOURCE_INVALID_RESPONSE


# --- cancellation ---------------------------------------------------------------------------


async def test_cancellation_stops_the_downloads() -> None:
    event = asyncio.Event()

    def cancel_after_two(crtsh_id: int) -> None:
        if crtsh_id == 9000000004:
            event.set()

    fake = FakeCrtSh(on_download=cancel_after_two)
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, cancel_event=event)
    assert fake.downloads == [9000000005, 9000000004]


async def test_cancellation_before_any_request() -> None:
    event = asyncio.Event()
    event.set()
    fake = FakeCrtSh()
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, cancel_event=event)
    assert fake.urls == []


# --- the real thing (opt-in) ----------------------------------------------------------------


@pytest.mark.network
async def test_real_crtsh() -> None:
    """Asks the real crt.sh about example.org. Run with `make test-network`; never in CI."""
    entity = entity_input(D, "example.org")
    ctx = ShuttleContext(META)
    found = await collect(CrtShShuttle(), entity, ctx=ctx)
    assert_declared(META, found, entity)
    for domain in entities(found, D):
        assert domain.value.endswith(".example.org")
    certificates = entities(found, C)
    assert certificates, ctx.notes
    assert all(len(c.value) == 64 for c in certificates)
    assert relationships(found, RelationType.COVERS)
