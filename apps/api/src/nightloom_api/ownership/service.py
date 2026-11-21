"""Ownership verifications: adding, checking, covering targets, expiring.

A case owner adds a **domain** and a **method**; Nightloom gives them a
token to publish:

- ``dns_txt``: a TXT record at ``_nightloom-challenge.<domain>`` whose value is
  ``nightloom-verification=<token>``;
- ``http_file``: the same line as the body of
  ``https://<domain>/.well-known/nightloom-verification.txt`` (HTTPS, a public
  address, answered with 200 directly: no redirects).

The **worker** looks for the proof (the API never makes network requests):
when an owner asks (*Check now*), and once a day for verified domains. A
found proof makes the verification **verified** for :data:`VALIDITY` (30
days); checking again renews it. A daily re-check that no longer finds the
proof **revokes** it; one that cannot tell (the resolver or the site is
unreachable) is tried again, and revokes after :data:`MAX_FAILURES` in a row.
After 30 days without being renewed it **expires**.

A verified, unexpired domain **covers**: itself and its subdomains; URLs and
services on those names; and the IP addresses (and services on them) the
domain resolved to when last checked, which the worker confirms by resolving
the domain again before an active Shuttle runs. Only covered targets may be
scanned by active Shuttles (:func:`covers`).
"""

import ipaddress
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.service import Actor
from nightloom_api.ownership.models import Method, OwnershipVerification, VerificationStatus
from nightloom_core.entities import EntityType
from nightloom_core.normalize import (
    NormalizationError,
    is_public_suffix,
    normalize_domain,
    parse_key,
)

VALIDITY = timedelta(days=30)
RECHECK_EVERY = timedelta(hours=24)
# A queued check that has not reported after this long is queued again.
CHECK_STALE_AFTER = timedelta(minutes=10)
# An owner may ask again this soon after the last request.
CHECK_COOLDOWN = timedelta(seconds=30)
MAX_FAILURES = 3
MAX_PER_CASE = 50
MAX_ADDRESSES = 32

RECORD_PREFIX = "_nightloom-challenge"
PROOF_PREFIX = "nightloom-verification="
WELL_KNOWN_PATH = "/.well-known/nightloom-verification.txt"

SYSTEM_ACTOR = Actor.system("ownership-check")


class CheckResult(StrEnum):
    """What a check found."""

    # The proof is there.
    FOUND = "found"
    # The proof is definitely not there (no record, another value, 404, ...).
    MISSING = "missing"
    # Could not tell: the resolver or the site did not answer.
    UNREACHABLE = "unreachable"


# Short codes for last_error, and what they mean (shown in the web UI).
ERRORS: dict[str, str] = {
    "record_not_found": "No TXT record with the token was found.",
    "file_not_found": "The file was not found (the site did not answer 200).",
    "token_mismatch": "The file does not contain the token.",
    "redirected": "The file's address redirects; it must be answered directly.",
    "not_public": "The domain does not resolve to public addresses only.",
    "tls_error": "The site's HTTPS certificate could not be verified.",
    "dns_unreachable": "The DNS resolvers did not answer.",
    "site_unreachable": "The site did not answer.",
}


