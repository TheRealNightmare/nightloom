import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.config import Settings
from nightloom_api.infra import Infra, create_infra
from nightloom_api.infra.cache import get_cache
from nightloom_api.infra.db import NAMING_CONVENTION, Base, get_session
from nightloom_api.infra.graph import get_graph
from nightloom_api.infra.storage import Storage, get_storage
from nightloom_api.main import create_app


async def test_lifespan_starts_and_stops_without_connecting(
    settings_for_port: Callable[..., Settings],
    counting_server: tuple[int, Callable[[], int]],
) -> None:
    port, connections = counting_server
    app = create_app(settings_for_port(port, dependency_timeout_seconds=0.5))

    async with app.router.lifespan_context(app):
        infra = app.state.infra
        assert isinstance(infra, Infra)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            assert (await ac.get("/api/v1/health")).status_code == 200
        # Creating the S3 client must not connect either.
        await infra.storage.client()
        assert connections() == 0

        # Sanity check: the listener does see a real attempt.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            await ac.get("/api/v1/ready")
        assert connections() > 0


async def test_lifespan_closes_every_client(
    monkeypatch: pytest.MonkeyPatch, settings_for_port: Callable[..., Settings], closed_port: int
) -> None:
    closed: list[str] = []
    real_create = create_infra

    def tracking_create(settings: Settings) -> Infra:
        infra = real_create(settings)

        async def close() -> None:
            closed.append("closed")
            await Infra.aclose(infra)

        monkeypatch.setattr(infra, "aclose", close)
        return infra

    monkeypatch.setattr("nightloom_api.main.create_infra", tracking_create)
    app = create_app(settings_for_port(closed_port))

    async with app.router.lifespan_context(app):
        assert closed == []
    assert closed == ["closed"]


class FakeClosable:
    def __init__(self, name: str, log: list[str], *, fail: bool = False) -> None:
        self.name, self.log, self.fail = name, log, fail

    async def close(self) -> None:
        self.log.append(self.name)
        if self.fail:
            raise RuntimeError("secret detail that must not be logged")


async def test_aclose_closes_all_even_if_one_fails(caplog: pytest.LogCaptureFixture) -> None:
    log: list[str] = []
    engine = FakeClosable("postgres", log, fail=True)
    graph = FakeClosable("neo4j", log)
    cache = FakeClosable("valkey", log)
    storage = FakeClosable("storage", log)
    fake: Any = Infra.__new__(Infra)
    fake.engine = type("E", (), {"dispose": engine.close})()
    fake.graph = graph
    fake.cache = type("C", (), {"aclose": cache.close})()
    fake.storage = type("S", (), {"aclose": storage.close})()

    with caplog.at_level(logging.WARNING):
        await Infra.aclose(fake)

    assert log == ["postgres", "neo4j", "valkey", "storage"]
    assert "Closing the postgres client failed: RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text


async def test_storage_reuses_one_client_and_can_close_unused(settings: Settings) -> None:
    unused = Storage(settings)
    await unused.aclose()

    storage = Storage(settings)
    first = await storage.client()
    assert await storage.client() is first
    await storage.aclose()


async def test_dependencies_return_the_shared_clients(settings: Settings) -> None:
    app = create_app(settings)
    seen: dict[str, object] = {}

    @app.get("/probe")
    async def probe(
        session: AsyncSession = Depends(get_session),  # noqa: B008
        graph: AsyncDriver = Depends(get_graph),  # noqa: B008
        cache: Redis = Depends(get_cache),  # noqa: B008
        storage: Storage = Depends(get_storage),  # noqa: B008
    ) -> None:
        seen.update(session=session, graph=graph, cache=cache, storage=storage)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac,
    ):
        assert (await ac.get("/probe")).status_code == 200
        infra: Infra = app.state.infra
        assert isinstance(seen["session"], AsyncSession)
        assert seen["graph"] is infra.graph
        assert seen["cache"] is infra.cache
        assert seen["storage"] is infra.storage


def test_metadata_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention == {**NAMING_CONVENTION}
    for key in ("ix", "uq", "ck", "fk", "pk"):
        assert key in NAMING_CONVENTION
