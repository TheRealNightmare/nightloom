"""``GET /schema`` without services: its content mirrors nightloom_core exactly."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nightloom_api.auth.deps import current_user
from nightloom_api.auth.models import Role, User
from nightloom_api.routers.schema import CACHE_CONTROL, build_schema
from nightloom_core.entities import ENTITY_MODELS, EntityType, Sensitivity, build_entity
from nightloom_core.provenance import (
    CREDIBILITY_SCALE,
    MANUAL_CONFIDENCE,
    MANUAL_CREDIBILITY,
    MANUAL_RELIABILITY,
    NOTE_MAX_LENGTH,
    RELIABILITY_SCALE,
)
from nightloom_core.relationships import ALLOWED, NOTE_REQUIRED, RelationType, is_allowed

DOCUMENTATION_IPS = ("192.0.2.", "198.51.100.", "203.0.113.")


def test_entity_types_mirror_the_core_models() -> None:
    schema = build_schema()
    assert [item.type for item in schema.entity_types] == list(EntityType)
    for item in schema.entity_types:
        model = ENTITY_MODELS[item.type]
        assert item.label == model.label
        assert item.display_name == model.display_name
        assert item.sensitivity == model.sensitivity
        assert item.hint == model.hint
        assert item.example == model.example


@pytest.mark.parametrize("entity_type", list(EntityType))
def test_examples_are_valid_and_documentation_safe(entity_type: EntityType) -> None:
    example = next(item.example for item in build_schema().entity_types if item.type == entity_type)
    build_entity(entity_type, example)  # raises if the example is not a valid value
    if entity_type in (EntityType.IP_ADDRESS, EntityType.NETBLOCK, EntityType.SERVICE):
        assert example.startswith(DOCUMENTATION_IPS)  # RFC 5737
    if entity_type in (EntityType.DOMAIN, EntityType.URL, EntityType.EMAIL):
        assert "example.org" in example
    if entity_type == EntityType.ASN:
        assert 64496 <= int(example.removeprefix("AS")) <= 64511  # RFC 5398


def test_relationship_pairs_are_exactly_the_allow_list() -> None:
    schema = build_schema()
    assert [item.type for item in schema.relationship_types] == list(RelationType)
    for item in schema.relationship_types:
        assert set(item.pairs) == ALLOWED[item.type]
        assert len(item.pairs) == len(ALLOWED[item.type])
        assert item.note_required == (item.type in NOTE_REQUIRED)
        assert all(is_allowed(item.type, source, target) for source, target in item.pairs)
    related_to = next(i for i in schema.relationship_types if i.type == RelationType.RELATED_TO)
    assert len(related_to.pairs) == len(EntityType) ** 2


def test_pairs_are_in_entity_type_order() -> None:
    order = list(EntityType)
    for item in build_schema().relationship_types:
        keys = [(order.index(s), order.index(t)) for s, t in item.pairs]
        assert keys == sorted(keys)


def test_admiralty_scales_and_manual_defaults() -> None:
    schema = build_schema()
    assert [(g.code, g.label) for g in schema.admiralty.reliability] == list(
        RELIABILITY_SCALE.items()
    )
    assert [(g.code, g.label) for g in schema.admiralty.credibility] == [
        (str(code), text) for code, text in CREDIBILITY_SCALE.items()
    ]
    defaults = schema.manual_defaults
    assert defaults.source == "manual"
    assert (defaults.reliability, defaults.credibility) == (MANUAL_RELIABILITY, MANUAL_CREDIBILITY)
    assert defaults.admiralty == "B2"
    assert defaults.confidence == MANUAL_CONFIDENCE
    assert defaults.note_max_length == NOTE_MAX_LENGTH


def test_schema_is_built_once() -> None:
    assert build_schema() is build_schema()


@pytest.fixture
async def signed_in_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    user = User(email="viewer@example.org", display_name="Viewer", role=Role.VIEWER)
    app.dependency_overrides[current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac


async def test_get_schema(signed_in_client: AsyncClient) -> None:
    response = await signed_in_client.get("/api/v1/schema")
    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHE_CONTROL == "private, max-age=300"
    body = response.json()
    assert set(body) == {"entity_types", "relationship_types", "admiralty", "manual_defaults"}
    domain = body["entity_types"][0]
    assert domain == {
        "type": "domain",
        "label": "Domain",
        "display_name": "Domain",
        "sensitivity": "infrastructure",
        "hint": ENTITY_MODELS[EntityType.DOMAIN].hint,
        "example": "www.example.org",
    }
    email = next(item for item in body["entity_types"] if item["type"] == "email")
    assert email["sensitivity"] == Sensitivity.PERSON
    subdomain = next(i for i in body["relationship_types"] if i["type"] == "HAS_SUBDOMAIN")
    assert subdomain == {
        "type": "HAS_SUBDOMAIN",
        "note_required": False,
        "pairs": [["domain", "domain"]],
    }
    assert body["manual_defaults"]["admiralty"] == "B2"


async def test_get_schema_is_in_the_openapi_document(client: AsyncClient) -> None:
    paths = (await client.get("/api/openapi.json")).json()["paths"]
    assert "/api/v1/schema" in paths


def test_the_web_test_fixture_is_current() -> None:
    """apps/web's unit tests use a copy of the schema; it must match what the API serves."""
    fixture = Path(__file__).resolve().parents[2] / "web" / "src" / "test" / "schema.json"
    regenerate = "uv run python -m nightloom_api.routers.schema > apps/web/src/test/schema.json"
    fixture_json = json.loads(fixture.read_text(encoding="utf-8"))
    assert fixture_json == build_schema().model_dump(mode="json"), f"Regenerate it: {regenerate}"
