"""Appending events to the audit log.

:func:`record` adds an event to the caller's transaction, so the event commits
or rolls back together with the change it describes. Events about something
that did *not* happen (a failed sign-in, a denied request) have no change to
attach to; :func:`record_standalone` writes those in their own short
transaction.

Appends are serialized with a transaction-level advisory lock: the appender
takes the lock, reads the latest hash, and inserts its row, and the lock is
held until its transaction ends. Row IDs are drawn while holding the lock, so
ID order is chain order. Callers should record as late as possible (right
before committing) to hold the lock briefly, and must use PostgreSQL's default
READ COMMITTED isolation, so the latest hash read after taking the lock is
current.
"""

import hashlib
import ipaddress
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.chain import GENESIS_HASH, chain_payload, compute_hash, sanitize_details
from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth.models import User
from nightloom_api.auth.schemas import normalize_email
from nightloom_api.request_id import current_request_id, new_request_id

logger = logging.getLogger(__name__)

# Key for pg_advisory_xact_lock: the ASCII bytes "nlaudit\0" as a bigint.
AUDIT_CHAIN_LOCK_ID = 0x6E6C_6175_6469_7400

_NEXT_ROW = text(
    "SELECT nextval(pg_get_serial_sequence('audit_events', 'id')) AS id, "
    "now() AS occurred_at, "
    "(SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1) AS prev_hash"
)


@dataclass(frozen=True)
class Actor:
    """Who did it: a user (id and email at the time), or nobody known, and from where."""

    user_id: uuid.UUID | None
    label: str | None
    ip: str | None

    @classmethod
    def for_user(cls, user: User, ip: str | None) -> "Actor":
        return cls(user_id=user.id, label=user.email, ip=ip)

    @classmethod
    def anonymous(cls, ip: str | None) -> "Actor":
        """An unauthenticated client, e.g. a failed sign-in (no email is stored)."""
        return cls(user_id=None, label=None, ip=ip)

    @classmethod
    def system(cls, name: str) -> "Actor":
        """A command run by an operator, e.g. ``system:cli``."""
        return cls(user_id=None, label=f"system:{name}", ip=None)


@dataclass(frozen=True)
class RecordedEvent:
    id: int
    hash: str


def email_digest(email: str) -> str:
    """Hex SHA-256 of the normalized email: identifies repeated attempts without storing it.

    Not anonymous: anyone holding a candidate address can compute the same
    digest. It keeps typed addresses (and passwords typed into the email field)
    out of the log.
    """
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()


def _clean_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return None


async def record(
    db: AsyncSession,
    *,
    action: AuditAction | str,
    outcome: AuditOutcome | str,
    actor: Actor,
    target_type: TargetType | str | None = None,
    target_id: object = None,
    details: Mapping[str, Any] | None = None,
    case_id: uuid.UUID | None = None,
) -> RecordedEvent:
    """Append an event in ``db``'s current transaction. The caller commits.

    Raises ``ValueError`` for an unknown action or outcome. ``details`` goes
    through :func:`~nightloom_api.audit.chain.sanitize_details`. The request ID
    is the current request's (a fresh one outside a request, e.g. the CLI).
    """
    action = AuditAction(action)
    outcome = AuditOutcome(outcome)
    values: dict[str, Any] = {
        "actor_user_id": actor.user_id,
        "actor_label": actor.label,
        "actor_ip": _clean_ip(actor.ip),
        "request_id": current_request_id() or new_request_id(),
        "action": action.value,
        "outcome": outcome.value,
        "target_type": str(target_type) if target_type is not None else None,
        "target_id": str(target_id) if target_id is not None else None,
        "case_id": case_id,
        "details": sanitize_details(details),
    }

    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": AUDIT_CHAIN_LOCK_ID})
    # A separate statement, so its snapshot is taken after the lock is held.
    row = (await db.execute(_NEXT_ROW)).one()
    values["id"] = int(row.id)
    values["occurred_at"] = row.occurred_at
    values["prev_hash"] = row.prev_hash or GENESIS_HASH
    values["hash"] = compute_hash(chain_payload(**values))

    await db.execute(insert(AuditEvent).values(**values))
    return RecordedEvent(id=values["id"], hash=values["hash"])


async def record_standalone(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    action: AuditAction,
    outcome: AuditOutcome,
    actor: Actor,
    target_type: TargetType | str | None = None,
    target_id: object = None,
    details: Mapping[str, Any] | None = None,
    case_id: uuid.UUID | None = None,
) -> RecordedEvent | None:
    """Record an event in its own short transaction (failed sign-ins, denials).

    These events describe something that did not happen, so a database error
    here is logged (without details) and swallowed: the caller still answers
    with its 401/403/429 instead of a 500.
    """
    try:
        async with sessionmaker() as db, db.begin():
            return await record(
                db,
                action=action,
                outcome=outcome,
                actor=actor,
                target_type=target_type,
                target_id=target_id,
                details=details,
                case_id=case_id,
            )
    except (SQLAlchemyError, OSError) as exc:
        logger.error("Could not record audit event %s: %s", action.value, type(exc).__name__)
        return None
