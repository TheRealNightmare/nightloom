"""The HTTP headers and TLS Shuttle against a fake site: no network."""

import hashlib
import ssl
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from nightloom_core.entities import EntityType, build_entity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    FailureReason,
    Finding,
    Mode,
    RelationshipFinding,
    ShuttleFailedError,
)
from nightloom_sdk.http import DestinationNotAllowedError
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_active import HttpHeadersShuttle
from nightloom_shuttle_active.certificates import parse_der
from nightloom_shuttle_active.headers import (
    MAX_REDIRECTS,
    NOTE_HTTP_UNREACHABLE,
    NOTE_HTTPS_UNREACHABLE,
    NOTE_NO_CONNECTION_DETAILS,
    NOTE_NOT_PUBLIC,
    NOTE_REDIRECT_ELSEWHERE,
    NOTE_TLS_INVALID,
    NOTE_TOO_MANY_REDIRECTS,
    SECURITY_HEADERS,
    product_of,
)

D = EntityType.DOMAIN
R = RelationType
META = HttpHeadersShuttle.meta
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
HOST = "shop.example.org"


def make_certificate(cn: str = HOST, *, email: bool = True) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    attributes = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if email:
        attributes.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, "admin@example.org"))
    name = x509.Name(attributes)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(0x1234ABCD)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=89))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(HOST), x509.DNSName("example.org")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(Encoding.DER)


CERT = make_certificate()


class FakeSsl:
    def version(self) -> str:
        return "TLSv1.3"

    def getpeercert(self, binary_form: bool = False) -> bytes:
        assert binary_form
        return CERT


class FakeStream:
    def __init__(self, tls: bool = True, address: tuple[Any, ...] = ("93.184.215.14", 443)):
        self.tls = tls
        self.address = address

    def get_extra_info(self, name: str) -> Any:
        if name == "server_addr":
            return self.address
        if name == "ssl_object":
            return FakeSsl() if self.tls else None
        return None


SECURE = {
    "server": "nginx/1.25.3 (Ubuntu)",
    "x-powered-by": "PHP/8.2.12",
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "content-security-policy": "default-src 'self'",
    "x-content-type-options": "nosniff",
}


class FakeSite:
    """Answers by scheme and path; records every request (nothing leaves the test)."""

    def __init__(self, replies: dict[str, httpx.Response | Exception]) -> None:
        self.replies = replies
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        assert request.method == "GET"
        reply = self.replies.get(url)
        if reply is None:
            return httpx.Response(404)
        if isinstance(reply, Exception):
            raise reply
        return reply


def ok(
    headers: dict[str, str] | None = None, *, stream: FakeStream | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        headers=headers if headers is not None else SECURE,
        extensions={"network_stream": stream or FakeStream()},
        content=b"<html>body is never read</html>",
    )


def redirect(location: str, status: int = 301) -> httpx.Response:
    return httpx.Response(
        status, headers={"location": location}, extensions={"network_stream": FakeStream()}
    )


async def run(
    replies: dict[str, httpx.Response | Exception], host: str = HOST
) -> tuple[list[Finding], tuple[str, ...], FakeSite]:
    site = FakeSite(replies)
    async with httpx.AsyncClient(transport=httpx.MockTransport(site.handler)) as client:
        shuttle = HttpHeadersShuttle(http=client, now=lambda: NOW)
        ctx = fake_context(META)
        entity = entity_input(D, host)
        findings = await collect(shuttle, entity, ctx=ctx)
        assert_declared(META, findings, entity)
        for finding in findings:
            if isinstance(finding, EntityFinding):
                build_entity(finding.type, finding.value, finding.properties)
    return findings, ctx.notes, site


def entities(findings: list[Finding], kind: EntityType) -> dict[str, dict[str, Any]]:
    return {
        f.value: f.properties or {}
        for f in findings
        if isinstance(f, EntityFinding) and f.type == kind
    }


def relations(findings: list[Finding]) -> set[tuple[str, str, str]]:
    return {
        (f.type.value, f.source.value, f.target.value)
        for f in findings
        if isinstance(f, RelationshipFinding)
    }


def test_meta_is_active_and_never_cached() -> None:
    assert META.mode == Mode.ACTIVE
    assert META.cache_ttl_seconds == 0
    assert META.input_types == frozenset({D})


async def test_a_secure_site() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/": ok(),
            f"http://{HOST}/": redirect(f"https://{HOST}/"),
        }
    )
    assert site.requests == [f"https://{HOST}/", f"http://{HOST}/"]
    assert notes == ()
    domain = entities(findings, D)[HOST]
    assert domain == {
        "http_checked_at": NOW,
        "https_valid": True,
        "http_redirects_to_https": True,
        "https_status": 200,
        "security_headers": [
            "strict-transport-security",
            "content-security-policy",
            "x-content-type-options",
        ],
        "missing_security_headers": [
            "x-frame-options",
            "referrer-policy",
            "permissions-policy",
            "cross-origin-opener-policy",
        ],
        "tls_version": "TLSv1.3",
        "server": "nginx/1.25.3 (Ubuntu)",
        "powered_by": "PHP/8.2.12",
        "hsts": "max-age=63072000; includeSubDomains",
    }
    assert set(entities(findings, EntityType.TECHNOLOGY)) == {"nginx", "php"}
    service = "93.184.215.14:443/tcp"
    assert entities(findings, EntityType.SERVICE) == {
        service: {"product": "nginx", "version": "1.25.3"}
    }
    fingerprint = hashlib.sha256(CERT).hexdigest()
    certificate = entities(findings, EntityType.CERTIFICATE)[fingerprint]
    assert certificate["subject_cn"] == HOST
    assert "admin@example.org" not in certificate["issuer"]
    assert certificate["self_signed"] is True
    assert certificate["san_count"] == 2
    assert certificate["serial_number"] == "1234abcd"
    assert relations(findings) == {
        ("USES", HOST, "nginx"),
        ("USES", HOST, "php"),
        ("USES", service, "nginx"),
        ("USES", service, "php"),
        ("RESOLVES_TO", HOST, "93.184.215.14"),
        ("RUNS_ON", service, "93.184.215.14"),
        ("COVERS", fingerprint, HOST),
        ("PRESENTS", service, fingerprint),
    }


