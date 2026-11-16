"""Investigation cases and their members.

Access comes only from case membership (see :mod:`nightloom_api.cases.access`):
non-members get 404 for every route below, members without the needed
capability 403, and both refusals are audited.
"""

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import listing
from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.listing import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, AuditFilters, AuditPage
from nightloom_api.audit.service import Actor
from nightloom_api.auth.deps import CurrentUser, client_ip, require_role
from nightloom_api.auth.models import Role, User
from nightloom_api.cases import service
from nightloom_api.cases.access import (
    Capability,
    CaseAccess,
    can,
    case_access,
    deny_capability,
)
from nightloom_api.cases.models import TITLE_MAX_LENGTH, CaseRole, CaseStatus
from nightloom_api.cases.schemas import (
    CaseCreate,
    CaseDelete,
    CaseOut,
    CasePage,
    CaseUpdate,
    MemberOut,
    MemberPut,
)
from nightloom_api.infra.db import get_session, get_sessionmaker

router = APIRouter(prefix="/cases", tags=["cases"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Sessionmaker = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
CaseCreator = Annotated[User, Depends(require_role(Role.ADMIN, Role.ANALYST))]

Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]
ReadableLocked = Annotated[CaseAccess, Depends(case_access(Capability.READ, lock=True))]
EditableLocked = Annotated[CaseAccess, Depends(case_access(Capability.EDIT, lock=True))]
ManagedLocked = Annotated[CaseAccess, Depends(case_access(Capability.MANAGE, lock=True))]
AuditReadable = Annotated[CaseAccess, Depends(case_access(Capability.READ_AUDIT))]

_CASE_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_403_FORBIDDEN: {"description": "Your case role does not allow this"},
    status.HTTP_404_NOT_FOUND: {"description": "No such case, or you are not a member"},
}


def raise_http(error: service.CaseServiceError) -> NoReturn:
    """Translate a service error into the matching HTTP error."""
    match error:
        case service.NotFoundError():
            code = status.HTTP_404_NOT_FOUND
        case service.ConflictError():
            code = status.HTTP_409_CONFLICT
        case _:
            code = status.HTTP_422_UNPROCESSABLE_CONTENT
    raise HTTPException(code, str(error)) from error


async def audit_failure(
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
    *,
    action: AuditAction,
    actor: Actor,
    case_id: uuid.UUID,
    error: service.CaseServiceError,
    target_type: TargetType,
    target_id: object,
    details: dict[str, object] | None = None,
) -> None:
    """Record a refused change (409, a wrong confirmation, an unknown user) on its own."""
    await db.rollback()  # release the case lock first
    await audit.record_standalone(
        make_session,
        action=action,
        outcome=AuditOutcome.FAILURE,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        case_id=case_id,
        details={**(details or {}), "reason": error.reason},
    )


# --- cases ------------------------------------------------------------------------


@router.post("", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def create_case(
    body: CaseCreate, request: Request, user: CaseCreator, db: DbSession
) -> CaseOut:
    """Create a case (admins and analysts). The creator becomes its owner.

    Needs a purpose, a legal basis and ``ethics_acknowledged: true``.
    """
    case = await service.create_case(db, user, body, actor=Actor.for_user(user, client_ip(request)))
    return CaseOut.build(case, CaseRole.OWNER)


@router.get("", response_model=CasePage)
async def list_cases(
    user: CurrentUser,
    db: DbSession,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    status_filter: Annotated[CaseStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=TITLE_MAX_LENGTH)] = None,
) -> CasePage:
    """Your cases, most recently updated first; ``q`` searches titles (case-insensitive)."""
    try:
        result = await service.list_cases(
            db, user, limit=limit, cursor=cursor, status=status_filter, query=q
        )
    except service.InvalidCursorError as error:
        raise_http(error)
    return CasePage(
        items=[CaseOut.build(case, role) for case, role in result.items],
        next_cursor=result.next_cursor,
    )


@router.get("/{case_id}", response_model=CaseOut, responses=_CASE_ERRORS)
async def get_case(access: Readable) -> CaseOut:
    """One case (any member)."""
    return CaseOut.build(access.case, access.role)


@router.patch("/{case_id}", response_model=CaseOut, responses=_CASE_ERRORS)
async def update_case(
    body: CaseUpdate,
    request: Request,
    access: EditableLocked,
    db: DbSession,
    make_session: Sessionmaker,
) -> CaseOut:
    """Change a case. Editors: title and description. Owners: every field.

    Changing purpose, legal_basis, legal_basis_note or allow_person_entities
    needs ``change_reason``. Closing sets ``closed_at``; reopening clears it.
    """
    plan = service.plan_update(access.case, body)
    if plan.needs_owner and not can(access.role, Capability.MANAGE):
        await deny_capability(db, make_session, request, access, Capability.MANAGE)
    try:
        case = await service.update_case(db, access.case, body, plan, actor=access.actor)
    except service.CaseServiceError as error:
        raise_http(error)
    return CaseOut.build(case, access.role)


