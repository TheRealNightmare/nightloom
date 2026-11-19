"""Case exports against PostgreSQL, Valkey and Neo4j."""

import json
import re
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from neo4j.exceptions import DriverError
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, text

from export_checks import graphml_items, read_csv, read_json, read_zip, validate_graphml
from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth.models import Role, User
from nightloom_api.export import service
from nightloom_api.export.redaction import REDACTED_KEY_PREFIX, REDACTED_VALUE
from nightloom_api.graph.repository import GraphCounts, GraphRepository
from nightloom_api.infra import Infra
from nightloom_api.infra.cache import create_client, get_cache
from nightloom_api.infra.graph import get_graph_repository
from support import PREFIX, csrf, login

pytestmark = [pytest.mark.db, pytest.mark.graph]

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]

CASE: dict[str, Any] = {
    "title": "Example exposure review",
    "purpose": "Map the public attack surface of example.org for its owner.",
    "legal_basis": "owned_assets",
    "ethics_acknowledged": True,
    "allow_person_entities": True,
    "person_entities_reason": "The owner asked us to check what their staff expose publicly.",
}
REASON = "Handing the findings to the owner's security team."
# Fictional person data: never in a redacted export, never in an audit event.
PERSON_VALUES = ("alice@example.net", "Alice Example", "alice example")
FILENAME = re.compile(
    r'^attachment; filename="nightloom-example-exposure-review-\d{8}T\d{6}Z\.(json|graphml|zip)"$'
)


@dataclass
class World:
    case_id: str
    clients: dict[str, AsyncClient]


async def signed_in(new_client: ClientFactory, user: User) -> AsyncClient:
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    return client


def case_url(case_id: str, path: str = "") -> str:
    return f"{PREFIX}/cases/{case_id}{path}"


