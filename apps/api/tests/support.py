"""Helpers shared by the test modules (importable as ``support``).

The database tests (marker ``db``) connect to ``NIGHTLOOM_TEST_DATABASE_URL``
and ``NIGHTLOOM_TEST_VALKEY_URL``. ``make test`` derives both from ``.env``: a
separate ``nightloom_test`` database (created on first use) and Valkey
database 15, so development data is never touched.

Neo4j Community Edition has a single database, so the tests share it with the
local stack (``NIGHTLOOM_TEST_NEO4J_URI``, ``_USER``, ``_PASSWORD``). They stay
out of real data because every graph is scoped to a case ID and the tests
only ever create and delete graphs of random case IDs of their own.
"""

import asyncio
import os
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic.config import Config
from httpx import AsyncClient, Response
from neo4j import AsyncDriver
from pydantic import SecretStr

from nightloom_api.auth import sessions
from nightloom_api.config import Settings
from nightloom_api.graph.repository import GraphRepository

TEST_DATABASE_URL = os.environ.get(
    "NIGHTLOOM_TEST_DATABASE_URL",
    "postgresql+asyncpg://nightloom:change-me-postgres-password@127.0.0.1:5432/nightloom_test",
)
TEST_VALKEY_URL = os.environ.get(
    "NIGHTLOOM_TEST_VALKEY_URL", "redis://:change-me-valkey-password@127.0.0.1:6379/15"
)
TEST_NEO4J_URI = os.environ.get("NIGHTLOOM_TEST_NEO4J_URI", "bolt://127.0.0.1:7687")
TEST_NEO4J_USER = os.environ.get("NIGHTLOOM_TEST_NEO4J_USER", "neo4j")
TEST_NEO4J_PASSWORD = os.environ.get("NIGHTLOOM_TEST_NEO4J_PASSWORD", "change-me-neo4j-password")
API_DIR = Path(__file__).resolve().parents[1]

PASSWORD = "correct horse battery staple"
ADMIN_EMAIL = "admin@example.org"
PREFIX = "/api/v1"
# Created by migration 0003_audit_events.
APPEND_ONLY_TRIGGER = "audit_events_append_only"


def graph_test_settings() -> Settings:
    """Settings whose Neo4j connection is the test one."""
    return Settings(
        env="test",
        neo4j_uri=TEST_NEO4J_URI,
        neo4j_user=TEST_NEO4J_USER,
        neo4j_password=SecretStr(TEST_NEO4J_PASSWORD),
    )


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


class TrackingGraphRepository(GraphRepository):
    """Hands out fresh case IDs and remembers them, so their graphs can be cleaned up."""

    def __init__(self, driver: AsyncDriver) -> None:
        super().__init__(driver)
        self.used: list[uuid.UUID] = []

    def new_case(self) -> uuid.UUID:
        case_id = uuid.uuid4()
        self.used.append(case_id)
        return case_id
