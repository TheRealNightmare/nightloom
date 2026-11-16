"""Sign-in, sign-out, the current user and password changes."""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.auth import service, sessions
from nightloom_api.auth.deps import CurrentSession, client_ip, get_login_rate_limiter
from nightloom_api.auth.ratelimit import LoginRateLimiter
from nightloom_api.auth.schemas import LoginRequest, LoginResponse, PasswordChangeRequest, UserOut
from nightloom_api.config import Settings, get_settings
from nightloom_api.infra.db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
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
        case service.WrongPasswordError():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        case service.PasswordPolicyError():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        case service.EmailTakenError() | service.ConflictError():
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        case service.UserNotFoundError():
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    raise error  # pragma: no cover - every subclass is handled above


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
    settings: AppSettings,
    limiter: Limiter,
) -> LoginResponse:
    """Sign in. Sets the HttpOnly session cookie and the CSRF cookie."""
    try:
        result = await service.login(
            db,
            limiter,
            email=body.email,
            password=body.password,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except service.AuthServiceError as error:
        raise_http(error)
    sessions.set_auth_cookies(response, result.token, settings)
    return LoginResponse(user=UserOut.model_validate(result.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    ctx: CurrentSession, response: Response, db: DbSession, settings: AppSettings
) -> None:
    """Sign out: delete this session and clear the cookies. Needs the CSRF header."""
    await sessions.delete_session(db, ctx.session.id)
    await db.commit()
    sessions.clear_auth_cookies(response, settings)


@router.get("/me", response_model=UserOut)
async def me(ctx: CurrentSession) -> UserOut:
    """The signed-in user."""
    return UserOut.model_validate(ctx.user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    ctx: CurrentSession,
    db: DbSession,
    limiter: Limiter,
) -> None:
    """Change your password. Signs out all your other sessions; this one stays."""
    try:
        await service.change_password(
            db,
            limiter,
            ctx,
            current_password=body.current_password,
            new_password=body.new_password,
            ip=client_ip(request),
        )
    except (service.AuthServiceError, service.PasswordPolicyError) as error:
        raise_http(error)