class OwnershipError(ValueError):
    """Refused: the message is for the user; ``status_code`` for the API."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def clean_domain(value: str) -> str:
    """The canonical domain, refused if it is a wildcard or a public suffix."""
    try:
        domain = normalize_domain(value)
    except NormalizationError as error:
        raise OwnershipError(str(error)) from error
    if domain.startswith("*."):
        raise OwnershipError("Verify the domain itself, without a wildcard.")
    if domain.startswith("_"):
        raise OwnershipError("Verify a host name, not a service label such as _dmarc.")
    if is_public_suffix(domain):
        raise OwnershipError("This is a public suffix (like co.uk); verify a domain under it.")
    return domain


def new_token() -> str:
    return secrets.token_urlsafe(24)


def proof_value(token: str) -> str:
    return f"{PROOF_PREFIX}{token}"


def record_name(domain: str) -> str:
    return f"{RECORD_PREFIX}.{domain}"


def file_url(domain: str) -> str:
    return f"https://{domain}{WELL_KNOWN_PATH}"


def is_active(row: OwnershipVerification, now: datetime) -> bool:
    """Whether the verification currently lets active Shuttles run."""
    return (
        row.status == VerificationStatus.VERIFIED
        and row.expires_at is not None
        and row.expires_at > now
    )


def under(name: str, domain: str) -> bool:
    """Whether ``name`` is ``domain`` or one of its subdomains."""
    return name == domain or name.endswith("." + domain)


# --- reading -------------------------------------------------------------------------------


async def list_for_case(db: AsyncSession, case_id: uuid.UUID) -> list[OwnershipVerification]:
    return list(
        (
            await db.execute(
                select(OwnershipVerification)
                .where(OwnershipVerification.case_id == case_id)
                .order_by(OwnershipVerification.domain)
            )
        ).scalars()
    )


async def get(
    db: AsyncSession, case_id: uuid.UUID, verification_id: uuid.UUID, *, lock: bool = False
) -> OwnershipVerification | None:
    statement = select(OwnershipVerification).where(
        OwnershipVerification.case_id == case_id, OwnershipVerification.id == verification_id
    )
    if lock:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


@dataclass(frozen=True)
class Target:
    """What an active Shuttle would touch: a host name or an address."""

    host: str | None = None
    address: str | None = None


def target_of(entity_key: str) -> Target | None:
    """The host or address an entity names, or ``None`` if it names neither."""
    try:
        entity_type, value = parse_key(entity_key)
    except NormalizationError:
        return None
    match entity_type:
        case EntityType.DOMAIN:
            return Target(host=value)
        case EntityType.IP_ADDRESS:
            return Target(address=value)
        case EntityType.URL:
            host = (urlsplit(value).hostname or "").lower()
            return _host_or_address(host)
        case EntityType.SERVICE:
            host = value.rsplit(":", 1)[0].strip("[]")
            return _host_or_address(host)
        case _:
            return None


def _host_or_address(host: str) -> Target | None:
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return Target(host=host)
    return Target(address=host)


def covering(
    rows: Sequence[OwnershipVerification], target: Target, now: datetime
) -> OwnershipVerification | None:
    """The active verification that covers ``target``, if any (the most specific domain)."""
    found: OwnershipVerification | None = None
    for row in rows:
        if not is_active(row, now):
            continue
        by_name = target.host is not None and under(target.host, row.domain)
        if by_name and (found is None or len(row.domain) > len(found.domain)):
            found = row
        if target.address is not None and target.address in row.addresses:
            found = found or row
    return found


async def covers(
    db: AsyncSession, case_id: uuid.UUID, entity_key: str, now: datetime
) -> OwnershipVerification | None:
    """The case's active verification that lets an active Shuttle run on this entity."""
    target = target_of(entity_key)
    if target is None:
        return None
    return covering(await list_for_case(db, case_id), target, now)


# --- changing ------------------------------------------------------------------------------


async def create(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    domain: str,
    method: Method,
    user_id: uuid.UUID,
    actor: Actor,
) -> OwnershipVerification:
    """Add a pending verification and its ``ownership.create`` event (no commit)."""
    count = await db.scalar(
        select(func.count())
        .select_from(OwnershipVerification)
        .where(OwnershipVerification.case_id == case_id)
    )
    if (count or 0) >= MAX_PER_CASE:
        raise OwnershipError(
            f"A case can have at most {MAX_PER_CASE} verified domains. Remove one first."
        )
    taken = await db.scalar(
        select(OwnershipVerification.id).where(
            OwnershipVerification.case_id == case_id, OwnershipVerification.domain == domain
        )
    )
    if taken is not None:
        raise OwnershipError("This domain is already in the list.", status_code=409)
    row = OwnershipVerification(
        case_id=case_id,
        domain=domain,
        method=method,
        token=new_token(),
        status=VerificationStatus.PENDING,
        created_by=user_id,
        addresses=[],
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        action=AuditAction.OWNERSHIP_CREATE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.OWNERSHIP,
        target_id=str(row.id),
        case_id=case_id,
        details={"domain": domain, "method": str(method)},
    )
    return row


async def delete(db: AsyncSession, row: OwnershipVerification, *, actor: Actor) -> None:
    """Remove a verification and record ``ownership.delete`` (no commit)."""
    await audit.record(
        db,
        action=AuditAction.OWNERSHIP_DELETE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.OWNERSHIP,
        target_id=str(row.id),
        case_id=row.case_id,
        details={"domain": row.domain, "status": row.status},
    )
    await db.delete(row)


def request_check(row: OwnershipVerification, now: datetime) -> bool:
    """Queue an owner's check (it renews). False when one was asked for moments ago."""
    pending = row.check_requested_at
    if pending is not None and row.check_renews and now - pending < CHECK_COOLDOWN:
        return False
    row.check_requested_at = now
    row.check_renews = True
    return True


@dataclass(frozen=True)
class Proof:
    """What the worker found: the result, an error code, and the domain's addresses."""

    result: CheckResult
    error: str | None = None
    addresses: tuple[str, ...] = ()


