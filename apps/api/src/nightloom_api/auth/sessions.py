"""Server-side sessions: tokens, cookies, CSRF tokens and the ``sessions`` table.

The browser holds a random 256-bit session token in an HttpOnly cookie. The
database stores only its SHA-256, so a leaked ``sessions`` table cannot be
replayed. Sessions expire after :data:`IDLE_TIMEOUT` without use and, at the
latest, :data:`ABSOLUTE_LIFETIME` after sign-in.
"""

import hashlib
import hmac
import ipaddress
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.auth import clock
from nightloom_api.auth.models import USER_AGENT_MAX_LENGTH, Session, User
from nightloom_api.config import Settings

SESSION_COOKIE = "nightloom_session"
CSRF_COOKIE = "nightloom_csrf"
CSRF_HEADER = "X-CSRF-Token"

TOKEN_BYTES = 32
IDLE_TIMEOUT = timedelta(hours=24)
ABSOLUTE_LIFETIME = timedelta(days=7)
# ``last_seen_at`` is written at most this often, not on every request.
TOUCH_INTERVAL = timedelta(minutes=1)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def new_token() -> str:
    """A fresh session token: 32 random bytes, URL-safe base64."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The hex SHA-256 of a session token, as stored in ``sessions.token_hash``."""
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token_for(session_token: str) -> str:
    """The CSRF token that belongs to a session token.

    Derived from the secret session token with a domain-separated hash, so it
    is unpredictable without the session cookie and an attacker who can plant
    cookies still cannot pair a CSRF cookie with someone else's session.
    """
    return hashlib.sha256(b"nightloom-csrf:" + session_token.encode()).hexdigest()


def csrf_matches(header: str | None, cookie: str | None, session_token: str) -> bool:
    """Double-submit check: header == cookie == the session's CSRF token (constant time)."""
    if not header or not cookie:
        return False
    expected = csrf_token_for(session_token).encode()
    header_ok = hmac.compare_digest(header.encode(), cookie.encode())
    session_ok = hmac.compare_digest(header.encode(), expected)
    return header_ok and session_ok


def set_auth_cookies(response: Response, token: str, settings: Settings) -> None:
    """Set the HttpOnly session cookie and the script-readable CSRF cookie."""
    max_age = int(ABSOLUTE_LIFETIME.total_seconds())
    secure = settings.cookies_secure
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token_for(token),
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=False,
        samesite="strict",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Expire both auth cookies in the browser."""
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name, path="/", secure=settings.cookies_secure, httponly=httponly, samesite="strict"
        )


def _clean_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return None


async def create_session(
    db: AsyncSession, user: User, *, ip: str | None, user_agent: str | None
) -> str:
    """Store a new session for ``user`` and return its (unhashed) token.

    The caller commits.
    """
    token = new_token()
    now = clock.utcnow()
    db.add(
        Session(
            user_id=user.id,
            token_hash=hash_token(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + ABSOLUTE_LIFETIME,
            ip=_clean_ip(ip),
            user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
        )
    )
    await db.flush()
    return token


@dataclass(frozen=True)
class AuthContext:
    """The signed-in user and their session, as resolved for one request."""

    user: User
    session: Session
    token: str


def is_expired(session: Session) -> bool:
    """Whether the session has passed its idle timeout or absolute lifetime."""
    now = clock.utcnow()
    return now >= session.expires_at or now - session.last_seen_at >= IDLE_TIMEOUT


async def resolve(db: AsyncSession, token: str) -> AuthContext | None:
    """Look up a session by token and refresh its idle timer.

    Returns ``None`` (and deletes the row) when the session is expired or its
    user is deactivated. Commits any change.
    """
    row = (
        await db.execute(
            select(Session, User)
            .join(User, Session.user_id == User.id)
            .where(Session.token_hash == hash_token(token))
        )
    ).first()
    if row is None:
        return None
    session, user = row

    if is_expired(session) or not user.is_active:
        await db.delete(session)
        await db.commit()
        return None

    now = clock.utcnow()
    if now - session.last_seen_at >= TOUCH_INTERVAL:
        session.last_seen_at = now
        await db.commit()
    return AuthContext(user=user, session=session, token=token)


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    """Delete one session. The caller commits."""
    await db.execute(delete(Session).where(Session.id == session_id))


async def delete_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, *, keep: uuid.UUID | None = None
) -> int:
    """Delete every session of ``user_id`` except ``keep``; return how many. The caller commits."""
    statement = delete(Session).where(Session.user_id == user_id)
    if keep is not None:
        statement = statement.where(Session.id != keep)
    result = await db.execute(statement)
    return int(result.rowcount)  # type: ignore[attr-defined]


async def delete_expired_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Housekeeping on sign-in: drop this user's expired sessions. The caller commits."""
    now = clock.utcnow()
    await db.execute(
        delete(Session).where(
            Session.user_id == user_id,
            (Session.expires_at <= now) | (Session.last_seen_at <= now - IDLE_TIMEOUT),
        )
    )
