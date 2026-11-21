"""Looking for ownership proofs, and the daily sweep (see :mod:`nightloom_api.ownership`).

The worker is the only part of Nightloom that looks: the DNS record through
the same public resolvers as the DNS Shuttle (``NIGHTLOOM_DNS_RESOLVERS``),
the file through the same guarded HTTP client as Shuttles (public addresses
only, checked when connecting), over HTTPS, with no redirects and at most
:data:`MAX_FILE_BYTES` read. After a found proof it records the domain's
public A and AAAA addresses, which the verification then covers too.
"""

import logging
import ssl
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import dns.rdatatype
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.auth import clock
from nightloom_api.ownership import service
from nightloom_api.ownership.models import Method, OwnershipVerification
from nightloom_api.ownership.service import CheckResult, Proof
from nightloom_core.normalize import NormalizationError, normalize_ip
from nightloom_sdk.http import (
    DestinationNotAllowedError,
    GuardedHTTPTransport,
    GuardTransport,
    ResponseTooLargeError,
    ShuttleHttpClient,
    is_public_address,
    user_agent,
)
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_shuttle_dns.client import (
    RESOLVER_RATE_LIMIT,
    DnsClient,
    DnsQueryError,
    ResolverClient,
)
from nightloom_shuttle_dns.config import load_config

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 4096
HTTP_TIMEOUT_SECONDS = 10.0

HttpFactory = Callable[[], httpx.AsyncClient]


def guarded_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The Shuttles' guarded client, without redirects, reading at most MAX_FILE_BYTES."""
    return ShuttleHttpClient(
        transport=GuardTransport(
            transport or GuardedHTTPTransport(), limiter=None, max_response_bytes=MAX_FILE_BYTES
        ),
        headers={"User-Agent": user_agent(), "Accept-Encoding": "identity"},
        timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        follow_redirects=False,
        trust_env=False,
        max_response_bytes=MAX_FILE_BYTES,
    )


def _tls_failure(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


class AddressResolver(Protocol):
    async def addresses(self, domain: str) -> tuple[str, ...] | None:
        """The domain's public addresses now; None if the resolvers did not answer."""


@dataclass
class Prover:
    """Looks for proofs; ``dns`` and ``http`` are replaced in tests."""

    dns: DnsClient
    http: HttpFactory = guarded_client

    async def check(self, domain: str, method: str, token: str) -> Proof:
        expected = service.proof_value(token)
        if method == Method.DNS_TXT:
            proof = await self._check_txt(domain, expected)
        else:
            proof = await self._check_file(domain, expected)
        if proof.result != CheckResult.FOUND:
            return proof
        return Proof(CheckResult.FOUND, addresses=await self.addresses(domain) or ())

    async def _check_txt(self, domain: str, expected: str) -> Proof:
        try:
            answer = await self.dns.query(service.record_name(domain), dns.rdatatype.TXT)
        except DnsQueryError:
            return Proof(CheckResult.UNREACHABLE, "dns_unreachable")
        for record in answer.records if answer is not None else ():
            strings: tuple[bytes, ...] = getattr(record, "strings", ())
            value = b"".join(strings).decode("utf-8", errors="replace").strip()
            if value == expected:
                return Proof(CheckResult.FOUND)
        return Proof(CheckResult.MISSING, "record_not_found")

    async def _check_file(self, domain: str, expected: str) -> Proof:
        try:
            async with self.http() as client:
                response = await client.get(service.file_url(domain))
        except DestinationNotAllowedError:
            return Proof(CheckResult.MISSING, "not_public")
        except ResponseTooLargeError:
            return Proof(CheckResult.MISSING, "token_mismatch")
        except httpx.HTTPError as error:
            code = "tls_error" if _tls_failure(error) else "site_unreachable"
            return Proof(CheckResult.UNREACHABLE, code)
        if response.is_redirect:
            return Proof(CheckResult.MISSING, "redirected")
        if response.status_code >= 500:
            return Proof(CheckResult.UNREACHABLE, "site_unreachable")
        if response.status_code != 200:
            return Proof(CheckResult.MISSING, "file_not_found")
        lines = response.content.decode("utf-8", errors="replace").splitlines()
        if any(line.strip() == expected for line in lines):
            return Proof(CheckResult.FOUND)
        return Proof(CheckResult.MISSING, "token_mismatch")

    async def addresses(self, domain: str) -> tuple[str, ...] | None:
        """The domain's public A and AAAA addresses now; None if the resolvers did not answer."""
        found: dict[str, None] = {}
        for rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
            try:
                answer = await self.dns.query(domain, rdtype)
            except DnsQueryError:
                return None
            for record in answer.records if answer is not None else ():
                try:
                    address = normalize_ip(record.to_text())
                except NormalizationError:
                    continue
                if is_public_address(address):
                    found[address] = None
        return tuple(found)[: service.MAX_ADDRESSES]


def default_prover() -> Prover:
    """Asks the configured public resolvers (raises if the configuration is invalid)."""
    config = load_config()
    return Prover(dns=ResolverClient(config.resolvers, HostRateLimiter(RESOLVER_RATE_LIMIT)))


async def check_verification(
    sessionmaker: async_sessionmaker[AsyncSession],
    verification_id: uuid.UUID,
    prover: Prover,
) -> str | None:
    """Look for one verification's proof and record what was found. Returns the new status.

    The row is not locked while looking (that takes seconds); its result is
    written under a lock, unless it was removed meanwhile.
    """
    async with sessionmaker() as db:
        row = await db.get(OwnershipVerification, verification_id)
        if row is None:
            return None
        domain, method, token = row.domain, row.method, row.token
    proof = await prover.check(domain, method, token)
    async with sessionmaker() as db, db.begin():
        row = await db.get(OwnershipVerification, verification_id, with_for_update=True)
        if row is None or row.token != token:
            return None
        status = await service.apply_check(db, row, proof, clock.utcnow())
    logger.info("Ownership check %s: %s (%s).", verification_id, status, proof.error or "found")
    return status


__all__ = [
    "MAX_FILE_BYTES",
    "AddressResolver",
    "Prover",
    "check_verification",
    "default_prover",
    "guarded_client",
]
