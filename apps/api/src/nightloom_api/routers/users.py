"""User management (admins only)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.auth import service
from nightloom_api.auth.deps import AdminUser
from nightloom_api.auth.schemas import UserCreate, UserOut, UserUpdate
from nightloom_api.infra.db import get_session
from nightloom_api.routers.auth import raise_http

router = APIRouter(prefix="/users", tags=["users"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[UserOut])
async def list_users(_admin: AdminUser, db: DbSession) -> list[UserOut]:
    """Every user, oldest first."""
    return [UserOut.model_validate(user) for user in await service.list_users(db)]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, _admin: AdminUser, db: DbSession) -> UserOut:
    """Create a user with an initial password (no public registration)."""
    try:
        user = await service.create_user(
            db,
            email=body.email,
            display_name=body.display_name,
            role=body.role,
            password=body.password,
        )
    except (service.AuthServiceError, service.PasswordPolicyError) as error:
        raise_http(error)
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, admin: AdminUser, db: DbSession
) -> UserOut:
    """Change a user's display name, role or active flag.

    409 if an admin would demote or deactivate themselves, or if the last
    active admin would be demoted or deactivated.
    """
    try:
        user = await service.update_user(db, admin, user_id, body)
    except service.AuthServiceError as error:
        raise_http(error)
    return UserOut.model_validate(user)


@router.delete("/{user_id}/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_sessions(user_id: uuid.UUID, _admin: AdminUser, db: DbSession) -> None:
    """Sign a user out of every session."""
    try:
        await service.revoke_sessions(db, user_id)
    except service.AuthServiceError as error:
        raise_http(error)
