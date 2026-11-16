import asyncio
from collections.abc import Callable, Mapping

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nightloom_api.config import Settings
from nightloom_api.infra import Check, DependencyName
from nightloom_api.main import create_app
from nightloom_api.routers.readiness import get_readiness_checks

NAMES: tuple[DependencyName, ...] = ("postgres", "neo4j", "valkey", "storage")


async def ok() -> None:
    return None


def failing(exc: Exception) -> Check:
    async def check() -> None:
        raise exc

    return check


async def hangs() -> None:
    await asyncio.sleep(60)


def use_checks(app: FastAPI, checks: Mapping[DependencyName, Check]) -> None:
    app.dependency_overrides[get_readiness_checks] = lambda: checks


async def test_ready_returns_200_when_every_check_passes(app: FastAPI, client: AsyncClient) -> None:
    use_checks(app, dict.fromkeys(NAMES, ok))

    response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["checks"]) == set(NAMES)
    for result in body["checks"].values():
        assert result["ok"] is True
        assert result["error"] is None
        assert result["latency_ms"] >= 0


async def test_ready_returns_503_when_one_check_fails(app: FastAPI, client: AsyncClient) -> None:
    checks: dict[DependencyName, Check] = dict.fromkeys(NAMES, ok)
    checks["neo4j"] = failing(ConnectionRefusedError("Connect call failed"))
    use_checks(app, checks)

    response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["neo4j"] == {
        "ok": False,
        "latency_ms": body["checks"]["neo4j"]["latency_ms"],
        "error": "ConnectionRefusedError: Connect call failed",
    }
    assert all(body["checks"][name]["ok"] for name in ("postgres", "valkey", "storage"))


async def test_checks_run_concurrently_and_time_out() -> None:
    app = create_app(Settings(env="test", dependency_timeout_seconds=0.2))
    use_checks(app, dict.fromkeys(NAMES, hangs))

    loop = asyncio.get_running_loop()
    started = loop.time()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        response = await ac.get("/api/v1/ready")
    elapsed = loop.time() - started

    assert response.status_code == 503
    for result in response.json()["checks"].values():
        assert result == {
            "ok": False,
            "latency_ms": result["latency_ms"],
            "error": "timed out after 0.2s",
        }
        assert 150 <= result["latency_ms"] < 1000
    # Four 0.2 s checks run in parallel, not one after another.
    assert elapsed < 0.6


async def test_error_never_contains_credentials_or_urls(
    settings_for_port: Callable[..., Settings], fake_secrets: tuple[str, ...]
) -> None:
    settings = settings_for_port(5432)
    app = create_app(settings)
    leaky = [
        f"cannot connect to {settings.database_url.get_secret_value()}",
        f"auth failed for neo4j/{settings.neo4j_password.get_secret_value()}",
        f"bad password {settings.valkey_url.get_secret_value().split('@')[0]}",
        f"signature mismatch for {settings.s3_access_key.get_secret_value()} "
        f"{settings.s3_secret_key.get_secret_value()}",
    ]
    use_checks(
        app,
        {name: failing(RuntimeError(text)) for name, text in zip(NAMES, leaky, strict=True)},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        response = await ac.get("/api/v1/ready")

    assert response.status_code == 503
    for secret in fake_secrets:
        assert secret not in response.text
    assert "://" not in response.text
    assert response.json()["checks"]["postgres"]["error"] == "RuntimeError: cannot connect to ***"


async def test_failures_are_logged_without_secrets(
    app: FastAPI,
    client: AsyncClient,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    checks: dict[DependencyName, Check] = dict.fromkeys(NAMES, ok)
    checks["valkey"] = failing(OSError(f"refused {settings.valkey_url.get_secret_value()}"))
    use_checks(app, checks)

    await client.get("/api/v1/ready")

    assert "Readiness check valkey failed: OSError: refused ***" in caplog.text
    assert settings.valkey_url.get_secret_value() not in caplog.text


async def test_real_clients_report_unreachable_dependencies(
    settings_for_port: Callable[..., Settings],
    closed_port: int,
    fake_secrets: tuple[str, ...],
) -> None:
    # Real drivers against a port with nothing listening: every ping fails fast
    # (or times out), the response is 503 and still free of credentials.
    app = create_app(settings_for_port(closed_port, dependency_timeout_seconds=0.5))

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac,
    ):
        response = await ac.get("/api/v1/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert set(body["checks"]) == set(NAMES)
    for result in body["checks"].values():
        assert result["ok"] is False
        assert result["error"]
        assert len(result["error"]) <= 120
    for secret in fake_secrets:
        assert secret not in response.text
    assert "://" not in response.text


async def test_openapi_documents_ready_and_503(client: AsyncClient) -> None:
    schema = (await client.get("/api/openapi.json")).json()

    responses = schema["paths"]["/api/v1/ready"]["get"]["responses"]
    assert {"200", "503"} <= set(responses)
