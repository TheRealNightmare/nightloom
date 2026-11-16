"""Authentication and user-management operations.

Plain async functions over an ``AsyncSession`` (and a rate limiter for
sign-in), independent of HTTP. Failures are raised as :class:`AuthServiceError`
subclasses, which the routers translate into status codes.
"""

import contextlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.auth import clock, passwords, sessions
from nightloom_api.auth.models import Role, User
from nightloom_api.auth.passwords import PasswordPolicyError
from nightloom_api.auth.ratelimit import LoginRateLimiter
from nightloom_api.auth.schemas import UserUpdate, normalize_email
from nightloom_api.auth.sessions import AuthContext

INVALID_CREDENTIALS = "Invalid email or password."
# Longer inputs cannot be a valid password; refuse them before hashing.
MAX_LOGIN_PASSWORD_LENGTH = 1024


class AuthServiceError(Exception):
    """Base class; ``str(error)`` is safe to return to the client."""


class InvalidCredentialsError(AuthServiceError):
    def __init__(self) -> None:
        super().__init__(INVALID_CREDENTIALS)


class RateLimitedError(AuthServiceError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many failed attempts. Try again later.")
        self.retry_after = retry_after


class AuthUnavailableError(AuthServiceError):
    def __init__(self) -> None:
        super().__init__("Sign-in is temporarily unavailable. Try again shortly.")


class WrongPasswordError(AuthServiceError):
    def __init__(self) -> None:
        super().__init__("Current password is incorrect.")


class EmailTakenError(AuthServiceError):
    def __init__(self) -> None:
        super().__init__("A user with this email already exists.")


class UserNotFoundError(AuthServiceError):
    def __init__(self) -> None:
        super().__init__("User not found.")


class ConflictError(AuthServiceError):
    """The change would leave the system in a state that is not allowed."""


__all__ = [
    "INVALID_CREDENTIALS",
    "AuthServiceError",
    "AuthUnavailableError",
    "ConflictError",
    "EmailTakenError",
    "InvalidCredentialsError",
    "LoginResult",
    "PasswordPolicyError",
    "RateLimitedError",
    "UserNotFoundError",
    "WrongPasswordError",
    "change_password",
    "create_user",
    "list_users",
    "login",
    "revoke_sessions",
    "update_user",
]


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def _check_limit(limiter: LoginRateLimiter, email: str, ip: str | None) -> None:
    try:
        retry_after = await limiter.retry_after(email, ip)
    except RedisError as exc:
        raise AuthUnavailableError from exc
    if retry_after is not None:
        raise RateLimitedError(retry_after)


async def _record_failure(limiter: LoginRateLimiter, email: str, ip: str | None) -> None:
    try:
        await limiter.record_failure(email, ip)
    except RedisError as exc:
        raise AuthUnavailableError from exc


async def login(
    db: AsyncSession,
    limiter: LoginRateLimiter,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> LoginResult:
    """Verify the credentials and open a session.

    Every failure (unknown email, wrong password, inactive user) raises the
    same :class:`InvalidCredentialsError` after a full-cost hash check.
    """
    email = normalize_email(email)
    await _check_limit(limiter, email, ip)

    if len(password) > MAX_LOGIN_PASSWORD_LENGTH:
        await _record_failure(limiter, email, ip)
        raise InvalidCredentialsError

    user = await _get_user_by_email(db, email)
    if user is None:
        passwords.dummy_verify(password)
        await _record_failure(limiter, email, ip)
        raise InvalidCredentialsError

    valid, new_hash = passwords.verify_password(password, user.password_hash)
    if not valid or not user.is_active:
        await _record_failure(limiter, email, ip)
        raise InvalidCredentialsError

    if new_hash is not None:
        user.password_hash = new_hash
    user.last_login_at = clock.utcnow()
    await sessions.delete_expired_sessions(db, user.id)
    token = await sessions.create_session(db, user, ip=ip, user_agent=user_agent)
    await db.commit()

    # The sign-in succeeded; if Valkey hiccups now, the counter just expires later.
    with contextlib.suppress(RedisError):
        await limiter.reset_email(email)
    return LoginResult(user=user, token=token)


async def change_password(
    db: AsyncSession,
    limiter: LoginRateLimiter,
    ctx: AuthContext,
    *,
    current_password: str,
    new_password: str,
    ip: str | None,
) -> int:
    """Change the signed-in user's password and sign out their other sessions.

    Wrong current passwords count against the same limits as sign-in. Returns
    the number of revoked sessions.
    """
    user = ctx.user
    await _check_limit(limiter, user.email, ip)
    valid = (
        len(current_password) <= MAX_LOGIN_PASSWORD_LENGTH
        and (passwords.verify_password(current_password, user.password_hash)[0])
    )
    if not valid:
        await _record_failure(limiter, user.email, ip)
        raise WrongPasswordError

    passwords.validate_password(new_password, user.email)
    if new_password == current_password:
        raise PasswordPolicyError("New password must differ from the current one.")

    user.password_hash = passwords.hash_password(new_password)
    revoked = await sessions.delete_user_sessions(db, user.id, keep=ctx.session.id)
    await db.commit()
    return revoked


async def create_user(
    db: AsyncSession, *, email: str, display_name: str, role: Role, password: str
) -> User:
    """Create a user. Raises :class:`EmailTakenError` or :class:`PasswordPolicyError`."""
    email = normalize_email(email)
    passwords.validate_password(password, email)
    if await _get_user_by_email(db, email) is not None:
        raise EmailTakenError

    user = User(
        email=email,
        display_name=display_name.strip(),
        role=role,
        password_hash=passwords.hash_password(password),
        is_active=True,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:  # created concurrently
        await db.rollback()
        raise EmailTakenError from exc
    return user


async def list_users(db: AsyncSession) -> Sequence[User]:
    """Every user, oldest first."""
    return (await db.execute(select(User).order_by(User.created_at, User.email))).scalars().all()


async def update_user(
    db: AsyncSession, actor: User, user_id: uuid.UUID, changes: UserUpdate
) -> User:
    """Apply an admin's changes to a user.

    An admin cannot demote or deactivate themselves, and the last active admin
    cannot be demoted or deactivated (:class:`ConflictError`). Deactivating a
    user signs out all of their sessions.
    """
    # Lock the active admins first (always in the same order), so two admins
    # demoting each other at the same time cannot both succeed.
    active_admins = (
        (
            await db.execute(
                select(User.id)
                .where(User.role == Role.ADMIN, User.is_active.is_(True))
                .order_by(User.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
    if user is None:
        raise UserNotFoundError

    new_role = changes.role if changes.role is not None else user.role
    new_active = changes.is_active if changes.is_active is not None else user.is_active
    loses_admin = (
        user.role == Role.ADMIN and user.is_active and (new_role != Role.ADMIN or not new_active)
    )
    if loses_admin:
        if user.id == actor.id:
            raise ConflictError("You cannot remove your own admin role or deactivate yourself.")
        if len(active_admins) <= 1:
            raise ConflictError("At least one active admin must remain.")

    if changes.display_name is not None:
        user.display_name = changes.display_name
    user.role = new_role
    if user.is_active and not new_active:
        await sessions.delete_user_sessions(db, user.id)
    user.is_active = new_active
    await db.commit()
    await db.refresh(user)
    return user


async def revoke_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Sign a user out everywhere. Returns the number of revoked sessions."""
    if await db.get(User, user_id) is None:
        raise UserNotFoundError
    revoked = await sessions.delete_user_sessions(db, user_id)
    await db.commit()
    return revoked
