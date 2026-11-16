import asyncio
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from nightloom_api.config import Settings, get_settings
from nightloom_api.main import create_app

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
