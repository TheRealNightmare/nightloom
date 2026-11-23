"""Fixtures for the database-backed tests (markers ``db`` and ``graph``).

They need PostgreSQL, Valkey and Neo4j; see support.py for the URLs. The API's
conftest and the worker's import them from here, so both test suites run
against the same, freshly reset databases.
"""

import re
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import pytest
from alembic import command
from botocore.exceptions import ClientError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from nightloom_api.audit.service import Actor
from nightloom_api.auth import clock, service
from nightloom_api.auth.models import Role, User
from nightloom_api.config import Settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.graph.schema import apply_schema
from nightloom_api.infra import Infra, graph
from nightloom_api.infra.errors import describe_error
from nightloom_api.infra.storage import get_storage
from nightloom_api.jobs.queue import get_job_queue
from nightloom_api.main import create_app
from support import (
    ADMIN_EMAIL,
    APPEND_ONLY_TRIGGER,
    PASSWORD,
    TEST_DATABASE_URL,
    TEST_NEO4J_PASSWORD,
    TEST_VALKEY_URL,
    FakeClock,
    TrackingGraphRepository,
    alembic_config,
    call_in_thread,
    graph_test_settings,
    run_in_thread,
)


class RecordingJobQueue:
    """Stands in for Celery: records what would be sent (or fails, when told to)."""

    def __init__(self) -> None:
        self.sent: list[tuple[uuid.UUID, int]] = []
        self.checks: list[uuid.UUID] = []
        self.fail = False

    async def enqueue(self, job_id: uuid.UUID, *, timeout_seconds: int) -> None:
        if self.fail:
            raise ConnectionError("broker unavailable")
        self.sent.append((job_id, timeout_seconds))

    async def enqueue_check(self, verification_id: uuid.UUID) -> None:
        if self.fail:
            raise ConnectionError("broker unavailable")
        self.checks.append(verification_id)

    def close(self) -> None:
        pass


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aenter__(self) -> "_Body":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def read(self) -> bytes:
        return self.data


