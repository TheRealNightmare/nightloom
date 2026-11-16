from importlib.metadata import version

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nightloom_api.config import Settings
from nightloom_api.main import create_app


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": version("nightloom-api"),
        "env": "test",
    }


async def test_health_version_matches_package_metadata(client: AsyncClient, app: FastAPI) -> None:
    response = await client.get("/api/v1/health")

    assert response.json()["version"] == version("nightloom-api")
    assert app.version == version("nightloom-api")


async def test_health_respects_custom_api_prefix() -> None:
    app = create_app(Settings(env="test", api_prefix="/custom"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        assert (await ac.get("/custom/health")).status_code == 200
        assert (await ac.get("/api/v1/health")).status_code == 404


async def test_unknown_route_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404


async def test_openapi_schema_lists_health(client: AsyncClient) -> None:
    response = await client.get("/api/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Nightloom API"
    assert "/api/v1/health" in schema["paths"]


async def test_docs_are_served(client: AsyncClient) -> None:
    response = await client.get("/api/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
