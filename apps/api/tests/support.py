"""Helpers shared by the test modules (importable as ``support``).

The database tests (marker ``db``) connect to ``NIGHTLOOM_TEST_DATABASE_URL``
and ``NIGHTLOOM_TEST_VALKEY_URL``. ``make test`` derives both from ``.env``: a
separate ``nightloom_test`` database (created on first use) and Valkey
database 15, so development data is never touched.
"""

import asyncio
import os
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic.config import Config
from httpx import AsyncClient, Response

from nightloom_api.auth import sessions

TEST_DATABASE_URL = os.environ.get(
    "NIGHTLOOM_TEST_DATABASE_URL",
    "postgresql+asyncpg://nightloom:change-me-postgres-password@127.0.0.1:5432/nightloom_test",
)
TEST_VALKEY_URL = os.environ.get(
    "NIGHTLOOM_TEST_VALKEY_URL", "redis://:change-me-valkey-password@127.0.0.1:6379/15"
)
API_DIR = Path(__file__).resolve().parents[1]

PASSWORD = "correct horse battery staple"
ADMIN_EMAIL = "admin@example.org"
PREFIX = "/api/v1"


def run_in_thread[T](func: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run a coroutine function on a fresh event loop in a separate thread.

    Alembic's env.py calls ``asyncio.run`` itself, so it can never run on the
    test's own event loop.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(func())).result()


def call_in_thread[T](func: Callable[[], T]) -> T:
    """Run a blocking function in a separate thread and return its result."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(func).result()


def alembic_config() -> Config:
    """Alembic configuration aimed at the test database."""
    config = Config(str(API_DIR / "alembic.ini"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    return config


class FakeClock:
    """A controllable replacement for :func:`nightloom_api.auth.clock.utcnow`."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


async def login(
    client: AsyncClient, email: str = ADMIN_EMAIL, password: str = PASSWORD
) -> Response:
    return await client.post(f"{PREFIX}/auth/login", json={"email": email, "password": password})


def csrf(client: AsyncClient) -> dict[str, str]:
    """The CSRF header for a signed-in client."""
    token = client.cookies.get(sessions.CSRF_COOKIE)
    assert token, "client has no CSRF cookie; log in first"
    return {sessions.CSRF_HEADER: token}


def set_cookie_headers(response: Response) -> dict[str, str]:
    """``Set-Cookie`` headers by cookie name."""
    return {header.split("=", 1)[0]: header for header in response.headers.get_list("set-cookie")}
