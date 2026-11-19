"""Sign-in, sign-out, the current user and password changes."""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.service import Actor
from nightloom_api.auth import service, sessions
from nightloom_api.auth.deps import AnySession, client_ip, get_login_rate_limiter
from nightloom_api.auth.ratelimit import LoginRateLimiter
from nightloom_api.auth.schemas import LoginRequest, LoginResponse, PasswordChangeRequest, UserOut
from nightloom_api.config import Settings, get_settings
from nightloom_api.infra.db import get_session, get_sessionmaker

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Sessionmaker = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Limiter = Annotated[LoginRateLimiter, Depends(get_login_rate_limiter)]


def raise_http(error: service.AuthServiceError | service.PasswordPolicyError) -> NoReturn:
    """Translate a service error into the matching HTTP error."""
    match error:
        case service.InvalidCredentialsError():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
        case service.RateLimitedError(retry_after=retry_after):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                str(error),
                headers={"Retry-After": str(max(retry_after, 1))},
            ) from error
        case service.AuthUnavailableError():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
        case service.WrongPasswordError() | service.SelfPasswordResetError():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        case service.PasswordPolicyError():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        case service.EmailTakenError() | service.ConflictError():
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        case service.UserNotFoundError():
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    raise error  # pragma: no cover - every subclass is handled above


def failure_reason(error: service.AuthServiceError | service.PasswordPolicyError) -> str:
    """A short, fixed reason for the audit log (never the error's text)."""
    match error:
        case service.InvalidCredentialsError(reason=reason):
            return reason
        case service.WrongPasswordError():
            return "wrong_current_password"
        case service.PasswordPolicyError():
            return "password_policy"
        case service.EmailTakenError():
            return "email_taken"
        case service.ConflictError():
            return "conflict"
        case service.UserNotFoundError():
            return "not_found"
        case service.SelfPasswordResetError():
            return "self_reset"
    return "error"


async def audit_failure(
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
    *,
    action: AuditAction,
    actor: Actor,
    error: service.AuthServiceError | service.PasswordPolicyError,
    target_type: TargetType | None = None,
    target_id: object = None,
    details: dict[str, object] | None = None,
) -> None:
    """Record a failed or refused request in a transaction of its own.

    A rate-limited request becomes ``auth.rate_limited`` (denied) whatever the
    attempted action was; a service outage (503) is not recorded. The request's
    own transaction is rolled back first, so it holds no locks meanwhile.
    """
    if isinstance(error, service.AuthUnavailableError):
        return
    await db.rollback()
    extra = dict(details or {})
    if isinstance(error, service.RateLimitedError):
        extra.update(attempted=action.value, retry_after=error.retry_after)
        action, outcome = AuditAction.AUTH_RATE_LIMITED, AuditOutcome.DENIED
    else:
        extra["reason"] = failure_reason(error)
        outcome = AuditOutcome.FAILURE
    await audit.record_standalone(
        make_session,
        action=action,
        outcome=outcome,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        details=extra,
    )


_LOGIN_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Invalid email or password"},
    status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many failed attempts"},
}


@router.post("/login", response_model=LoginResponse, responses=_LOGIN_ERRORS)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    make_session: Sessionmaker,
    settings: AppSettings,
    limiter: Limiter,
) -> LoginResponse:
    """Sign in. Sets the HttpOnly session cookie and the CSRF cookie.

    Failures are audited without the typed email: only its SHA-256.
    """
    ip = client_ip(request)
    try:
        result = await service.login(
            db,
            limiter,
            email=body.email,
            password=body.password,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
    except service.AuthServiceError as error:
        await audit_failure(
            db,
            make_session,
            action=AuditAction.AUTH_LOGIN,
            actor=Actor.anonymous(ip),
            error=error,
            details={"email_sha256": audit.email_digest(body.email)},
        )
        raise_http(error)
    sessions.set_auth_cookies(response, result.token, settings)
    return LoginResponse(user=UserOut.model_validate(result.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    ctx: AnySession,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> None:
    """Sign out: delete this session and clear the cookies. Needs the CSRF header.

    Allowed while a password change is required.
    """
    await sessions.delete_session(db, ctx.session.id)
    await audit.record(
        db,
        action=AuditAction.AUTH_LOGOUT,
        outcome=AuditOutcome.SUCCESS,
        actor=Actor.for_user(ctx.user, client_ip(request)),
        target_type=TargetType.USER,
        target_id=ctx.user.id,
    )
    await db.commit()
    sessions.clear_auth_cookies(response, settings)


@router.get("/me", response_model=UserOut)
async def me(ctx: AnySession) -> UserOut:
    """The signed-in user (allowed while a password change is required)."""
    return UserOut.model_validate(ctx.user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    ctx: AnySession,
    db: DbSession,
    make_session: Sessionmaker,
    limiter: Limiter,
) -> None:
    """Change your password. Signs out all your other sessions; this one stays.

    Allowed while a password change is required, and clears that requirement.

    A wrong current password is audited as a failure; a new password that
    fails the policy (422) is not.
    """
    ip = client_ip(request)
    try:
        await service.change_password(
            db,
            limiter,
            ctx,
            current_password=body.current_password,
            new_password=body.new_password,
            ip=ip,
        )
    except (service.AuthServiceError, service.PasswordPolicyError) as error:
        if not isinstance(error, service.PasswordPolicyError):
            await audit_failure(
                db,
                make_session,
                action=AuditAction.AUTH_PASSWORD_CHANGE,
                actor=Actor.for_user(ctx.user, ip),
                error=error,
                target_type=TargetType.USER,
                target_id=ctx.user.id,
            )
        raise_http(error)