async def post(client: AsyncClient, url: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(url, json=body, headers=csrf(client))
    assert response.status_code in (200, 201), response.text
    result: dict[str, Any] = response.json()
    return result


@pytest.fixture
async def world(make_user: UserFactory, new_client: ClientFactory) -> World:
    """A person-entity case with an owner, an editor and a viewer, and a small graph.

    example.org hosts a URL and has an email address that belongs to a person;
    an organization's name starts with "=" (a spreadsheet formula).
    """
    owner = await make_user("owner@example.org", Role.ANALYST)
    editor = await make_user("editor@example.org", Role.ANALYST)
    viewer = await make_user("viewer@example.org", Role.VIEWER)
    clients = {
        "owner": await signed_in(new_client, owner),
        "editor": await signed_in(new_client, editor),
        "viewer": await signed_in(new_client, viewer),
    }
    owner_client = clients["owner"]
    case_id = (await post(owner_client, f"{PREFIX}/cases", CASE))["id"]
    for user, role in ((editor, "editor"), (viewer, "viewer")):
        response = await owner_client.put(
            case_url(case_id, "/members"),
            json={"email": user.email, "role": role},
            headers=csrf(owner_client),
        )
        assert response.status_code == 201
    entities = case_url(case_id, "/entities")
    relationships = case_url(case_id, "/relationships")
    await post(owner_client, entities, {"type": "domain", "value": "example.org"})
    await post(owner_client, entities, {"type": "url", "value": "https://example.org/login"})
    await post(
        owner_client,
        entities,
        {"type": "email", "value": "alice@example.net", "note": "Alice's work address"},
    )
    await post(owner_client, entities, {"type": "person", "value": "Alice Example"})
    await post(owner_client, entities, {"type": "organization", "value": "=SUM(A1)"})
    for type_, source, target in (
        ("HOSTS", "domain:example.org", "url:https://example.org/login"),
        ("HAS_EMAIL", "domain:example.org", "email:alice@example.net"),
        ("ACCOUNT_OF", "email:alice@example.net", "person:alice example"),
    ):
        await post(
            owner_client,
            relationships,
            {"type": type_, "source_key": source, "target_key": target, "note": "alice"},
        )
    return World(case_id, clients)


async def export(
    client: AsyncClient, case_id: str, export_format: str = "json", **params: Any
) -> Response:
    return await client.get(
        case_url(case_id, "/export"), params={"format": export_format, **params}
    )


async def last_event_id(infra: Infra) -> int:
    async with infra.sessionmaker() as db:
        return int(await db.scalar(text("SELECT coalesce(max(id), 0) FROM audit_events")) or 0)


async def events_since(infra: Infra, mark: int) -> list[AuditEvent]:
    async with infra.sessionmaker() as db:
        rows = await db.execute(
            select(AuditEvent).where(AuditEvent.id > mark).order_by(AuditEvent.id)
        )
        return list(rows.scalars())


def assert_no_person_data(data: bytes | str) -> None:
    content = data if isinstance(data, str) else data.decode()
    for value in PERSON_VALUES:
        assert value not in content


def assert_download_headers(response: Response) -> None:
    assert FILENAME.match(response.headers["content-disposition"]), response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    # Streamed: no length known up front.
    assert "content-length" not in response.headers


# --- formats ------------------------------------------------------------------------------


async def test_viewer_exports_redacted_json(world: World, infra: Infra) -> None:
    mark = await last_event_id(infra)
    response = await export(world.clients["viewer"], world.case_id)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    assert_download_headers(response)
    assert_no_person_data(response.content)
    document = read_json(response.content)
    case = document["case"]
    assert case["id"] == world.case_id
    assert case["title"] == "Example exposure review"
    assert case["legal_basis"] == "owned_assets"
    assert case["exported_by_label"] == "Test User"
    assert case["include_person_data"] is False
    assert {"exported_at", "nightloom_version"} <= set(case)

    by_type = {item["type"]: item for item in document["entities"]}
    assert set(by_type) == {"domain", "url", "email", "person", "organization"}
    for type_ in ("email", "person"):
        redacted = by_type[type_]
        assert redacted["value"] == REDACTED_VALUE
        assert redacted["sensitivity"] == "person"
        assert redacted["key"].startswith(REDACTED_KEY_PREFIX)
        assert redacted["properties"] == {}
        assert redacted["provenance"]["note"] is None
    assert by_type["domain"]["value"] == "example.org"
    assert by_type["domain"]["provenance"]["sources"] == ["manual"]

    keys = {item["key"] for item in document["entities"]}
    relationships = document["relationships"]
    assert len(relationships) == 3
    assert all({rel["source_key"], rel["target_key"]} <= keys for rel in relationships)
    account_of = next(rel for rel in relationships if rel["type"] == "ACCOUNT_OF")
    assert account_of["source_key"] == by_type["email"]["key"]
    assert account_of["target_key"] == by_type["person"]["key"]
    assert account_of["provenance"]["note"] is None
    hosts = next(rel for rel in relationships if rel["type"] == "HOSTS")
    assert hosts["provenance"]["note"] == "alice"  # no person at either end: kept

    [event] = await events_since(infra, mark)
    assert (event.action, event.outcome, event.case_id) == (
        "case.export",
        "success",
        uuid.UUID(world.case_id),
    )
    assert (event.target_type, event.target_id) == ("case", world.case_id)
    assert event.details == {
        "format": "json",
        "entities": 5,
        "relationships": 3,
        "include_person_data": False,
        "redacted_entities": 2,
    }


async def test_redacted_keys_differ_between_exports(world: World) -> None:
    viewer = world.clients["viewer"]
    first = read_json((await export(viewer, world.case_id)).content)
    second = read_json((await export(viewer, world.case_id)).content)

    def email_key(document: dict[str, Any]) -> str:
        return str(next(e["key"] for e in document["entities"] if e["type"] == "email"))

    assert email_key(first) != email_key(second)


async def test_graphml_export_is_valid(world: World) -> None:
    response = await export(world.clients["editor"], world.case_id, "graphml")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/graphml+xml; charset=utf-8"
    assert_download_headers(response)
    assert_no_person_data(response.content)
    nodes, edges = graphml_items(validate_graphml(response.content))
    assert len(nodes) == 5
    assert {node["type"] for node in nodes} == {
        "domain",
        "url",
        "email",
        "person",
        "organization",
    }
    assert all(set(node) >= {"value", "key", "sensitivity", "first_seen"} for node in nodes)
    assert all(node["confidence"] == "0.8" and node["admiralty"] == "B2" for node in nodes)
    assert {edge["type"] for edge in edges} == {"HOSTS", "HAS_EMAIL", "ACCOUNT_OF"}
    person = next(node for node in nodes if node["type"] == "person")
    assert person["value"] == REDACTED_VALUE
    assert any(edge["target_key"] == person["key"] for edge in edges)


async def test_csv_export_zip(world: World) -> None:
    response = await export(world.clients["viewer"], world.case_id, "csv")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert_download_headers(response)
    files = read_zip(response.content)
    assert set(files) == {"README.txt", "entities.csv", "relationships.csv"}
    for content in files.values():
        assert_no_person_data(content)
    entities = read_csv(files["entities.csv"])
    assert len(entities) == 5
    organization = next(row for row in entities if row["type"] == "organization")
    # CSV injection: the formula is shown as text.
    assert organization["value"] == "'=SUM(A1)"
    keys = {row["key"] for row in entities}
    relationships = read_csv(files["relationships.csv"])
    assert len(relationships) == 3
    assert all({row["source_key"], row["target_key"]} <= keys for row in relationships)
    assert "REDACTED" in files["README.txt"]


async def test_empty_case_exports(world: World, make_user: UserFactory) -> None:
    owner = world.clients["owner"]
    empty = await post(owner, f"{PREFIX}/cases", {**CASE, "title": "Empty case"})
    response = await export(owner, empty["id"], "graphml")
    assert response.status_code == 200
    nodes, edges = graphml_items(validate_graphml(response.content))
    assert (nodes, edges) == ([], [])


# --- person data --------------------------------------------------------------------------


async def test_owner_exports_person_data_with_a_reason(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)

    response = await export(
        owner, world.case_id, include_person_data="true", reason=f"  {REASON}  "
    )

    assert response.status_code == 200, response.text
    document = read_json(response.content)
    assert document["case"]["include_person_data"] is True
    values = {item["value"] for item in document["entities"]}
    assert {"alice@example.net", "Alice Example"} <= values
    assert "email:alice@example.net" in {item["key"] for item in document["entities"]}
    [event] = await events_since(infra, mark)
    assert event.action == "case.export"
    assert event.details == {
        "format": "json",
        "entities": 5,
        "relationships": 3,
        "include_person_data": True,
        "reason": REASON,
    }
    assert_no_person_data(json.dumps(event.details))


@pytest.mark.parametrize("reason", [None, "", "too short", "         x"])
async def test_person_export_needs_a_reason(world: World, reason: str | None) -> None:
    params: dict[str, Any] = {"include_person_data": "true"}
    if reason is not None:
        params["reason"] = reason
    response = await export(world.clients["owner"], world.case_id, **params)
    assert response.status_code == 422
    assert "reason" in response.json()["detail"]


async def test_reason_is_capped(world: World) -> None:
    response = await export(
        world.clients["owner"], world.case_id, include_person_data="true", reason="x" * 501
    )
    assert response.status_code == 422


@pytest.mark.parametrize("role", ["editor", "viewer"])
async def test_only_owners_export_person_data(world: World, infra: Infra, role: str) -> None:
    mark = await last_event_id(infra)
    response = await export(
        world.clients[role], world.case_id, include_person_data="true", reason=REASON
    )
    assert response.status_code == 403
    [event] = await events_since(infra, mark)
    assert (event.action, event.outcome) == ("authz.denied", "denied")
    assert event.details["required"] == "export_person_data"
    assert event.details["reason"] == "missing_capability"
    # The check endpoint refuses in the same way.
    check = await world.clients[role].get(
        case_url(world.case_id, "/export/check"),
        params={"include_person_data": "true", "reason": REASON},
    )
    assert check.status_code == 403


async def test_outsiders_get_404(world: World, make_user: UserFactory, new_client: Any) -> None:
    outsider = await signed_in(new_client, await make_user("other@example.org", Role.ANALYST))
    assert (await export(outsider, world.case_id)).status_code == 404


async def test_unknown_format_is_refused(world: World) -> None:
    assert (await export(world.clients["owner"], world.case_id, "pdf")).status_code == 422
    response = await world.clients["owner"].get(case_url(world.case_id, "/export"))
    assert response.status_code == 422


# --- limits -------------------------------------------------------------------------------


async def test_too_many_entities_is_413(
    world: World, infra: Infra, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "MAX_EXPORT_ENTITIES", 4)
    mark = await last_event_id(infra)

    response = await export(world.clients["viewer"], world.case_id, "csv")

    assert response.status_code == 413
    assert response.json()["detail"].startswith("This case has 5 entities; exports are limited")
    [event] = await events_since(infra, mark)
    assert (event.action, event.outcome) == ("case.export", "failure")
    assert event.details["error"] == "too_large"
    check = await world.clients["viewer"].get(case_url(world.case_id, "/export/check"))
    assert check.status_code == 413


async def test_exports_are_rate_limited_per_user(world: World, infra: Infra) -> None:
    viewer = world.clients["viewer"]
    for _ in range(10):
        assert (await export(viewer, world.case_id)).status_code == 200
    mark = await last_event_id(infra)

    refused = await export(viewer, world.case_id, "graphml")

    assert refused.status_code == 429
    assert 0 < int(refused.headers["retry-after"]) <= 600
    assert "at most 10 exports every 10 minutes" in refused.json()["detail"]
    [event] = await events_since(infra, mark)
    assert (event.action, event.outcome) == ("case.export", "failure")
    assert event.details["error"] == "rate_limited"
    check = await viewer.get(case_url(world.case_id, "/export/check"))
    assert check.status_code == 429
    assert "retry-after" in check.headers
    # Another user has their own allowance.
    assert (await export(world.clients["editor"], world.case_id)).status_code == 200


async def test_check_reports_counts_without_using_an_export(world: World) -> None:
    viewer = world.clients["viewer"]
    url = case_url(world.case_id, "/export/check")
    first = await viewer.get(url, params={"format": "csv"})
    assert first.status_code == 200
    assert first.json() == {
        "entities": 5,
        "person_entities": 2,
        "relationships": 3,
        "max_entities": service.MAX_EXPORT_ENTITIES,
        "include_person_data": False,
        "exports_remaining": 10,
    }
    await export(viewer, world.case_id)
    assert (await viewer.get(url)).json()["exports_remaining"] == 9
    owner = await world.clients["owner"].get(
        url, params={"include_person_data": "true", "reason": REASON}
    )
    assert owner.json()["include_person_data"] is True


# --- failures -----------------------------------------------------------------------------


class FailingCounts(GraphRepository):
    async def count_graph(self, case_id: uuid.UUID | str) -> GraphCounts:
        raise DriverError("unavailable")


async def test_neo4j_down_is_503(world: World, db_app: FastAPI, infra: Infra) -> None:
    db_app.dependency_overrides[get_graph_repository] = lambda: FailingCounts(infra.graph)
    try:
        response = await export(world.clients["viewer"], world.case_id)
        check = await world.clients["viewer"].get(case_url(world.case_id, "/export/check"))
    finally:
        del db_app.dependency_overrides[get_graph_repository]
    assert response.status_code == 503
    assert check.status_code == 503


async def test_valkey_down_is_503(
    world: World, db_app: FastAPI, db_settings: Any, closed_port: int
) -> None:
    broken: Redis = create_client(
        db_settings.model_copy(
            update={"valkey_url": SecretStr(f"redis://127.0.0.1:{closed_port}/15")}
        )
    )
    db_app.dependency_overrides[get_cache] = lambda: broken
    try:
        response = await export(world.clients["viewer"], world.case_id)
        check = await world.clients["viewer"].get(case_url(world.case_id, "/export/check"))
    finally:
        del db_app.dependency_overrides[get_cache]
        await broken.aclose()
    assert response.status_code == 503
    assert check.status_code == 503
    assert response.json()["detail"] == "Exports are unavailable right now. Try again later."
