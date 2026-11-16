"""Sign-in, sessions, CSRF, rate limiting and password changes against real services."""

import asyncio
import hashlib
import itertools
import time
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from httpx import AsyncClient
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import func, select

from nightloom_api.auth import passwords, ratelimit, sessions
from nightloom_api.auth.models import Role, Session, User
from nightloom_api.infra import Infra
from support import ADMIN_EMAIL, PASSWORD, PREFIX, FakeClock, csrf, login, set_cookie_headers

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]
INVALID = {"detail": "Invalid email or password."}


async def session_rows(infra: Infra) -> list[Session]:
    async with infra.sessionmaker() as db:
        return list((await db.execute(select(Session))).scalars().all())


async def fetch_user(infra: Infra, email: str) -> User:
    async with infra.sessionmaker() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


# --- full flow -------------------------------------------------------------------


async def test_login_me_logout_flow(
    db_client: AsyncClient, make_user: UserFactory, infra: Infra
) -> None:
    await make_user()

    response = await login(db_client, email="  Admin@Example.ORG ")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == ADMIN_EMAIL
    assert body["user"]["role"] == "admin"
    assert body["user"]["last_login_at"] is not None
    assert "password_hash" not in response.text
    assert "$argon2" not in response.text

    cookies = set_cookie_headers(response)
    session_cookie = cookies[sessions.SESSION_COOKIE]
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie
    assert "Path=/" in session_cookie
    assert "Secure" not in session_cookie  # test env, cookie_secure off
    assert "HttpOnly" not in cookies[sessions.CSRF_COOKIE]

    me = await db_client.get(f"{PREFIX}/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == ADMIN_EMAIL

    token = db_client.cookies[sessions.SESSION_COOKIE]
    no_csrf = await db_client.post(f"{PREFIX}/auth/logout")
    assert no_csrf.status_code == 403
    assert no_csrf.json() == {"detail": "CSRF token missing or invalid."}

    logout = await db_client.post(f"{PREFIX}/auth/logout", headers=csrf(db_client))
    assert logout.status_code == 204
    cleared = set_cookie_headers(logout)
    assert set(cleared) == {sessions.SESSION_COOKIE, sessions.CSRF_COOKIE}
    assert all("Max-Age=0" in header for header in cleared.values())
    assert await session_rows(infra) == []

    assert (await db_client.get(f"{PREFIX}/auth/me")).status_code == 401
    # Replaying the old token does not work either: the row is gone.
    db_client.cookies.set(sessions.SESSION_COOKIE, token)
    replay = await db_client.get(f"{PREFIX}/auth/me")
    assert replay.status_code == 401


async def test_only_the_token_hash_is_stored(
    db_client: AsyncClient, make_user: UserFactory, infra: Infra
) -> None:
    await make_user()
    await login(db_client, password=PASSWORD)
    token = db_client.cookies[sessions.SESSION_COOKIE]

    [row] = await session_rows(infra)

    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in {row.token_hash, row.ip, row.user_agent}
    assert row.ip == "127.0.0.1"
    assert row.expires_at == row.created_at + sessions.ABSOLUTE_LIFETIME


async def test_user_agent_is_truncated(
    db_client: AsyncClient, make_user: UserFactory, infra: Infra
) -> None:
    await make_user()

    await db_client.post(
        f"{PREFIX}/auth/login",
        json={"email": ADMIN_EMAIL, "password": PASSWORD},
        headers={"user-agent": "x" * 1000},
    )

    [row] = await session_rows(infra)
    assert row.user_agent == "x" * 256


# --- failures --------------------------------------------------------------------


async def test_wrong_password(db_client: AsyncClient, make_user: UserFactory) -> None:
    await make_user()

    response = await login(db_client, password="not the password")

    assert response.status_code == 401
    assert response.json() == INVALID
    assert "set-cookie" not in response.headers


async def test_unknown_email_runs_a_dummy_hash(
    db_client: AsyncClient, make_user: UserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user()
    calls: list[str] = []
    real = passwords.dummy_verify

    def spy(password: str) -> None:
        calls.append(password)
        real(password)

    monkeypatch.setattr(passwords, "dummy_verify", spy)

    response = await login(db_client, email="nobody@example.org", password="some password")

    assert response.status_code == 401
    assert response.json() == INVALID
    assert calls == ["some password"]


async def test_inactive_user_cannot_log_in(db_client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(is_active=False)

    response = await login(db_client)

    assert response.status_code == 401
    assert response.json() == INVALID


async def test_overlong_password_is_rejected_without_hashing(
    db_client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user()

    response = await login(db_client, password="x" * 5000)

    assert response.status_code == 401
    assert "x" * 100 not in response.text


async def test_outdated_hash_is_upgraded_on_login(
    db_client: AsyncClient, make_user: UserFactory, infra: Infra
) -> None:
    user = await make_user()
    weak = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8192, parallelism=1),))
    async with infra.sessionmaker() as db:
        stored = await db.get(User, user.id)
        assert stored is not None
        stored.password_hash = weak.hash(PASSWORD)
        await db.commit()

    assert (await login(db_client)).status_code == 200

    upgraded = await fetch_user(infra, ADMIN_EMAIL)
    assert "m=65536,t=3,p=4" in upgraded.password_hash
    assert passwords.verify_password(PASSWORD, upgraded.password_hash) == (True, None)


# --- rate limiting ---------------------------------------------------------------


async def test_sixth_wrong_password_is_rate_limited(
    db_client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user()

    for _ in range(ratelimit.MAX_FAILURES_PER_EMAIL):
        assert (await login(db_client, password="wrong password!")).status_code == 401

    limited = await login(db_client, password="wrong password!")
    assert limited.status_code == 429
    retry_after = int(limited.headers["retry-after"])
    assert 0 < retry_after <= ratelimit.WINDOW_SECONDS
    # Even the right password is refused until the window ends.
    assert (await login(db_client)).status_code == 429


async def test_email_limit_is_per_normalized_email(
    db_client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user()
    for variant in ["admin@example.org", "ADMIN@example.org", " Admin@Example.org "] * 2:
        await login(db_client, email=variant, password="wrong password!")

    assert (await login(db_client)).status_code == 429


async def test_success_resets_the_email_counter(
    db_client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user()
    for _ in range(ratelimit.MAX_FAILURES_PER_EMAIL - 1):
        await login(db_client, password="wrong password!")
    assert (await login(db_client)).status_code == 200

    for _ in range(ratelimit.MAX_FAILURES_PER_EMAIL - 1):
        assert (await login(db_client, password="wrong password!")).status_code == 401
    assert (await login(db_client)).status_code == 200


async def test_ip_limit_spans_emails(db_client: AsyncClient, make_user: UserFactory) -> None:
    await make_user()
    for index in range(ratelimit.MAX_FAILURES_PER_IP):
        response = await login(db_client, email=f"guess{index}@example.org", password="nope nope!")
        assert response.status_code == 401

    limited = await login(db_client)  # different email, same IP
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


async def test_rate_limit_keys_do_not_contain_emails(
    db_client: AsyncClient, make_user: UserFactory, infra: Infra
) -> None:
    await make_user()
    await login(db_client, password="wrong password!")

    keys = [key.decode() if isinstance(key, bytes) else key for key in await infra.cache.keys("*")]
    assert keys
    assert all(key.startswith(ratelimit.KEY_PREFIX) for key in keys)
    assert not any("example.org" in key for key in keys)
    for key in keys:
        assert 0 < await infra.cache.ttl(key) <= ratelimit.WINDOW_SECONDS


# --- CSRF ------------------------------------------------------------------------


async def test_csrf_is_required_on_unsafe_methods(
    db_client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user()
    await login(db_client)
    good = csrf(db_client)[sessions.CSRF_HEADER]
    url = f"{PREFIX}/auth/password"
    body = {"current_password": PASSWORD, "new_password": "a brand new password"}

    assert (await db_client.post(url, json=body)).status_code == 403
    assert (
        await db_client.post(url, json=body, headers={sessions.CSRF_HEADER: "forged"})
    ).status_code == 403

    # Header and cookie agree but belong to no session (e.g. a planted cookie).
    forged = sessions.csrf_token_for(sessions.new_token())
    db_client.cookies.set(sessions.CSRF_COOKIE, forged)
    response = await db_client.post(url, json=body, headers={sessions.CSRF_HEADER: forged})
    assert response.status_code == 403

    db_client.cookies.set(sessions.CSRF_COOKIE, good)
    response = await db_client.post(url, json=body, headers={sessions.CSRF_HEADER: good})
    assert response.status_code == 204


async def test_safe_methods_need_no_csrf(db_client: AsyncClient, make_user: UserFactory) -> None:
    await make_user()
    await login(db_client)

    assert (await db_client.get(f"{PREFIX}/auth/me")).status_code == 200
    assert (await db_client.get(f"{PREFIX}/users")).status_code == 200


# --- expiry ----------------------------------------------------------------------


async def test_idle_timeout(
    db_client: AsyncClient, make_user: UserFactory, fake_clock: FakeClock, infra: Infra
) -> None:
    await make_user()
    await login(db_client)

    fake_clock.advance(hours=23, minutes=59)
    assert (await db_client.get(f"{PREFIX}/auth/me")).status_code == 200

    fake_clock.advance(hours=24)
    assert (await db_client.get(f"{PREFIX}/auth/me")).status_code == 401
    assert await session_rows(infra) == []  # expired sessions are deleted


async def test_activity_slides_the_idle_timeout(
    db_client: AsyncClient, make_user: UserFactory, fake_clock: FakeClock
) -> None:
    await make_user()
    await login(db_client)

    for _ in range(3):  # 69 hours in total, never 24 idle
        fake_clock.advance(hours=23)
        assert (await db_client.get(f"{PREFIX}/auth/me")).status_code == 200


async def test_absolute_lifetime(
    db_client: AsyncClient, make_user: UserFactory, fake_clock: FakeClock
) -> None:
    await make_user()
    await login(db_client)

    for _ in range(8):  # 160 hours: active every 20 hours
        fake_clock.advance(hours=20)
        assert (await db_client.get(f"{PREFIX}/auth/me")).status_code == 200

    fake_clock.advance(hours=8)  # 168 hours = 7 days after sign-in
    assert (await db_client.get(f"{PREFIX}/auth/me")).status_code == 401


async def test_last_seen_is_written_at_most_once_a_minute(
    db_client: AsyncClient, make_user: UserFactory, fake_clock: FakeClock, infra: Infra
) -> None:
    await make_user()
    await login(db_client)
    signed_in = fake_clock.now

    fake_clock.advance(seconds=30)
    await db_client.get(f"{PREFIX}/auth/me")
    [row] = await session_rows(infra)
    assert row.last_seen_at == signed_in

    fake_clock.advance(seconds=31)
    await db_client.get(f"{PREFIX}/auth/me")
    [row] = await session_rows(infra)
    assert row.last_seen_at == fake_clock.now


async def test_login_cleans_up_expired_sessions(
    new_client: ClientFactory, make_user: UserFactory, fake_clock: FakeClock, infra: Infra
) -> None:
    await make_user()
    await login(new_client())
    fake_clock.advance(days=2)

    await login(new_client())

    rows = await session_rows(infra)
    assert len(rows) == 1
    assert rows[0].created_at == fake_clock.now


async def test_deactivated_user_session_stops_working(
    db_client: AsyncClient, make_user: UserFactory, infra: Infra
) -> None:
    user = await make_user(email="analyst@example.org", role=Role.ANALYST)
    await login(db_client, email=user.email)
    async with infra.sessionmaker() as db:
        stored = await db.get(User, user.id)
        assert stored is not None
        stored.is_active = False
        await db.commit()

    assert (await db_client.get(f"{PREFIX}/auth/me")).status_code == 401


# --- password change -------------------------------------------------------------


async def test_password_change_revokes_other_sessions(
    new_client: ClientFactory, make_user: UserFactory, infra: Infra
) -> None:
    await make_user()
    laptop, phone = new_client(), new_client()
    await login(laptop)
    await login(phone)
    new_password = "an entirely new passphrase"

    response = await laptop.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": new_password},
        headers=csrf(laptop),
    )

    assert response.status_code == 204
    assert (await laptop.get(f"{PREFIX}/auth/me")).status_code == 200
    assert (await phone.get(f"{PREFIX}/auth/me")).status_code == 401
    assert len(await session_rows(infra)) == 1

    other = new_client()
    assert (await login(other, password=PASSWORD)).status_code == 401
    assert (await login(other, password=new_password)).status_code == 200


async def test_password_change_needs_the_current_password(
    db_client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user()
    await login(db_client)

    response = await db_client.post(
        f"{PREFIX}/auth/password",
        json={"current_password": "not my password", "new_password": "a brand new password"},
        headers=csrf(db_client),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Current password is incorrect."}


@pytest.mark.parametrize(
    ("new_password", "message"),
    [
        ("too short", "at least 12 characters"),
        ("my admin password", "must not contain your email"),
        (PASSWORD, "must differ"),
    ],
)
async def test_password_change_enforces_the_policy(
    db_client: AsyncClient, make_user: UserFactory, new_password: str, message: str
) -> None:
    await make_user()
    await login(db_client)

    response = await db_client.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": new_password},
        headers=csrf(db_client),
    )

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert new_password not in response.text


async def test_wrong_current_passwords_are_rate_limited(
    db_client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user()
    await login(db_client)
    body = {"current_password": "guessing wrong", "new_password": "a brand new password"}

    for _ in range(ratelimit.MAX_FAILURES_PER_EMAIL):
        response = await db_client.post(
            f"{PREFIX}/auth/password", json=body, headers=csrf(db_client)
        )
        assert response.status_code == 400

    response = await db_client.post(f"{PREFIX}/auth/password", json=body, headers=csrf(db_client))
    assert response.status_code == 429
    assert "retry-after" in response.headers


async def test_sessions_are_deleted_with_their_user(
    db_client: AsyncClient, make_user: UserFactory, infra: Infra
) -> None:
    # A session created directly: signing in would also write an audit event,
    # and users with audit history cannot be deleted (see test_audit.py).
    user = await make_user()
    async with infra.sessionmaker() as db:
        await sessions.create_session(db, user, ip=None, user_agent=None)
        await db.commit()
    async with infra.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Session)) == 1
        stored = await db.get(User, user.id)
        await db.delete(stored)
        await db.commit()

    async with infra.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Session)) == 0


# --- the event loop stays responsive during sign-ins ------------------------------

HEARTBEAT_INTERVAL = 0.005
MAX_HEARTBEAT_GAP = 0.060


async def test_failed_logins_do_not_block_the_event_loop(
    new_client: ClientFactory, make_user: UserFactory
) -> None:
    """Argon2 runs off the event loop: other work keeps its 5 ms rhythm during 8 sign-ins."""
    await make_user()
    beats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        # Beat once more after `stop` is set: if the loop was blocked until the
        # end, that last beat is what measures the stall.
        while True:
            beats.append(time.perf_counter())
            if stop.is_set():
                return
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    # 4 wrong passwords for a real account (verify) and 4 unknown emails (dummy
    # verify): both hashing paths, and below the per-email limit of 5.
    attempts = [(ADMIN_EMAIL, f"wrong password {n}") for n in range(4)]
    attempts += [(f"nobody{n}@example.org", "some password") for n in range(4)]

    beat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(HEARTBEAT_INTERVAL * 2)
    try:
        responses = await asyncio.gather(
            *(login(new_client(), email=email, password=pw) for email, pw in attempts)
        )
    finally:
        stop.set()
        await beat_task

    assert [response.status_code for response in responses] == [401] * len(attempts)
    max_gap = max(later - earlier for earlier, later in itertools.pairwise(beats))
    print(f"heartbeats={len(beats)} max_gap_ms={max_gap * 1000:.1f}")
    assert max_gap < MAX_HEARTBEAT_GAP, f"event loop blocked for {max_gap * 1000:.0f} ms"
