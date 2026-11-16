"""User management (admins only). Every change, and every refused one, is audited."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit.actions import AuditAction, TargetType
from nightloom_api.audit.service import Actor
from nightloom_api.auth import service
from nightloom_api.auth.deps import AdminUser, client_ip
from nightloom_api.auth.schemas import UserCreate, UserOut, UserUpdate
from nightloom_api.infra.db import get_session
from nightloom_api.routers.auth import Sessionmaker, audit_failure, raise_http

router = APIRouter(prefix="/users", tags=["users"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[UserOut])
async def list_users(_admin: AdminUser, db: DbSession) -> list[UserOut]:
    """Every user, oldest first."""
    return [UserOut.model_validate(user) for user in await service.list_users(db)]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    request: Request,
    admin: AdminUser,
    db: DbSession,
    make_session: Sessionmaker,
) -> UserOut:
    """Create a user with an initial password (no public registration)."""
    actor = Actor.for_user(admin, client_ip(request))
    try:
        user = await service.create_user(
            db,
            email=body.email,
            display_name=body.display_name,
            role=body.role,
            password=body.password,
            actor=actor,
        )
    except (service.AuthServiceError, service.PasswordPolicyError) as error:
        await audit_failure(
            db,
            make_session,
            action=AuditAction.USER_CREATE,
            actor=actor,
            error=error,
            details={"role": body.role.value},
        )
        raise_http(error)
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    admin: AdminUser,
    db: DbSession,
    make_session: Sessionmaker,
) -> UserOut:
    """Change a user's display name, role or active flag.

    409 if an admin would demote or deactivate themselves, or if the last
    active admin would be demoted or deactivated.
    """
    ip = client_ip(request)
    actor = Actor.for_user(admin, ip)
    try:
        user = await service.update_user(db, admin, user_id, body, ip=ip)
    except service.AuthServiceError as error:
        await audit_failure(
            db,
            make_session,
            action=AuditAction.USER_UPDATE,
            actor=actor,
            error=error,
            target_type=TargetType.USER,
            target_id=user_id,
        )
        raise_http(error)
    return UserOut.model_validate(user)


@router.delete("/{user_id}/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_sessions(
    user_id: uuid.UUID,
    request: Request,
    admin: AdminUser,
    db: DbSession,
    make_session: Sessionmaker,
) -> None:
    """Sign a user out of every session."""
    actor = Actor.for_user(admin, client_ip(request))
    try:
        await service.revoke_sessions(db, user_id, actor=actor)
    except service.AuthServiceError as error:
        await audit_failure(
            db,
            make_session,
            action=AuditAction.USER_SESSIONS_REVOKE,
            actor=actor,
            error=error,
            target_type=TargetType.USER,
            target_id=user_id,
        )
        raise_http(error)