async def test_same_host_redirects_are_followed_others_are_not() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/": redirect("/en/", 302),
            f"https://{HOST}/en/": redirect("https://login.example.net/"),
            f"http://{HOST}/": ok({}),
        }
    )
    assert site.requests == [f"https://{HOST}/", f"https://{HOST}/en/", f"http://{HOST}/"]
    assert NOTE_REDIRECT_ELSEWHERE.format(target="login.example.net") in notes
    domain = entities(findings, D)[HOST]
    assert domain["https_status"] == 301
    assert domain["http_redirects_to_https"] is False
    # The connection details are those of the first answer.
    assert "93.184.215.14" in entities(findings, EntityType.IP_ADDRESS)


async def test_redirect_loops_stop() -> None:
    _, notes, site = await run({f"https://{HOST}/": redirect(f"https://{HOST}/")})
    assert site.requests.count(f"https://{HOST}/") == MAX_REDIRECTS + 1
    assert NOTE_TOO_MANY_REDIRECTS.format(count=MAX_REDIRECTS) in notes


async def test_an_invalid_certificate_is_recorded_not_read() -> None:
    def tls_error() -> httpx.ConnectError:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLError as error:
            failure = httpx.ConnectError("TLS")
            failure.__cause__ = error
            return failure

    findings, notes, _ = await run(
        {f"https://{HOST}/": tls_error(), f"http://{HOST}/": ok({"server": "Apache"})}
    )
    assert NOTE_TLS_INVALID.format(host=HOST) in notes
    domain = entities(findings, D)[HOST]
    assert domain["https_valid"] is False
    assert domain["http_redirects_to_https"] is False
    assert "https_status" not in domain
    assert entities(findings, EntityType.CERTIFICATE) == {}


async def test_plain_http_only_and_missing_connection_details() -> None:
    findings, notes, _ = await run(
        {
            f"https://{HOST}/": httpx.ConnectError("refused"),
            f"http://{HOST}/": redirect(f"https://{HOST}/"),
        }
    )
    assert NOTE_HTTPS_UNREACHABLE.format(host=HOST) in notes
    assert entities(findings, D)[HOST]["http_redirects_to_https"] is True
    headers_only = httpx.Response(200, headers={"server": "cloudflare"})
    findings, notes, _ = await run(
        {f"https://{HOST}/": headers_only, f"http://{HOST}/": httpx.ConnectTimeout("slow")}
    )
    assert NOTE_NO_CONNECTION_DETAILS in notes
    assert NOTE_HTTP_UNREACHABLE.format(host=HOST) in notes
    assert entities(findings, EntityType.SERVICE) == {}
    assert set(entities(findings, EntityType.TECHNOLOGY)) == {"cloudflare"}


async def test_unreachable_everywhere_fails() -> None:
    with pytest.raises(ShuttleFailedError) as raised:
        await run(
            {
                f"https://{HOST}/": httpx.ConnectError("down"),
                f"http://{HOST}/": httpx.ConnectError("down"),
            }
        )
    assert raised.value.reason == FailureReason.SOURCE_UNAVAILABLE


async def test_a_private_address_is_never_contacted() -> None:
    findings, notes, site = await run({f"https://{HOST}/": DestinationNotAllowedError("private")})
    assert notes == (NOTE_NOT_PUBLIC.format(host=HOST),)
    assert findings == []
    assert site.requests == [f"https://{HOST}/"]


async def test_ipv6_service() -> None:
    findings, _, _ = await run(
        {
            f"https://{HOST}/": ok(stream=FakeStream(address=("2001:4860::1", 443, 0, 0))),
            f"http://{HOST}/": redirect(f"https://{HOST}/"),
        }
    )
    assert set(entities(findings, EntityType.SERVICE)) == {"[2001:4860::1]:443/tcp"}


def test_products() -> None:
    assert product_of("nginx/1.25.3 (Ubuntu)") == ("nginx", "1.25.3")
    assert product_of("Microsoft-IIS/10.0") == ("microsoft-iis", "10.0")
    assert product_of("cloudflare") == ("cloudflare", None)
    assert product_of("Apache/<script>") == ("apache", None)
    for nothing in (None, "", "   ", "(none)", "1.2.3", "bad\x00value"):
        assert product_of(nothing) is None


def test_parse_der_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_der(b"not a certificate")
    parsed = parse_der(make_certificate(email=False))
    assert parsed.self_signed is True
    assert len(SECURITY_HEADERS) == 7
