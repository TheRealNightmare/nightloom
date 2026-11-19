"""Who may do what with a case.

Access to a case comes only from membership: a global admin who is not a
member has no more access than anyone else. Each case role grants a fixed set
of :class:`Capability` values (see :data:`CAPABILITIES`), and a user whose
global role is ``viewer`` is never more than a case viewer, whatever role is
stored for them.

A user who is not a member of a case (or is deactivated) gets ``404 Case not
found.`` for every case route, exactly as if the case did not exist, so case
IDs reveal nothing. A member without the needed capability gets ``403``. Both
refusals are recorded as ``authz.denied`` with the case's ID, in a transaction
of their own.
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, NoReturn

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome
from nightloom_api.audit.service import Actor
from nightloom_api.auth.deps import FORBIDDEN, client_ip, current_user, route_path
from nightloom_api.auth.models import Role, User
from nightloom_api.cases.models import Case, CaseMember, CaseRole
from nightloom_api.infra.db import get_session, get_sessionmaker

CASE_NOT_FOUND = "Case not found."

# Global roles that may create cases (and become their owner).
CASE_CREATOR_ROLES = frozenset({Role.ADMIN, Role.ANALYST})


class Capability(StrEnum):
    """Something a case member may do."""

    READ = "read"
    # Title and description.
    EDIT = "edit"
    # Running Shuttles in the case (granted now, used once Shuttles exist).
    RUN_SHUTTLES = "run_shuttles"
    READ_AUDIT = "read_audit"
    # Purpose, legal basis, status, person entities, retention, members, deletion.
    MANAGE = "manage"
    # Exporting person-related data unredacted (owners, with a reason).
    EXPORT_PERSON_DATA = "export_person_data"


CAPABILITIES: dict[CaseRole, frozenset[Capability]] = {
    CaseRole.VIEWER: frozenset({Capability.READ}),
    CaseRole.EDITOR: frozenset(
        {Capability.READ, Capability.EDIT, Capability.RUN_SHUTTLES, Capability.READ_AUDIT}
    ),
    CaseRole.OWNER: frozenset(Capability),
}


def effective_role(global_role: Role, case_role: CaseRole) -> CaseRole:
    """The case role that applies: global viewers are capped at case viewer."""
    return CaseRole.VIEWER if global_role == Role.VIEWER else case_role


def allowed_case_roles(global_role: Role) -> frozenset[CaseRole]:
    """The case roles a user with ``global_role`` may be given."""
    return frozenset({CaseRole.VIEWER}) if global_role == Role.VIEWER else frozenset(CaseRole)


def can(role: CaseRole, capability: Capability) -> bool:
    return capability in CAPABILITIES[role]


@dataclass(frozen=True)
class CaseAccess:
    """A case the signed-in user may use, with their effective role in it.

    ``actor`` is built up front: the user object expires if the request's
    transaction is rolled back, and refusals are recorded after a rollback.
    """

    case: Case
    role: CaseRole
    user: User
    actor: Actor


async def record_denial(
    make_session: async_sessionmaker[AsyncSession],
    request: Request,
    *,
    actor: Actor,
    case_id: uuid.UUID,
    global_role: Role,
    reason: str,
    case_role: CaseRole | None = None,
    required: Capability | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Record a refused case request as ``authz.denied`` in its own transaction.

    ``extra`` adds a few named fields (never request bodies) to the details.
    """
    details: dict[str, object] = {
        **(extra or {}),
        "method": request.method,
        "path": route_path(request),
        "role": global_role.value,
        "reason": reason,
    }
    if case_role is not None:
        details["case_role"] = case_role.value
    if required is not None:
        details["required"] = required.value
    await audit.record_standalone(
        make_session,
        action=AuditAction.AUTHZ_DENIED,
        outcome=AuditOutcome.DENIED,
        actor=actor,
        case_id=case_id,
        details=details,
    )


async def deny_capability(
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
    request: Request,
    access: CaseAccess,
    required: Capability,
) -> NoReturn:
    """Refuse a member who lacks ``required``: 403, recorded."""
    # Read before the rollback, which expires every loaded object.
    global_role, case_id = access.user.role, access.case.id
    await db.rollback()  # release the case lock before recording
    await record_denial(
        make_session,
        request,
        actor=access.actor,
        case_id=case_id,
        global_role=global_role,
        reason="missing_capability",
        case_role=access.role,
        required=required,
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)


def case_access(
    capability: Capability, *, lock: bool = False
) -> Callable[..., Awaitable[CaseAccess]]:
    """Dependency factory: the case in the ``case_id`` path parameter, if the user may use it.

    Non-members get 404 and members without ``capability`` get 403; both are
    recorded. With ``lock=True`` the case row is locked (``FOR NO KEY
    UPDATE``) for the rest of the request's transaction, so changes to the
    case and its members are serialized and the role checked here stays
    current until the commit.
    """

    async def dependency(
        case_id: uuid.UUID,
        request: Request,
        user: Annotated[User, Depends(current_user)],
        db: Annotated[AsyncSession, Depends(get_session)],
        make_session: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
    ) -> CaseAccess:
        actor = Actor.for_user(user, client_ip(request))
        global_role = user.role
        statement = (
            select(Case, CaseMember.role)
            .join(CaseMember, CaseMember.case_id == Case.id)
            .where(Case.id == case_id, CaseMember.user_id == user.id)
        )
        row = None
        if user.is_active and lock:
            locked = statement.with_for_update(key_share=True, of=Case)
            if (await db.execute(locked)).first() is not None:
                # A statement that waited for the lock still returns the rows of
                # its own (older) snapshot, so read the membership again now.
                row = (
                    await db.execute(statement.execution_options(populate_existing=True))
                ).first()
        elif user.is_active:
            row = (await db.execute(statement)).first()

        if row is None:
            await db.rollback()
            await record_denial(
                make_session,
                request,
                actor=actor,
                case_id=case_id,
                global_role=global_role,
                reason="not_a_member",
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, CASE_NOT_FOUND)

        case, stored_role = row
        access = CaseAccess(
            case=case, role=effective_role(global_role, stored_role), user=user, actor=actor
        )
        if not can(access.role, capability):
            await deny_capability(db, make_session, request, access, capability)
        return access

    return dependency
