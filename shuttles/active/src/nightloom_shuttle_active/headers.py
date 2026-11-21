"""The HTTP headers and TLS Shuttle: what a verified site answers over HTTPS.

**Active**: it connects to the site itself, so it runs only on domains the
case has verified (the domain or a subdomain of a verified domain); the API
and the worker check that before a run. It sends two ordinary requests, the
way a browser opening the site would, through the Shuttles' guarded HTTP
client (public addresses only, checked when connecting):

- ``GET https://<domain>/``, following at most :data:`MAX_REDIRECTS`
  redirects **on the same host** only (a redirect elsewhere is recorded,
  never followed: the other host is not verified). Only the headers are
  read, never the body.
- ``GET http://<domain>/`` without following anything, to see whether plain
  HTTP redirects to HTTPS.

It yields the input Domain with ``http_checked_at``, ``https_status``,
``https_valid``, ``http_redirects_to_https``, ``tls_version``, ``server``,
``powered_by``, ``hsts`` and which of :data:`SECURITY_HEADERS` were sent
(``security_headers``) or not (``missing_security_headers``). From the HTTPS
connection: the address it connected to (IPAddress, ``RESOLVES_TO``), the
service ``<ip>:<port>/tcp`` (``RUNS_ON``; ``product`` and ``version`` from
the ``Server`` header), the certificate it presented (Certificate by SHA-256
fingerprint, ``PRESENTS``, and ``COVERS`` the domain when it was valid for
it), and the software the ``Server`` and ``X-Powered-By`` headers name as
Technologies (``USES``, from the domain and the service).

A certificate that does not verify is not read (nothing is sent over such a
connection): ``https_valid`` is false and a note says why.

Provenance **A2** (confidence 0.9): observed directly, now, from Nightloom's
own vantage point.
"""

import ipaddress
import re
import ssl
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from nightloom_core.entities import SECURITY_HEADERS_MAX, EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, normalize
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    FailureReason,
    Finding,
    Mode,
    RateLimit,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleFailedError,
    ShuttleMeta,
)
from nightloom_sdk.http import DestinationNotAllowedError
from nightloom_shuttle_active.certificates import PresentedCertificate, parse_der

DOMAIN = EntityType.DOMAIN
IP = EntityType.IP_ADDRESS
SERVICE = EntityType.SERVICE
CERTIFICATE = EntityType.CERTIFICATE
TECHNOLOGY = EntityType.TECHNOLOGY

TIMEOUT_SECONDS = 60
MAX_REDIRECTS = 3
MAX_HEADER_LENGTH = 200
MAX_HSTS_LENGTH = 1024
ADMIRALTY, CONFIDENCE = "A2", 0.9

# The response headers whose presence is recorded, by lower-case name.
SECURITY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
)
assert len(SECURITY_HEADERS) <= SECURITY_HEADERS_MAX  # noqa: S101 - a constant's bound

_PRODUCT = re.compile(r"[A-Za-z][A-Za-z0-9._+-]{0,39}")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}")

NOTE_NOT_PUBLIC = (
    "{host} resolves to an address Shuttles may not reach (not public). Nothing was sent."
)
NOTE_TLS_INVALID = (
    "The HTTPS certificate of {host} could not be verified, so the site's headers were not read."
)
NOTE_HTTPS_UNREACHABLE = "{host} did not answer over HTTPS."
NOTE_HTTP_UNREACHABLE = "{host} did not answer over plain HTTP."
NOTE_REDIRECT_ELSEWHERE = (
    "HTTPS redirects to another host ({target}); it was not followed (it is not verified)."
)
NOTE_TOO_MANY_REDIRECTS = "Stopped after {count} redirects on the same host."
NOTE_NO_CONNECTION_DETAILS = (
    "The connection's address and certificate were not available; only headers were recorded."
)
NOTE_BAD_CERTIFICATE = "The certificate the server presented could not be read."

Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clean(value: str | None, limit: int = MAX_HEADER_LENGTH) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    if not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
        return None
    return text[:limit]


def product_of(header: str | None) -> tuple[str, str | None] | None:
    """``("nginx", "1.25.3")`` from ``nginx/1.25.3 (Ubuntu)``; None if it names nothing."""
    text = _clean(header)
    if text is None:
        return None
    first = text.split(" ", 1)[0]
    name, _, version = first.partition("/")
    if not _PRODUCT.fullmatch(name):
        return None
    return name.lower(), version if _VERSION.fullmatch(version) else None


@dataclass
class Answer:
    """One site's answer: the final response's status and headers, and the connection."""

    status: int
    headers: httpx.Headers
    redirected_elsewhere: str | None = None
    too_many_redirects: bool = False
    peer: tuple[str, int] | None = None
    tls_version: str | None = None
    certificate_der: bytes | None = None
    notes: list[str] = field(default_factory=list)