async def apply_check(
    db: AsyncSession, row: OwnershipVerification, proof: Proof, now: datetime
) -> str:
    """Record a check's result on a locked row and audit it; returns the new status (no commit).

    ``row.check_renews`` marks an owner's check (*Check now*): ``found`` (re)starts the 30
    days, and anything else makes it ``failed``. Otherwise it is the daily
    re-check of a verified domain: ``found`` keeps the expiry (and refreshes
    the addresses), ``missing`` revokes it, and ``unreachable`` counts a
    failure and revokes after :data:`MAX_FAILURES` in a row.
    """
    was = row.status
    renew = row.check_renews
    row.check_requested_at = None
    row.check_renews = False
    row.checked_at = now
    recheck = not renew and is_active(row, now)
    if proof.result == CheckResult.FOUND:
        row.last_error = None
        row.failures = 0
        row.addresses = list(proof.addresses[:MAX_ADDRESSES])
        if not recheck:
            row.status = VerificationStatus.VERIFIED
            row.verified_at = now
            row.expires_at = now + VALIDITY
    elif recheck:
        row.last_error = proof.error
        if proof.result == CheckResult.UNREACHABLE:
            row.failures += 1
        if proof.result == CheckResult.MISSING or row.failures >= MAX_FAILURES:
            _drop(row, VerificationStatus.REVOKED)
    elif renew:
        row.last_error = proof.error
        _drop(row, VerificationStatus.FAILED)
    else:
        # A re-check of something no longer active (it expired meanwhile): nothing to do.
        return row.status
    if recheck and row.status == was:
        return row.status
    action = (
        AuditAction.OWNERSHIP_REVOKE
        if row.status == VerificationStatus.REVOKED
        else AuditAction.OWNERSHIP_CHECK
    )
    await audit.record(
        db,
        action=action,
        outcome=AuditOutcome.SUCCESS if proof.result == CheckResult.FOUND else AuditOutcome.FAILURE,
        actor=SYSTEM_ACTOR,
        target_type=TargetType.OWNERSHIP,
        target_id=str(row.id),
        case_id=row.case_id,
        details={
            "domain": row.domain,
            "method": row.method,
            "status": row.status,
            **({"error": proof.error} if proof.error else {}),
        },
    )
    return row.status


def _drop(row: OwnershipVerification, status: VerificationStatus) -> None:
    row.status = status
    row.verified_at = None
    row.expires_at = None
    row.addresses = []
    row.failures = 0


# --- the sweep (worker) --------------------------------------------------------------------


async def sweep(db: AsyncSession, now: datetime, *, limit: int = 20) -> list[uuid.UUID]:
    """Expire what is past its 30 days, and pick the checks to queue (commits).

    Verified domains not checked for :data:`RECHECK_EVERY` get a re-check;
    owners' checks queued more than :data:`CHECK_STALE_AFTER` ago (a lost
    message, a worker restart) are queued again.
    """
    expired = list(
        (
            await db.execute(
                select(OwnershipVerification)
                .where(
                    OwnershipVerification.status == VerificationStatus.VERIFIED,
                    OwnershipVerification.expires_at <= now,
                )
                .with_for_update(skip_locked=True)
                .limit(100)
            )
        ).scalars()
    )
    for row in expired:
        _drop(row, VerificationStatus.EXPIRED)
        row.check_requested_at = None
        row.check_renews = False
        await audit.record(
            db,
            action=AuditAction.OWNERSHIP_EXPIRE,
            outcome=AuditOutcome.SUCCESS,
            actor=SYSTEM_ACTOR,
            target_type=TargetType.OWNERSHIP,
            target_id=str(row.id),
            case_id=row.case_id,
            details={"domain": row.domain},
        )
    due: list[uuid.UUID] = []
    stale = list(
        (
            await db.execute(
                select(OwnershipVerification)
                .where(OwnershipVerification.check_requested_at <= now - CHECK_STALE_AFTER)
                .order_by(OwnershipVerification.check_requested_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        ).scalars()
    )
    for row in stale:
        row.check_requested_at = now
        due.append(row.id)
    if len(due) < limit:
        recheck = list(
            (
                await db.execute(
                    select(OwnershipVerification)
                    .where(
                        OwnershipVerification.status == VerificationStatus.VERIFIED,
                        OwnershipVerification.check_requested_at.is_(None),
                        OwnershipVerification.checked_at <= now - RECHECK_EVERY,
                    )
                    .order_by(OwnershipVerification.checked_at)
                    .with_for_update(skip_locked=True)
                    .limit(limit - len(due))
                )
            ).scalars()
        )
        for row in recheck:
            row.check_requested_at = now
            row.check_renews = False
            due.append(row.id)
    await db.commit()
    return due


__all__ = [
    "CHECK_COOLDOWN",
    "ERRORS",
    "MAX_FAILURES",
    "MAX_PER_CASE",
    "PROOF_PREFIX",
    "RECHECK_EVERY",
    "RECORD_PREFIX",
    "VALIDITY",
    "WELL_KNOWN_PATH",
    "CheckResult",
    "OwnershipError",
    "Proof",
    "Target",
    "apply_check",
    "clean_domain",
    "covering",
    "covers",
    "create",
    "delete",
    "file_url",
    "get",
    "is_active",
    "list_for_case",
    "new_token",
    "proof_value",
    "record_name",
    "request_check",
    "sweep",
    "target_of",
    "under",
]
