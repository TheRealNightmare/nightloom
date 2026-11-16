import asyncio
import re
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from typing import Any

import pytest
from alembic import command
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
from nightloom_api.config import Settings, get_settings
from nightloom_api.infra import Infra
from nightloom_api.infra.errors import describe_error
from nightloom_api.main import create_app
from support import (
    ADMIN_EMAIL,
    APPEND_ONLY_TRIGGER,
    PASSWORD,
    TEST_DATABASE_URL,
    TEST_VALKEY_URL,
    FakeClock,
    alembic_config,
    call_in_thread,
    run_in_thread,
)

# Recognisable fake credentials: tests assert they never leak into responses.
SECRET_PG = "pg-S3cret-value"
SECRET_NEO4J = "neo4j-S3cret-value"
SECRET_VALKEY = "valkey-S3cret-value"
SECRET_S3_ACCESS = "s3-access-S3cret"
SECRET_S3_KEY = "s3-key-S3cret-value"
ALL_SECRETS = (SECRET_PG, SECRET_NEO4J, SECRET_VALKEY, SECRET_S3_ACCESS, SECRET_S3_KEY)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


SettingsForPort = Callable[..., Settings]


@pytest.fixture
def fake_secrets() -> tuple[str, ...]:
    return ALL_SECRETS


@pytest.fixture
def settings_for_port() -> SettingsForPort:
    return _settings_for_port


def _settings_for_port(port: int, **overrides: object) -> Settings:
    """Settings whose four dependencies all point at 127.0.0.1:<port>, with fake secrets."""
    values: dict[str, object] = {
        "env": "test",
        "database_url": SecretStr(
            f"postgresql+asyncpg://nightloom:{SECRET_PG}@127.0.0.1:{port}/nightloom"
        ),
        "neo4j_uri": f"bolt://127.0.0.1:{port}",
        "neo4j_password": SecretStr(SECRET_NEO4J),
        "valkey_url": SecretStr(f"redis://:{SECRET_VALKEY}@127.0.0.1:{port}/0"),
        "s3_endpoint": f"http://127.0.0.1:{port}",
        "s3_access_key": SecretStr(SECRET_S3_ACCESS),
        "s3_secret_key": SecretStr(SECRET_S3_KEY),
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture
async def counting_server() -> AsyncIterator[tuple[int, Callable[[], int]]]:
    """A TCP listener that drops every connection; yields ``(port, connection_count)``."""
    accepted = 0

    async def on_connect(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted
        accepted += 1
        writer.close()

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    async with server:
        yield port, lambda: accepted


@pytest.fixture
async def closed_port() -> int:
    """A local port with nothing listening (bound, then released)."""
    server = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port


# --- Database-backed tests (marker `db`) --------------------------------------
# These fixtures need PostgreSQL and Valkey; see support.py for the URLs.


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


DB_HINT = "Start PostgreSQL and Valkey with `make deps-up` (or `make up`), then run `make test`."


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Create the test database if needed and migrate it to head, once per session."""
    secrets = [TEST_DATABASE_URL, TEST_VALKEY_URL]
    for name, check in (("PostgreSQL", _ensure_test_database), ("Valkey", _check_test_valkey)):
        try:
            run_in_thread(check)
        except Exception as exc:
            pytest.fail(f"{name} is not usable: {describe_error(exc, secrets)}. {DB_HINT}", False)
    call_in_thread(lambda: command.upgrade(alembic_config(), "head"))
    return TEST_DATABASE_URL


@pytest.fixture
def db_settings(migrated_database: str) -> Settings:
    return Settings(
        env="test",
        database_url=SecretStr(migrated_database),
        valkey_url=SecretStr(TEST_VALKEY_URL),
    )


async def _reset_state(infra: Infra) -> None:
    async with infra.engine.begin() as conn:
        # audit_events is append-only; as its owner the test role may switch
        # the trigger off for the duration of this transaction.
        await conn.execute(text(f"ALTER TABLE audit_events DISABLE TRIGGER {APPEND_ONLY_TRIGGER}"))
        await conn.execute(text("TRUNCATE audit_events, users, sessions RESTART IDENTITY CASCADE"))
        await conn.execute(text(f"ALTER TABLE audit_events ENABLE TRIGGER {APPEND_ONLY_TRIGGER}"))
    await infra.cache.flushdb()


@pytest.fixture
async def db_app(db_settings: Settings) -> AsyncIterator[FastAPI]:
    """A running app (lifespan entered) on the test database, with empty tables."""
    app = create_app(db_settings)
    async with app.router.lifespan_context(app):
        await _reset_state(app.state.infra)
        yield app
        await _reset_state(app.state.infra)


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