@router.delete(
    "/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **_CASE_ERRORS,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "confirm_title does not match"},
    },
)
async def delete_case(
    body: CaseDelete, access: ManagedLocked, db: DbSession, make_session: Sessionmaker
) -> None:
    """Delete a case and its memberships (owners). ``confirm_title`` must equal the title.

    The case's audit events are kept.
    """
    case_id = access.case.id
    try:
        await service.delete_case(db, access.case, body.confirm_title, actor=access.actor)
    except service.CaseServiceError as error:
        await audit_failure(
            db,
            make_session,
            action=AuditAction.CASE_DELETE,
            actor=access.actor,
            case_id=case_id,
            error=error,
            target_type=TargetType.CASE,
            target_id=case_id,
        )
        raise_http(error)


@router.get("/{case_id}/audit", response_model=AuditPage, responses=_CASE_ERRORS)
async def case_audit(
    access: AuditReadable,
    db: DbSession,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    action: AuditAction | None = None,
    outcome: AuditOutcome | None = None,
    since: Annotated[AwareDatetime | None, Query(description="occurred_at >= since")] = None,
    until: Annotated[AwareDatetime | None, Query(description="occurred_at < until")] = None,
) -> AuditPage:
    """This case's audit events (owners and editors), newest first, paged like ``GET /audit``."""
    filters = AuditFilters(
        action=action, outcome=outcome, case_id=access.case.id, since=since, until=until
    )
    return await listing.list_events(db, filters, before_id=before_id, limit=limit)


# --- members ----------------------------------------------------------------------


def member_out(member: service.Member) -> MemberOut:
    return MemberOut(
        user_id=member.user.id,
        email=member.user.email,
        display_name=member.user.display_name,
        global_role=member.user.role,
        is_active=member.user.is_active,
        role=member.member.role,
        added_by=member.member.added_by,
        added_at=member.member.added_at,
    )


@router.get("/{case_id}/members", response_model=list[MemberOut], responses=_CASE_ERRORS)
async def list_members(access: Readable, db: DbSession) -> list[MemberOut]:
    """The case's members (any member), owners first."""
    return [member_out(member) for member in await service.list_members(db, access.case.id)]


@router.put(
    "/{case_id}/members",
    response_model=MemberOut,
    responses={
        **_CASE_ERRORS,
        status.HTTP_201_CREATED: {"model": MemberOut, "description": "Member added"},
        status.HTTP_409_CONFLICT: {"description": "The case would lose its last owner"},
    },
)
async def put_member(
    body: MemberPut,
    response: Response,
    access: ManagedLocked,
    db: DbSession,
    make_session: Sessionmaker,
) -> MemberOut:
    """Add an active user by email (201), or change a member's role (200). Owners only.

    Global viewers can only be case viewers (422).
    """
    case_id, actor = access.case.id, access.actor
    try:
        result = await service.put_member(
            db,
            access.case,
            email=body.email,
            role=body.role,
            added_by=access.user.id,
            actor=actor,
        )
    except (service.NotFoundError, service.ConflictError) as error:
        # An unknown email can only have been an addition; a conflict only a demotion.
        await audit_failure(
            db,
            make_session,
            action=AuditAction.CASE_MEMBER_ADD
            if isinstance(error, service.NotFoundError)
            else AuditAction.CASE_MEMBER_UPDATE,
            actor=actor,
            case_id=case_id,
            error=error,
            target_type=TargetType.CASE,
            target_id=case_id,
            details={"role": body.role.value},
        )
        raise_http(error)
    except service.CaseServiceError as error:
        raise_http(error)
    if result.created:
        response.status_code = status.HTTP_201_CREATED
    return member_out(result.member)


@router.delete(
    "/{case_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **_CASE_ERRORS,
        status.HTTP_409_CONFLICT: {"description": "The case would lose its last owner"},
    },
)
async def remove_member(
    user_id: uuid.UUID,
    request: Request,
    access: ReadableLocked,
    db: DbSession,
    make_session: Sessionmaker,
) -> None:
    """Remove a member. Owners may remove anyone; any member may remove themselves.

    The last owner can never be removed (409).
    """
    if user_id != access.user.id and not can(access.role, Capability.MANAGE):
        await deny_capability(db, make_session, request, access, Capability.MANAGE)
    case_id, actor = access.case.id, access.actor
    try:
        await service.remove_member(db, access.case, user_id, actor=actor)
    except service.CaseServiceError as error:
        await audit_failure(
            db,
            make_session,
            action=AuditAction.CASE_MEMBER_REMOVE,
            actor=actor,
            case_id=case_id,
            error=error,
            target_type=TargetType.USER,
            target_id=user_id,
        )
        raise_http(error)
