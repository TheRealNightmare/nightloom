"""Case and membership operations.

Plain async functions over an ``AsyncSession``, independent of HTTP; access
checks happen before they are called (:mod:`nightloom_api.cases.access`).
Failures are raised as :class:`CaseServiceError` subclasses, which the router
translates into status codes.

Every successful change records its audit events in the same transaction,
right before committing. Audit ``details`` hold field names, roles and counts,
never the text of a title, description or purpose.
"""

import base64
import binascii
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api import evidence
from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.service import Actor
from nightloom_api.auth import clock
from nightloom_api.auth.models import Role, User
from nightloom_api.auth.schemas import normalize_email
from nightloom_api.cases.access import CASE_CREATOR_ROLES, allowed_case_roles, effective_role
from nightloom_api.cases.models import (
    ETHICS_VERSION,
    Case,
    CaseMember,
    CaseRole,
    CaseStatus,
)
from nightloom_api.cases.schemas import (
    EDITOR_FIELDS,
    NOTE_REQUIRED,
    REASON_FIELDS,
    CaseCreate,
    CaseUpdate,
    note_missing,
)
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra.storage import Storage

logger = logging.getLogger(__name__)

LIKE_ESCAPE = "\\"


class CaseServiceError(Exception):
    """Base class; ``str(error)`` is safe to return to the client."""

    reason = "error"


class CaseValidationError(CaseServiceError):
    """The request is well-formed but not acceptable for this case (422)."""

    reason = "invalid"


class ConflictError(CaseServiceError):
    """The change would leave the case without an owner (409)."""

    reason = "last_owner"


class ConfirmationError(CaseServiceError):
    """``confirm_title`` did not match (422)."""

    reason = "confirm_title_mismatch"

    def __init__(self) -> None:
        super().__init__("confirm_title does not match the case title.")


class NotFoundError(CaseServiceError):
    """A user or member the request names does not exist (404)."""

    reason = "not_found"


class InvalidCursorError(CaseServiceError):
    reason = "invalid_cursor"

    def __init__(self) -> None:
        super().__init__("Invalid cursor.")


NO_ACTIVE_USER = "No active user with that email."
NOT_A_MEMBER = "That user is not a member of this case."
LAST_OWNER = "A case must keep at least one owner."
CHANGE_REASON_REQUIRED = (
    "change_reason (at least 10 characters) is required to change "
    "purpose, legal_basis, legal_basis_note or allow_person_entities."
)


# --- creation and listing ---------------------------------------------------------


async def create_case(db: AsyncSession, creator: User, body: CaseCreate, *, actor: Actor) -> Case:
    """Create a case; the creator becomes its owner. Only admins and analysts may create."""
    if creator.role not in CASE_CREATOR_ROLES:  # the router checks first; this is a backstop
        raise CaseValidationError("Only admins and analysts can create cases.")
    now = clock.utcnow()
    case = Case(
        title=body.title,
        description=body.description,
        purpose=body.purpose,
        legal_basis=body.legal_basis,
        legal_basis_note=body.legal_basis_note,
        ethics_ack_version=ETHICS_VERSION,
        ethics_ack_at=now,
        allow_person_entities=body.allow_person_entities,
        status=CaseStatus.OPEN,
        retention_days=body.retention_days,
        created_by=creator.id,
        created_at=now,
        updated_at=now,
    )
    db.add(case)
    await db.flush()
    db.add(CaseMember(case_id=case.id, user_id=creator.id, role=CaseRole.OWNER, added_at=now))
    await db.flush()

    details: dict[str, object] = {
        "legal_basis": case.legal_basis.value,
        "allow_person_entities": case.allow_person_entities,
        "retention_days": case.retention_days,
        "ethics_ack_version": case.ethics_ack_version,
    }
    if body.allow_person_entities:
        details["person_entities_reason"] = body.person_entities_reason
    await audit.record(
        db,
        action=AuditAction.CASE_CREATE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.CASE,
        target_id=case.id,
        case_id=case.id,
        details=details,
    )
    await db.commit()
    return case