def _connection(
    response: httpx.Response,
) -> tuple[tuple[str, int] | None, str | None, bytes | None]:
    """The peer address, TLS version and certificate of the response's connection, if known."""
    stream: Any = response.extensions.get("network_stream")
    if stream is None:
        return None, None, None
    peer: tuple[str, int] | None = None
    try:
        address = stream.get_extra_info("server_addr")
        if isinstance(address, tuple) and len(address) >= 2:
            peer = (str(address[0]), int(address[1]))
    except Exception:
        peer = None
    version: str | None = None
    der: bytes | None = None
    try:
        ssl_object = stream.get_extra_info("ssl_object")
        if ssl_object is not None:
            version = ssl_object.version()
            der = ssl_object.getpeercert(binary_form=True)
    except Exception:
        version, der = None, None
    return peer, version, der


def _tls_failure(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


class HttpHeadersShuttle(Shuttle):
    """A verified site's headers, certificate and server software. See the module docstring."""

    meta = ShuttleMeta(
        name="http-headers",
        version="1.0.0",
        title="Web server headers and TLS",
        description=(
            "Opens https://<domain>/ and http://<domain>/ like a browser would (headers only; "
            "redirects followed on the same host only) and records the status, the security "
            "headers sent and missing, the Server and X-Powered-By software, the TLS version "
            "and certificate, and the address and service that answered. Active: it contacts "
            "the site, so it runs only on domains this case has verified."
        ),
        input_types=frozenset({DOMAIN}),
        output_entity_types=frozenset({DOMAIN, IP, SERVICE, CERTIFICATE, TECHNOLOGY}),
        output_relationship_types=frozenset(
            {
                RelationType.RESOLVES_TO,
                RelationType.RUNS_ON,
                RelationType.PRESENTS,
                RelationType.COVERS,
                RelationType.USES,
            }
        ),
        mode=Mode.ACTIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
    )

    def __init__(self, *, http: httpx.AsyncClient | None = None, now: Now = _utcnow) -> None:
        """``http`` and ``now`` replace the real ones in tests (a given client stays open)."""
        self._http = http
        self._now = now

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        host = entity.value
        client = self._http or ctx.http
        ctx.progress(f"Opening https://{host}/.")
        https_valid: bool | None = None
        answer: Answer | None = None
        try:
            answer = await self._https(ctx, client, host)
            https_valid = True
        except DestinationNotAllowedError:
            ctx.note(NOTE_NOT_PUBLIC.format(host=host))
            return
        except httpx.HTTPError as error:
            if _tls_failure(error):
                https_valid = False
                ctx.note(NOTE_TLS_INVALID.format(host=host))
            else:
                ctx.note(NOTE_HTTPS_UNREACHABLE.format(host=host))
        ctx.raise_if_cancelled()
        ctx.progress(f"Opening http://{host}/.")
        redirects: bool | None = None
        try:
            async with client.stream("GET", f"http://{host}/", follow_redirects=False) as response:
                location = response.headers.get("location")
                target = urlsplit(urljoin(str(response.url), location)) if location else None
                redirects = response.is_redirect and target is not None and target.scheme == "https"
        except DestinationNotAllowedError:
            ctx.note(NOTE_NOT_PUBLIC.format(host=host))
            return
        except httpx.HTTPError:
            ctx.note(NOTE_HTTP_UNREACHABLE.format(host=host))
        if answer is None and redirects is None and https_valid is None:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)
        for note in answer.notes if answer else []:
            ctx.note(note)
        async for finding in self._findings(ctx, entity, answer, https_valid, redirects):
            yield finding

    async def _https(self, ctx: ShuttleContext, client: httpx.AsyncClient, host: str) -> Answer:
        url = f"https://{host}/"
        first: tuple[tuple[str, int] | None, str | None, bytes | None] | None = None
        notes: list[str] = []
        for hop in range(MAX_REDIRECTS + 1):
            ctx.raise_if_cancelled()
            async with client.stream("GET", url, follow_redirects=False) as response:
                if first is None:
                    first = _connection(response)
                location = response.headers.get("location")
                if not response.is_redirect or not location:
                    break
                target = urljoin(url, location)
                parts = urlsplit(target)
                if parts.scheme != "https" or (parts.hostname or "").lower() != host:
                    notes.append(NOTE_REDIRECT_ELSEWHERE.format(target=parts.hostname or "?"))
                    break
                if hop == MAX_REDIRECTS:
                    notes.append(NOTE_TOO_MANY_REDIRECTS.format(count=MAX_REDIRECTS))
                    break
                url = target
        peer, version, der = first if first is not None else (None, None, None)
        if peer is None and der is None:
            notes.append(NOTE_NO_CONNECTION_DETAILS)
        return Answer(
            status=response.status_code,
            headers=response.headers,
            peer=peer,
            tls_version=version,
            certificate_der=der,
            notes=notes,
        )

    async def _findings(
        self,
        ctx: ShuttleContext,
        entity: EntityInput,
        answer: Answer | None,
        https_valid: bool | None,
        redirects: bool | None,
    ) -> AsyncIterator[Finding]:
        properties: dict[str, Any] = {"http_checked_at": self._now()}
        if https_valid is not None:
            properties["https_valid"] = https_valid
        if redirects is not None:
            properties["http_redirects_to_https"] = redirects
        server: tuple[str, str | None] | None = None
        technologies: list[str] = []
        if answer is not None:
            headers = answer.headers
            present = [name for name in SECURITY_HEADERS if name in headers]
            properties.update(
                https_status=answer.status,
                security_headers=present,
                missing_security_headers=[n for n in SECURITY_HEADERS if n not in present],
                tls_version=_clean(answer.tls_version),
                server=_clean(headers.get("server")),
                powered_by=_clean(headers.get("x-powered-by")),
                hsts=_clean(headers.get("strict-transport-security"), MAX_HSTS_LENGTH),
            )
            server = product_of(headers.get("server"))
            powered = product_of(headers.get("x-powered-by"))
            technologies = list(dict.fromkeys(p[0] for p in (server, powered) if p is not None))
        yield EntityFinding(
            type=DOMAIN,
            value=entity.value,
            properties={k: v for k, v in properties.items() if v is not None},
        )
        for name in technologies:
            yield EntityFinding(type=TECHNOLOGY, value=name)
            yield RelationshipFinding(
                type=RelationType.USES,
                source=entity.ref,
                target=EntityRef(type=TECHNOLOGY, value=name),
            )
        if answer is None:
            return
        service = self._service(answer)
        if service is not None:
            address, service_value = service
            yield EntityFinding(type=IP, value=address)
            yield RelationshipFinding(
                type=RelationType.RESOLVES_TO,
                source=entity.ref,
                target=EntityRef(type=IP, value=address),
            )
            service_properties: dict[str, Any] = {}
            if server is not None:
                service_properties["product"] = server[0]
                if server[1] is not None:
                    service_properties["version"] = server[1]
            yield EntityFinding(type=SERVICE, value=service_value, properties=service_properties)
            yield RelationshipFinding(
                type=RelationType.RUNS_ON,
                source=EntityRef(type=SERVICE, value=service_value),
                target=EntityRef(type=IP, value=address),
            )
            for name in technologies:
                yield RelationshipFinding(
                    type=RelationType.USES,
                    source=EntityRef(type=SERVICE, value=service_value),
                    target=EntityRef(type=TECHNOLOGY, value=name),
                )
        certificate = self._certificate(ctx, answer)
        if certificate is None:
            return
        yield EntityFinding(
            type=CERTIFICATE,
            value=certificate.fingerprint,
            properties={
                k: v
                for k, v in {
                    "subject_cn": certificate.subject_cn,
                    "issuer": certificate.issuer,
                    "serial_number": certificate.serial_number,
                    "not_before": certificate.not_before,
                    "not_after": certificate.not_after,
                    "self_signed": certificate.self_signed,
                    "san_count": certificate.san_count,
                }.items()
                if v is not None
            },
        )
        cert_ref = EntityRef(type=CERTIFICATE, value=certificate.fingerprint)
        # The connection verified it for this host.
        yield RelationshipFinding(type=RelationType.COVERS, source=cert_ref, target=entity.ref)
        if service is not None:
            yield RelationshipFinding(
                type=RelationType.PRESENTS,
                source=EntityRef(type=SERVICE, value=service[1]),
                target=cert_ref,
            )

    @staticmethod
    def _service(answer: Answer) -> tuple[str, str] | None:
        if answer.peer is None:
            return None
        host, port = answer.peer
        try:
            address = normalize(IP, host).value
            literal = f"[{address}]" if ipaddress.ip_address(address).version == 6 else address
            return address, normalize(SERVICE, f"{literal}:{port}/tcp").value
        except (NormalizationError, ValueError):
            return None

    @staticmethod
    def _certificate(ctx: ShuttleContext, answer: Answer) -> PresentedCertificate | None:
        if not answer.certificate_der:
            return None
        try:
            return parse_der(answer.certificate_der)
        except ValueError:
            ctx.note(NOTE_BAD_CERTIFICATE)
            return None


__all__ = [
    "ADMIRALTY",
    "CONFIDENCE",
    "MAX_REDIRECTS",
    "SECURITY_HEADERS",
    "HttpHeadersShuttle",
    "product_of",
]