class MemoryS3:
    """The S3 calls Nightloom makes (evidence), on a dict; ``fail`` makes reads fail."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.fail = False

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.objects[Key] = (Body, ContentType)

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if self.fail:
            raise ClientError({"Error": {"Code": "InternalError"}}, "GetObject")
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _Body(self.objects[Key][0])}

    async def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        keys = sorted(key for key in self.objects if key.startswith(kwargs["Prefix"]))
        page = keys[: kwargs["MaxKeys"]]
        return {"Contents": [{"Key": key} for key in page], "IsTruncated": len(keys) > len(page)}

    async def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)
        return {}


class MemoryStorage:
    """Stands in for the object storage in the API tests (no S3 server is needed)."""

    bucket = "nightloom-evidence"

    def __init__(self) -> None:
        self.s3 = MemoryS3()

    async def client(self) -> MemoryS3:
        return self.s3


# --- Database-backed tests (marker `db`) --------------------------------------
# These fixtures need PostgreSQL, Valkey and Neo4j; see support.py for the URLs.


async def _ensure_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    name = url.database or ""
    # Safety net: the tests TRUNCATE tables, so never point them at a real database.
    if not re.fullmatch(r"[a-z0-9_]*test[a-z0-9_]*", name):
        raise RuntimeError(f"refusing to use database {name!r}: its name must contain 'test'")
    # CREATE DATABASE cannot run inside a transaction.
    engine = create_async_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"timeout": 5},
    )
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


async def _check_test_valkey() -> None:
    client = Redis.from_url(TEST_VALKEY_URL, socket_connect_timeout=5)
    try:
        # Safety net: the tests FLUSHDB, so never use the default database 0.
        if int(client.connection_pool.connection_kwargs.get("db", 0)) == 0:
            raise RuntimeError("refusing to use Valkey database 0")
        await client.ping()
    finally:
        await client.aclose()


async def _prepare_neo4j() -> None:
    """Check that Neo4j answers and apply the graph schema (idempotent)."""
    driver = graph.create_driver(graph_test_settings())
    try:
        await graph.ping(driver)
        await apply_schema(driver)
    finally:
        await driver.close()


DB_HINT = (
    "Start PostgreSQL, Valkey and Neo4j with `make deps-up` (or `make up`), then run `make test`."
)


def _fail_unusable(name: str, exc: Exception) -> None:
    secrets = [TEST_DATABASE_URL, TEST_VALKEY_URL, TEST_NEO4J_PASSWORD]
    pytest.fail(f"{name} is not usable: {describe_error(exc, secrets)}. {DB_HINT}", False)


@pytest.fixture(scope="session")
def neo4j_ready() -> None:
    """Neo4j answers and has the graph schema, checked once per session."""
    try:
        run_in_thread(_prepare_neo4j)
    except Exception as exc:
        _fail_unusable("Neo4j", exc)


@pytest.fixture(scope="session")
def migrated_database(neo4j_ready: None) -> str:
    """Create the test database if needed and migrate it to head, once per session.

    Also needs Neo4j: deleting a case deletes its graph.
    """
    for name, check in (("PostgreSQL", _ensure_test_database), ("Valkey", _check_test_valkey)):
        try:
            run_in_thread(check)
        except Exception as exc:
            _fail_unusable(name, exc)
    call_in_thread(lambda: command.upgrade(alembic_config(), "head"))
    return TEST_DATABASE_URL


@pytest.fixture
def db_settings(migrated_database: str) -> Settings:
    return graph_test_settings().model_copy(
        update={
            "database_url": SecretStr(migrated_database),
            "valkey_url": SecretStr(TEST_VALKEY_URL),
        }
    )


@pytest.fixture
async def graph_repo(neo4j_ready: None) -> AsyncIterator[TrackingGraphRepository]:
    """A repository on the test Neo4j. Graphs of case IDs from ``new_case()`` are deleted after."""
    driver = graph.create_driver(graph_test_settings())
    repo = TrackingGraphRepository(driver)
    try:
        yield repo
    finally:
        for case_id in repo.used:
            await repo.delete_case_graph(case_id)
        await driver.close()


async def _reset_state(infra: Infra) -> None:
    # Graphs of the test database's cases (random IDs, never real ones).
    async with infra.sessionmaker() as db:
        case_ids = list((await db.execute(text("SELECT id FROM cases"))).scalars())
    repo = GraphRepository(infra.graph)
    for case_id in case_ids:
        await repo.delete_case_graph(case_id)
    async with infra.engine.begin() as conn:
        # audit_events is append-only; as its owner the test role may switch
        # the trigger off for the duration of this transaction.
        await conn.execute(text(f"ALTER TABLE audit_events DISABLE TRIGGER {APPEND_ONLY_TRIGGER}"))
        await conn.execute(
            text(
                "TRUNCATE audit_events, jobs, result_cache, ownership_verifications, scan_runs, "
                "scan_profiles, case_members, cases, users, sessions RESTART IDENTITY CASCADE"
            )
        )
        await conn.execute(text(f"ALTER TABLE audit_events ENABLE TRIGGER {APPEND_ONLY_TRIGGER}"))
    await infra.cache.flushdb()


@pytest.fixture
async def db_app(db_settings: Settings) -> AsyncIterator[FastAPI]:
    """A running app (lifespan entered) on the test database, with empty tables."""
    app = create_app(db_settings)
    # Jobs are never sent to a real broker by the API tests.
    queue = RecordingJobQueue()
    app.state.test_job_queue = queue
    app.dependency_overrides[get_job_queue] = lambda: queue
    # Nor do they need an S3 server: evidence goes to memory.
    storage = MemoryStorage()
    app.state.test_storage = storage
    app.dependency_overrides[get_storage] = lambda: storage
    async with app.router.lifespan_context(app):
        await _reset_state(app.state.infra)
        yield app
        await _reset_state(app.state.infra)


@pytest.fixture
def job_queue(db_app: FastAPI) -> RecordingJobQueue:
    queue: RecordingJobQueue = db_app.state.test_job_queue
    return queue


@pytest.fixture
def infra(db_app: FastAPI) -> Infra:
    result: Infra = db_app.state.infra
    return result


ClientFactory = Callable[[], AsyncClient]


@pytest.fixture
async def new_client(db_app: FastAPI) -> AsyncIterator[ClientFactory]:
    """Factory for clients with separate cookie jars (separate browsers)."""
    clients: list[AsyncClient] = []

    def factory() -> AsyncClient:
        client = AsyncClient(transport=ASGITransport(app=db_app), base_url="http://testserver")
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


@pytest.fixture
def db_client(new_client: ClientFactory) -> AsyncClient:
    return new_client()


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(clock, "utcnow", fake)
    return fake


UserFactory = Callable[..., Coroutine[Any, Any, User]]


@pytest.fixture
def make_user(infra: Infra) -> UserFactory:
    """Create a user directly through the service layer."""

    async def factory(
        email: str = ADMIN_EMAIL,
        role: Role = Role.ADMIN,
        *,
        password: str = PASSWORD,
        display_name: str = "Test User",
        is_active: bool = True,
    ) -> User:
        async with infra.sessionmaker() as db:
            user = await service.create_user(
                db,
                email=email,
                display_name=display_name,
                role=role,
                password=password,
                actor=Actor.system("test"),
            )
            if not is_active:
                user.is_active = False
                await db.commit()
        return user

    return factory