def escape_like(text: str) -> str:
    """Escape LIKE wildcards (``%``, ``_``) and the escape character itself."""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def encode_cursor(updated_at: datetime, case_id: uuid.UUID) -> str:
    """An opaque cursor for the position after (``updated_at``, ``case_id``)."""
    raw = f"{updated_at.isoformat()}|{case_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Inverse of :func:`encode_cursor`; raises :class:`InvalidCursorError`."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, _, case_id = raw.partition("|")
        updated_at = datetime.fromisoformat(stamp)
        if updated_at.tzinfo is None:
            raise ValueError("naive timestamp")
        return updated_at, uuid.UUID(case_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError from exc


@dataclass(frozen=True)
class CaseListing:
    items: list[tuple[Case, CaseRole]]
    next_cursor: str | None


async def list_cases(
    db: AsyncSession,
    user: User,
    *,
    limit: int,
    cursor: str | None = None,
    status: CaseStatus | None = None,
    query: str | None = None,
) -> CaseListing:
    """The user's cases, most recently updated first, with their effective role in each."""
    conditions: list[ColumnElement[bool]] = [CaseMember.user_id == user.id]
    if cursor is not None:
        updated_at, case_id = decode_cursor(cursor)
        conditions.append(tuple_(Case.updated_at, Case.id) < tuple_(updated_at, case_id))
    if status is not None:
        conditions.append(Case.status == status)
    if query:
        conditions.append(Case.title.ilike(f"%{escape_like(query)}%", escape=LIKE_ESCAPE))
    statement = (
        select(Case, CaseMember.role)
        .join(CaseMember, CaseMember.case_id == Case.id)
        .where(*conditions)
        .order_by(Case.updated_at.desc(), Case.id.desc())
        .limit(limit + 1)
    )
    rows = (await db.execute(statement)).all()
    has_more = len(rows) > limit
    items = [(case, effective_role(user.role, role)) for case, role in rows[:limit]]
    last = items[-1][0] if has_more else None
    return CaseListing(
        items=items, next_cursor=encode_cursor(last.updated_at, last.id) if last else None
    )


# --- changing and deleting --------------------------------------------------------


@dataclass(frozen=True)
class UpdatePlan:
    """What a PATCH would change, worked out before anything is written."""

    changes: dict[str, object]
    needs_owner: bool


def plan_update(case: Case, body: CaseUpdate) -> UpdatePlan:
    """The fields whose value actually changes, and whether only an owner may change them."""
    changes = {
        field: value for field, value in body.changes().items() if getattr(case, field) != value
    }
    needs_owner = bool(set(body.changes()) - EDITOR_FIELDS)
    return UpdatePlan(changes=changes, needs_owner=needs_owner)


async def update_case(
    db: AsyncSession, case: Case, body: CaseUpdate, plan: UpdatePlan, *, actor: Actor
) -> Case:
    """Apply a PATCH the caller is allowed to make (see :func:`plan_update`).

    Changing purpose, legal basis (or its note) or the person-entities switch
    needs ``change_reason``. Closing sets ``closed_at``; reopening clears it.
    Records ``case.update`` for field changes and ``case.status_change`` for a
    new status, in the same transaction.
    """
    changes = plan.changes
    reason_fields = sorted(REASON_FIELDS & changes.keys())
    if reason_fields and body.change_reason is None:
        raise CaseValidationError(CHANGE_REASON_REQUIRED)
    new_basis = body.legal_basis if "legal_basis" in changes else case.legal_basis
    new_note = body.legal_basis_note if "legal_basis_note" in changes else case.legal_basis_note
    if note_missing(new_basis or case.legal_basis, new_note):
        raise CaseValidationError(NOTE_REQUIRED)
    if not changes:
        return case

    old_status = case.status
    for field, value in changes.items():
        setattr(case, field, value)
    if "status" in changes:
        if case.status == CaseStatus.CLOSED:
            case.closed_at = clock.utcnow()
        elif case.status == CaseStatus.OPEN:
            case.closed_at = None
    await db.flush()

    field_names = sorted(changes.keys() - {"status"})
    if field_names:
        details: dict[str, object] = {"fields": field_names}
        if reason_fields:
            details["change_reason"] = body.change_reason
        await audit.record(
            db,
            action=AuditAction.CASE_UPDATE,
            outcome=AuditOutcome.SUCCESS,
            actor=actor,
            target_type=TargetType.CASE,
            target_id=case.id,
            case_id=case.id,
            details=details,
        )
    if "status" in changes:
        await audit.record(
            db,
            action=AuditAction.CASE_STATUS_CHANGE,
            outcome=AuditOutcome.SUCCESS,
            actor=actor,
            target_type=TargetType.CASE,
            target_id=case.id,
            case_id=case.id,
            details={"from": old_status.value, "to": case.status.value},
        )
    await db.commit()
    await db.refresh(case)
    return case


async def delete_case_dependents(
    graph: GraphRepository,
    make_session: async_sessionmaker[AsyncSession],
    case_id: uuid.UUID,
    *,
    actor: Actor,
    storage: Storage | None = None,
) -> None:
    """Remove a deleted case's data outside PostgreSQL: its Neo4j graph and evidence files.

    Runs **after** the case row's deletion has committed, so a graph failure
    can never resurrect the case or leave a half-deleted one behind. It never
    raises: the case is already gone, so the request must not end in a 500.
    The outcome is recorded as ``case.graph_delete`` (``success`` with the
    number of nodes removed, or ``failure`` with the error type); a failure
    is also logged, and leaves nodes that only an operator can remove (they
    are unreachable through the API, which checks case membership first).
    """
    details: dict[str, object]
    try:
        deleted = await graph.delete_case_graph(case_id)
    except Exception as exc:
        logger.error(
            "Deleting the graph of deleted case %s failed: %s", case_id, type(exc).__name__
        )
        outcome, details = AuditOutcome.FAILURE, {"reason": type(exc).__name__}
    else:
        outcome, details = AuditOutcome.SUCCESS, {"deleted_nodes": deleted}
    if storage is not None:
        try:
            details["deleted_evidence"] = await evidence.delete_case(storage, case_id)
        except Exception as exc:
            logger.error(
                "Deleting the evidence of deleted case %s failed: %s",
                case_id,
                type(exc).__name__,
            )
            outcome = AuditOutcome.FAILURE
            details["evidence_error"] = type(exc).__name__
    await audit.record_standalone(
        make_session,
        action=AuditAction.CASE_GRAPH_DELETE,
        outcome=outcome,
        actor=actor,
        target_type=TargetType.CASE,
        target_id=case_id,
        case_id=case_id,
        details=details,
    )


async def delete_case(db: AsyncSession, case: Case, confirm_title: str, *, actor: Actor) -> int:
    """Delete a case and its members after an exact title confirmation.

    The audit event keeps the case ID and member count, never the title.
    Returns the number of members removed. The caller then removes the case's
    graph with :func:`delete_case_dependents`, once this has committed.
    """
    if confirm_title != case.title:
        raise ConfirmationError
    case_id = case.id
    member_count = int(
        await db.scalar(
            select(func.count()).select_from(CaseMember).where(CaseMember.case_id == case_id)
        )
        or 0
    )
    await db.execute(delete(Case).where(Case.id == case_id))
    await audit.record(
        db,
        action=AuditAction.CASE_DELETE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.CASE,
        target_id=case_id,
        case_id=case_id,
        details={"member_count": member_count},
    )
    await db.commit()
    return member_count


# --- members ------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    member: CaseMember
    user: User


async def list_members(db: AsyncSession, case_id: uuid.UUID) -> Sequence[Member]:
    """The case's members: owners first, then editors, then viewers; by email within each."""
    rows = (
        await db.execute(
            select(CaseMember, User)
            .join(User, User.id == CaseMember.user_id)
            .where(CaseMember.case_id == case_id)
            .order_by(CaseMember.role, User.email)
        )
    ).all()
    return [Member(member=member, user=user) for member, user in rows]


async def _other_owners(db: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Owners of the case other than ``user_id`` who can act as owners.

    Deactivated users and global viewers (capped at case viewer) do not
    count: they could not manage the case.
    """
    count = await db.scalar(
        select(func.count())
        .select_from(CaseMember)
        .join(User, User.id == CaseMember.user_id)
        .where(
            CaseMember.case_id == case_id,
            CaseMember.user_id != user_id,
            CaseMember.role == CaseRole.OWNER,
            User.is_active.is_(True),
            User.role != Role.VIEWER,
        )
    )
    return int(count or 0)


async def _get_member(
    db: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID
) -> CaseMember | None:
    return await db.get(CaseMember, (case_id, user_id), populate_existing=True)


@dataclass(frozen=True)
class PutResult:
    member: Member
    created: bool


async def put_member(
    db: AsyncSession,
    case: Case,
    *,
    email: str,
    role: CaseRole,
    added_by: uuid.UUID,
    actor: Actor,
) -> PutResult:
    """Add an active user to the case by email, or change their role.

    The caller holds the case lock (see ``case_access(lock=True)``), which
    serializes membership changes, so the last-owner check cannot race.
    """
    email = normalize_email(email)
    user = (
        await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError(NO_ACTIVE_USER)
    if role not in allowed_case_roles(user.role):
        raise CaseValidationError("Users with the global viewer role can only be case viewers.")

    member = await _get_member(db, case.id, user.id)
    if member is not None and member.role == role:
        return PutResult(Member(member, user), created=False)

    if member is None:
        member = CaseMember(
            case_id=case.id, user_id=user.id, role=role, added_by=added_by, added_at=clock.utcnow()
        )
        db.add(member)
        action, details = AuditAction.CASE_MEMBER_ADD, {"role": role.value}
    else:
        if member.role == CaseRole.OWNER and not await _other_owners(db, case.id, user.id):
            raise ConflictError(LAST_OWNER)
        details = {"from": member.role.value, "to": role.value}
        member.role = role
        action = AuditAction.CASE_MEMBER_UPDATE
    await db.flush()
    await audit.record(
        db,
        action=action,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.USER,
        target_id=user.id,
        case_id=case.id,
        details=details,
    )
    await db.commit()
    return PutResult(Member(member, user), created=action == AuditAction.CASE_MEMBER_ADD)


async def remove_member(db: AsyncSession, case: Case, user_id: uuid.UUID, *, actor: Actor) -> None:
    """Remove a member; never the case's last owner. The caller holds the case lock."""
    member = await _get_member(db, case.id, user_id)
    if member is None:
        raise NotFoundError(NOT_A_MEMBER)
    if member.role == CaseRole.OWNER and not await _other_owners(db, case.id, user_id):
        raise ConflictError(LAST_OWNER)
    removed_role = member.role
    await db.delete(member)
    await db.flush()
    await audit.record(
        db,
        action=AuditAction.CASE_MEMBER_REMOVE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.USER,
        target_id=user_id,
        case_id=case.id,
        details={"role": removed_role.value, "self": user_id == actor.user_id},
    )
    await db.commit()
