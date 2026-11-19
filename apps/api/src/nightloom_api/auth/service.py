"""Authentication and user-management operations.

Plain async functions over an ``AsyncSession`` (and a rate limiter for
sign-in), independent of HTTP. Failures are raised as :class:`AuthServiceError`
subclasses, which the routers translate into status codes.

Every successful change records its audit event in the same transaction, right
before committing, so a change and its event commit or roll back together.
Failures change nothing; the routers record those in a transaction of their own.
"""

import contextlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.service import Actor
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
    """Sign-in failed. ``reason`` is for the audit log only; clients get one message."""

    def __init__(self, reason: str = "invalid_credentials") -> None:
        super().__init__(INVALID_CREDENTIALS)
        self.reason = reason


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


class SelfPasswordResetError(AuthServiceError):
    def __init__(self) -> None:
        super().__init__("You cannot reset your own password. Use Change password instead.")


__all__ = [
    "INVALID_CREDENTIALS",
    "AuthServiceError",
    "AuthUnavailableError",
    "ConflictError",
    "EmailTakenError",
    "InvalidCredentialsError",
    "LoginResult",
    "PasswordPolicyError",
    "PasswordResetResult",
    "RateLimitedError",
    "SelfPasswordResetError",
    "UserNotFoundError",
    "WrongPasswordError",
    "change_password",
    "create_user",
    "list_users",
    "login",
    "reset_password",
    "revoke_sessions",
    "update_user",
]


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str


@dataclass(frozen=True)
class PasswordResetResult:
    user: User
    temporary_password: str
    revoked_sessions: int


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
        raise InvalidCredentialsError("password_too_long")

    user = await _get_user_by_email(db, email)
    if user is None:
        await passwords.dummy_verify_async(password)
        await _record_failure(limiter, email, ip)
        raise InvalidCredentialsError("unknown_email")

    valid, new_hash = await passwords.verify_password_async(password, user.password_hash)
    if not valid or not user.is_active:
        await _record_failure(limiter, email, ip)
        raise InvalidCredentialsError("wrong_password" if not valid else "inactive_user")

    if new_hash is not None:
        user.password_hash = new_hash
    user.last_login_at = clock.utcnow()
    await sessions.delete_expired_sessions(db, user.id)
    token = await sessions.create_session(db, user, ip=ip, user_agent=user_agent)
    await audit.record(
        db,
        action=AuditAction.AUTH_LOGIN,
        outcome=AuditOutcome.SUCCESS,
        actor=Actor.for_user(user, ip),
        target_type=TargetType.USER,
        target_id=user.id,
    )
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
        and (await passwords.verify_password_async(current_password, user.password_hash))[0]
    )
    if not valid:
        await _record_failure(limiter, user.email, ip)
        raise WrongPasswordError

    passwords.validate_password(new_password, user.email)
    if new_password == current_password:
        raise PasswordPolicyError("New password must differ from the current one.")

    user.password_hash = await passwords.hash_password_async(new_password)
    required = user.must_change_password
    user.must_change_password = False
    revoked = await sessions.delete_user_sessions(db, user.id, keep=ctx.session.id)
    await audit.record(
        db,
        action=AuditAction.AUTH_PASSWORD_CHANGE,
        outcome=AuditOutcome.SUCCESS,
        actor=Actor.for_user(user, ip),
        target_type=TargetType.USER,
        target_id=user.id,
        # "password" in a key would be dropped by the sanitizer.
        details={"revoked_sessions": revoked, "change_required": required},
    )
    await db.commit()
    return revoked


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    display_name: str,
    role: Role,
    password: str,
    actor: Actor,
    must_change_password: bool = False,
    generated_password: bool = False,
) -> User:
    """Create a user. Raises :class:`EmailTakenError` or :class:`PasswordPolicyError`.

    ``actor`` is who creates it, for the audit log. ``generated_password``
    says whether ``password`` came from
    :func:`~nightloom_api.auth.passwords.generate_temporary_password` (audited
    as the kind of credential; the password itself never is).
    """
    email = normalize_email(email)
    passwords.validate_password(password, email)
    if await _get_user_by_email(db, email) is not None:
        raise EmailTakenError

    user = User(
        email=email,
        display_name=display_name.strip(),
        role=role,
        password_hash=await passwords.hash_password_async(password),
        is_active=True,
        must_change_password=must_change_password,
    )
    db.add(user)
    try:
        await db.flush()
        await audit.record(
            db,
            action=AuditAction.USER_CREATE,
            outcome=AuditOutcome.SUCCESS,
            actor=actor,
            target_type=TargetType.USER,
            target_id=user.id,
            details={
                "email": user.email,
                "role": role.value,
                # Which kind of credential, never the credential itself. Keys
                # avoid "password", which the sanitizer drops.
                "credential": "generated" if generated_password else "supplied",
                "change_required": must_change_password,
            },
        )
        await db.commit()
    except IntegrityError as exc:  # created concurrently
        await db.rollback()
        raise EmailTakenError from exc
    return user


