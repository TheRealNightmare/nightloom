"""Integration tests against the running Compose stack.

Run with ``make up && make test-integration``. The Makefile points the
``NIGHTLOOM_*`` connection variables at the ports published on 127.0.0.1 and
reads the credentials from ``.env``.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Connection

from nightloom_api.config import Settings
from nightloom_api.infra import Infra, create_infra
from nightloom_api.main import create_app

pytestmark = pytest.mark.integration

API_DIR = Path(__file__).resolve().parents[2]
EXPECTED_CHECKS = {"postgres", "neo4j", "valkey", "storage"}


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test")


@pytest.fixture
async def infra(settings: Settings) -> AsyncIterator[Infra]:
    infra = create_infra(settings)
    try:
        yield infra
    finally:
        await infra.aclose()


async def wait_until_ready(client: AsyncClient, attempts: int = 15) -> httpx.Response:
    """Poll /ready like an orchestrator would.

    A freshly started Neo4j can take longer than the 2 s check limit to answer
    its first query, so one early 503 is expected right after `make up`.
    """
    response = await client.get("/api/v1/ready")
    for _ in range(attempts - 1):
        if response.status_code == 200:
            break
        await asyncio.sleep(2)
        response = await client.get("/api/v1/ready")
    return response


def assert_all_ready(response: httpx.Response) -> None:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["checks"]) == EXPECTED_CHECKS
    for result in body["checks"].values():
        assert result["ok"] is True
        assert result["error"] is None


async def test_running_api_container_is_ready() -> None:
    base_url = os.environ.get("NIGHTLOOM_TEST_API_URL", "http://127.0.0.1:8000")

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        response = await wait_until_ready(client)

    assert_all_ready(response)


async def test_in_process_app_is_ready(settings: Settings) -> None:
    app = create_app(settings)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        response = await wait_until_ready(client)

    assert_all_ready(response)


async def test_postgres_session_runs_select_1(infra: Infra) -> None:
    async with infra.sessionmaker() as session:
        result = await session.execute(text("SELECT 1"))

    assert result.scalar_one() == 1


async def test_neo4j_returns_1(infra: Infra) -> None:
    async with infra.graph.session() as session:
        result = await session.run("RETURN 1 AS one")
        record = await result.single(strict=True)

    assert record["one"] == 1


async def test_valkey_set_get(infra: Infra) -> None:
    key = f"nightloom:test:{uuid.uuid4()}"

    await infra.cache.set(key, b"woven", ex=60)
    try:
        assert await infra.cache.get(key) == b"woven"
    finally:
        await infra.cache.delete(key)

    assert await infra.cache.get(key) is None


async def test_storage_put_get_delete(infra: Infra) -> None:
    client = await infra.storage.client()
    bucket = infra.storage.bucket
    key = f"tests/{uuid.uuid4()}.txt"

    await client.put_object(Bucket=bucket, Key=key, Body=b"evidence")
    try:
        response = await client.get_object(Bucket=bucket, Key=key)
        async with response["Body"] as body:
            assert await body.read() == b"evidence"
    finally:
        await client.delete_object(Bucket=bucket, Key=key)

    listed = await client.list_objects_v2(Bucket=bucket, Prefix=key)
    assert listed.get("KeyCount", 0) == 0


async def test_alembic_is_at_head(infra: Infra) -> None:
    script = ScriptDirectory.from_config(Config(str(API_DIR / "alembic.ini")))

    def current_heads(connection: Connection) -> tuple[str, ...]:
        return MigrationContext.configure(connection).get_current_heads()

    async with infra.engine.connect() as connection:
        current = await connection.run_sync(current_heads)

    assert set(current) == set(script.get_heads())
    assert "0001_baseline" in {rev.revision for rev in script.walk_revisions()}
