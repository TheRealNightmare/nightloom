"""FastAPI dependencies: who is signed in, and are they allowed to do this?"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.auth import sessions
from nightloom_api.auth.models import Role, User
from nightloom_api.auth.ratelimit import LoginRateLimiter
from nightloom_api.auth.sessions import AuthContext
from nightloom_api.infra.cache import get_cache
from nightloom_api.infra.db import get_session

NOT_AUTHENTICATED = "Not authenticated."
CSRF_FAILED = "CSRF token missing or invalid."
FORBIDDEN = "You do not have permission to do this."


def client_ip(request: Request) -> str | None:
    """The client's address.

    Behind a trusted reverse proxy (uvicorn's ``FORWARDED_ALLOW_IPS``) this is
    taken from ``X-Forwarded-For``; otherwise it is the TCP peer.
    """
    return request.client.host if request.client else None


def get_login_rate_limiter(cache: Annotated[Redis, Depends(get_cache)]) -> LoginRateLimiter:
    """FastAPI dependency: the sign-in rate limiter (overridden in tests)."""
    return LoginRateLimiter(cache)


async def current_session(
    request: Request, db: Annotated[AsyncSession, Depends(get_session)]
) -> AuthContext:
    """The request's valid session, or 401.

    Requests with an unsafe method (POST, PUT, PATCH, DELETE) must also send
    the CSRF token in the ``X-CSRF-Token`` header, or they get 403.
    """
    token = request.cookies.get(sessions.SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_AUTHENTICATED)
    ctx = await sessions.resolve(db, token)
    if ctx is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_AUTHENTICATED)

    if request.method not in sessions.SAFE_METHODS and not sessions.csrf_matches(
        request.headers.get(sessions.CSRF_HEADER),
        request.cookies.get(sessions.CSRF_COOKIE),
        token,
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, CSRF_FAILED)
    return ctx


async def current_user(ctx: Annotated[AuthContext, Depends(current_session)]) -> User:
    """The signed-in user, or 401 (403 on a failed CSRF check)."""
    return ctx.user


def check_role(user: User, roles: frozenset[Role]) -> User:
    """Return ``user`` if their role is one of ``roles``, else raise 403."""
    if user.role not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    return user


def require_role(*roles: Role) -> Callable[[User], Awaitable[User]]:
    """Dependency factory: the signed-in user, if they have one of ``roles`` (else 403)."""
    if not roles:
        raise ValueError("require_role() needs at least one role")
    allowed = frozenset(roles)

    async def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        return check_role(user, allowed)

    return dependency


CurrentSession = Annotated[AuthContext, Depends(current_session)]
CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(require_role(Role.ADMIN))]