async def list_users(db: AsyncSession) -> Sequence[User]:
    """Every user, oldest first."""
    return (await db.execute(select(User).order_by(User.created_at, User.email))).scalars().all()


async def update_user(
    db: AsyncSession,
    actor: User,
    user_id: uuid.UUID,
    changes: UserUpdate,
    *,
    ip: str | None = None,
) -> User:
    """Apply an admin's changes to a user.

    An admin cannot demote or deactivate themselves, and the last active admin
    cannot be demoted or deactivated (:class:`ConflictError`). Deactivating a
    user signs out all of their sessions. ``ip`` is the admin's, for the audit log.
    """
    # Lock the active admins first (always in the same order), so two admins
    # demoting each other at the same time cannot both succeed. FOR NO KEY
    # UPDATE (key_share=True) still serializes those changes but, unlike FOR
    # UPDATE, does not block the KEY SHARE lock that inserting an audit event
    # (or session) referencing one of these users takes: otherwise this could
    # deadlock with a concurrent audit append, which holds the chain lock.
    active_admins = (
        (
            await db.execute(
                select(User.id)
                .where(User.role == Role.ADMIN, User.is_active.is_(True))
                .order_by(User.id)
                .with_for_update(key_share=True)
            )
        )
        .scalars()
        .all()
    )
    user = await db.get(User, user_id, with_for_update={"key_share": True}, populate_existing=True)
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

    changed: dict[str, dict[str, object]] = {}
    for field, old, new in (
        ("display_name", user.display_name, changes.display_name or user.display_name),
        ("role", user.role.value, new_role.value),
        ("is_active", user.is_active, new_active),
    ):
        if old != new:
            changed[field] = {"from": old, "to": new}

    if changes.display_name is not None:
        user.display_name = changes.display_name
    user.role = new_role
    revoked = 0
    if user.is_active and not new_active:
        revoked = await sessions.delete_user_sessions(db, user.id)
    user.is_active = new_active
    details: dict[str, object] = {"changes": changed}
    if revoked:
        details["revoked_sessions"] = revoked
    await audit.record(
        db,
        action=AuditAction.USER_UPDATE,
        outcome=AuditOutcome.SUCCESS,
        actor=Actor.for_user(actor, ip),
        target_type=TargetType.USER,
        target_id=user.id,
        details=details,
    )
    await db.commit()
    await db.refresh(user)
    return user


async def revoke_sessions(db: AsyncSession, user_id: uuid.UUID, *, actor: Actor) -> int:
    """Sign a user out everywhere. Returns the number of revoked sessions."""
    if await db.get(User, user_id) is None:
        raise UserNotFoundError
    revoked = await sessions.delete_user_sessions(db, user_id)
    await audit.record(
        db,
        action=AuditAction.USER_SESSIONS_REVOKE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.USER,
        target_id=user_id,
        details={"revoked_sessions": revoked},
    )
    await db.commit()
    return revoked


async def reset_password(
    db: AsyncSession, actor: User, user_id: uuid.UUID, *, ip: str | None = None
) -> PasswordResetResult:
    """Give a user a new temporary password and sign them out everywhere.

    They must choose a new password at their next sign-in. An admin cannot
    reset their own password (:class:`SelfPasswordResetError`): they change it
    with their current one instead. ``ip`` is the admin's, for the audit log.
    """
    if user_id == actor.id:
        raise SelfPasswordResetError
    user = await db.get(User, user_id, with_for_update={"key_share": True}, populate_existing=True)
    if user is None:
        raise UserNotFoundError
    temporary = passwords.generate_temporary_password(user.email)
    user.password_hash = await passwords.hash_password_async(temporary)
    user.must_change_password = True
    revoked = await sessions.delete_user_sessions(db, user.id)
    await audit.record(
        db,
        action=AuditAction.USER_PASSWORD_RESET,
        outcome=AuditOutcome.SUCCESS,
        actor=Actor.for_user(actor, ip),
        target_type=TargetType.USER,
        target_id=user.id,
        details={"revoked_sessions": revoked},
    )
    await db.commit()
    await db.refresh(user)
    return PasswordResetResult(user=user, temporary_password=temporary, revoked_sessions=revoked)
