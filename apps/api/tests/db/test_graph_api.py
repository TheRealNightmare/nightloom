"""The case graph endpoints against PostgreSQL, Valkey and Neo4j."""

import hashlib
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from neo4j import AsyncTransaction
from neo4j.exceptions import DriverError
from sqlalchemy import select, text

from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth.models import Role, User
from nightloom_api.config import Settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra import Infra, graph
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
}
PERSON_REASON = "The owner asked us to check what their staff expose publicly."
CHANGE_REASON = "Investigation finished for now."


# --- helpers ------------------------------------------------------------------------


async def last_event_id(infra: Infra) -> int:
    async with infra.sessionmaker() as db:
        return int(await db.scalar(text("SELECT coalesce(max(id), 0) FROM audit_events")) or 0)


async def events_since(infra: Infra, mark: int) -> list[AuditEvent]:
    async with infra.sessionmaker() as db:
        rows = await db.execute(
            select(AuditEvent).where(AuditEvent.id > mark).order_by(AuditEvent.id)
        )
        return list(rows.scalars())


async def only_event_since(infra: Infra, mark: int) -> AuditEvent:
    events = await events_since(infra, mark)
    assert len(events) == 1, [(e.action, e.outcome, e.details) for e in events]
    return events[0]


def sha256(text_: str) -> str:
    return hashlib.sha256(text_.encode()).hexdigest()


async def signed_in(new_client: ClientFactory, user: User) -> AsyncClient:
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    return client


def case_url(case_id: str, path: str = "") -> str:
    return f"{PREFIX}/cases/{case_id}{path}"


def entity_url(case_id: str, key: str) -> str:
    return case_url(case_id, "/entities/" + quote(key, safe=""))


async def add_entity(
    client: AsyncClient, case_id: str, type_: str, value: str, **extra: Any
) -> Response:
    return await client.post(
        case_url(case_id, "/entities"),
        json={"type": type_, "value": value, **extra},
        headers=csrf(client),
    )


async def add_relationship(
    client: AsyncClient, case_id: str, type_: str, source: str, target: str, **extra: Any
) -> Response:
    return await client.post(
        case_url(case_id, "/relationships"),
        json={"type": type_, "source_key": source, "target_key": target, **extra},
        headers=csrf(client),
    )


async def delete_entity(client: AsyncClient, case_id: str, key: str) -> Response:
    return await client.delete(entity_url(case_id, key), headers=csrf(client))


async def set_status(client: AsyncClient, case_id: str, new_status: str) -> None:
    response = await client.patch(
        case_url(case_id), json={"status": new_status}, headers=csrf(client)
    )
    assert response.status_code == 200, response.text


@dataclass
class World:
    case_id: str
    owner: User
    clients: dict[str, AsyncClient]


@pytest.fixture
async def world(make_user: UserFactory, new_client: ClientFactory) -> World:
    """A case (no person entities) with an owner, an editor and a viewer; an outsider admin."""
    owner = await make_user("owner@example.org", Role.ANALYST)
    editor = await make_user("editor@example.org", Role.ANALYST)
    viewer = await make_user("viewer@example.org", Role.VIEWER)
    admin = await make_user("admin@example.org", Role.ADMIN)
    clients = {
        "owner": await signed_in(new_client, owner),
        "editor": await signed_in(new_client, editor),
        "viewer": await signed_in(new_client, viewer),
        "admin": await signed_in(new_client, admin),
    }
    created = await clients["owner"].post(
        f"{PREFIX}/cases", json=CASE, headers=csrf(clients["owner"])
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    for user, role in ((editor, "editor"), (viewer, "viewer")):
        added = await clients["owner"].put(
            case_url(case_id, "/members"),
            json={"email": user.email, "role": role},
            headers=csrf(clients["owner"]),
        )
        assert added.status_code == 201
    return World(case_id, owner, clients)


async def person_case(client: AsyncClient) -> str:
    created = await client.post(
        f"{PREFIX}/cases",
        json={**CASE, "allow_person_entities": True, "person_entities_reason": PERSON_REASON},
        headers=csrf(client),
    )
    assert created.status_code == 201
    return str(created.json()["id"])


# --- creating entities ----------------------------------------------------------------------


async def test_create_entity_normalizes_merges_and_audits(world: World, infra: Infra) -> None:
    editor = world.clients["editor"]
    mark = await last_event_id(infra)

    first = await add_entity(editor, world.case_id, "domain", "Example.ORG.", note="seed")

    assert first.status_code == 201, first.text
    body = first.json()
    assert (body["key"], body["value"], body["type"]) == (
        "domain:example.org",
        "example.org",
        "domain",
    )
    assert body["sensitivity"] == "infrastructure"
    assert body["properties"] == {}
    provenance = body["provenance"]
    assert provenance["observation_count"] == 1
    assert provenance["sources"] == ["manual"]
    assert provenance["max_confidence"] == 0.8
    assert provenance["admiralty"] == "B2"
    assert provenance["note"] == "seed"
    assert provenance["first_seen"] == provenance["last_seen"]

    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome, event.case_id) == (
        "entity.create",
        "success",
        uuid.UUID(world.case_id),
    )
    assert (event.target_type, event.target_id) == ("entity", "domain:example.org")
    assert event.details == {"type": "domain", "key": "domain:example.org"}

    again = await add_entity(world.clients["owner"], world.case_id, "domain", "example.org")
    assert again.status_code == 200
    assert again.json()["provenance"]["observation_count"] == 2
    assert again.json()["provenance"]["note"] == "seed"


async def test_create_entity_with_properties(world: World) -> None:
    response = await add_entity(
        world.clients["editor"],
        world.case_id,
        "netblock",
        "192.0.2.77/24",
        properties={"name": "EXAMPLE-NET", "country": "bd"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["key"] == "netblock:192.0.2.0/24"
    assert response.json()["properties"] == {"name": "EXAMPLE-NET", "country": "BD"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"type": "domain", "value": "not a domain"}, "must not contain spaces"),
        ({"type": "ip_address", "value": "999.0.0.1"}, "Not a valid IP address."),
        ({"type": "url", "value": "https://user:pw@example.org/"}, "embedded credentials"),
        ({"type": "asn", "value": "AS0"}, "between 1 and"),
    ],
)
async def test_create_entity_normalization_errors(
    world: World, body: dict[str, str], message: str
) -> None:
    editor = world.clients["editor"]
    response = await editor.post(
        case_url(world.case_id, "/entities"), json=body, headers=csrf(editor)
    )
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert body["value"] not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"type": "spaceship", "value": "x"},
        {"type": "domain"},
        {"type": "domain", "value": ""},
        {"type": "domain", "value": "example.org", "unexpected": 1},
        {"type": "domain", "value": "example.org", "note": "x" * 501},
        {"type": "domain", "value": "example.org", "properties": {"anything": 1}},
        {"type": "netblock", "value": "192.0.2.0/24", "properties": {"country": "Bangladesh"}},
        {"type": "phone", "value": "+16502530000", "phone_region": "GBR"},
    ],
)
async def test_create_entity_validation_errors(world: World, body: dict[str, Any]) -> None:
    editor = world.clients["editor"]
    response = await editor.post(
        case_url(world.case_id, "/entities"), json=body, headers=csrf(editor)
    )
    assert response.status_code == 422, response.text
    assert "Bangladesh" not in response.text  # inputs are never echoed


async def test_invalid_properties_are_located_under_body(world: World) -> None:
    response = await add_entity(
        world.clients["editor"],
        world.case_id,
        "url",
        "https://example.org/",
        properties={"status_code": 42},
    )
    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["loc"] == ["body", "properties", "status_code"]
    assert "input" not in error


# --- the person gate -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_", "value"),
    [
        ("email", "jane@example.org"),
        ("person", "Jane Doe"),
        ("username", "janedoe"),
        ("phone", "+16502530000"),
        ("social_profile", "@janedoe"),
        ("location", "Dhaka"),
        ("image", "portrait.jpg"),
    ],
)
async def test_person_entities_need_a_case_that_allows_them(
    world: World, infra: Infra, type_: str, value: str
) -> None:
    mark = await last_event_id(infra)

    response = await add_entity(world.clients["owner"], world.case_id, type_, value)

    assert response.status_code == 403
    assert response.json()["detail"] == "This case does not allow person-related entities."
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome, event.case_id) == (
        "authz.denied",
        "denied",
        uuid.UUID(world.case_id),
    )
    assert event.details["reason"] == "person_entities_disabled"
    assert event.details["entity_type"] == type_
    assert value not in str(event.details)
    graph_body = (await world.clients["owner"].get(case_url(world.case_id, "/graph"))).json()
    assert graph_body["nodes"] == []


async def test_person_entities_in_an_allowing_case_are_audited_by_digest(
    world: World, infra: Infra
) -> None:
    owner = world.clients["owner"]
    case_id = await person_case(owner)
    mark = await last_event_id(infra)

    email = await add_entity(owner, case_id, "email", "Jane.Doe@Example.org")
    person = await add_entity(owner, case_id, "person", "Jane  Doe")
    phone = await add_entity(owner, case_id, "phone", "020 7946 0958", phone_region="GB")
    domain = await add_entity(owner, case_id, "domain", "example.org")
    link = await add_relationship(
        owner, case_id, "ACCOUNT_OF", "email:jane.doe@example.org", "person:jane doe"
    )
    mail = await add_relationship(
        owner, case_id, "HAS_EMAIL", "domain:example.org", "email:jane.doe@example.org"
    )

    assert [r.status_code for r in (email, person, phone, domain, link, mail)] == [201] * 6
    assert email.json()["sensitivity"] == "person"
    assert phone.json()["key"] == "phone:+442079460958"
    events = await events_since(infra, mark)
    assert [e.action for e in events] == ["entity.create"] * 4 + ["relationship.create"] * 2
    assert events[0].details == {
        "type": "email",
        "key_sha256": sha256("email:jane.doe@example.org"),
    }
    assert events[0].target_id == "sha256:" + sha256("email:jane.doe@example.org")
    assert events[3].details == {"type": "domain", "key": "domain:example.org"}
    assert events[4].details == {
        "type": "ACCOUNT_OF",
        "source_key_sha256": sha256("email:jane.doe@example.org"),
        "target_key_sha256": sha256("person:jane doe"),
    }
    assert events[5].details == {
        "type": "HAS_EMAIL",
        "source_key": "domain:example.org",
        "target_key_sha256": sha256("email:jane.doe@example.org"),
    }
    assert events[5].target_id is not None and events[5].target_id.startswith("sha256:")
    every_event = " ".join(f"{e.target_id} {e.details}" for e in events)
    for personal in ("jane", "Jane", "+442079460958"):
        assert personal not in every_event


async def test_relationships_to_person_entities_are_gated_when_the_case_stops_allowing_them(
    world: World, infra: Infra
) -> None:
    owner = world.clients["owner"]
    case_id = await person_case(owner)
    await add_entity(owner, case_id, "email", "jane@example.org")
    await add_entity(owner, case_id, "person", "Jane Doe")
    response = await owner.patch(
        case_url(case_id),
        json={"allow_person_entities": False, "change_reason": "No longer needed for scope."},
        headers=csrf(owner),
    )
    assert response.status_code == 200
    mark = await last_event_id(infra)

    response = await add_relationship(
        owner, case_id, "ACCOUNT_OF", "email:jane@example.org", "person:jane doe"
    )

    assert response.status_code == 403
    event = await only_event_since(infra, mark)
    assert (event.action, event.details["reason"]) == ("authz.denied", "person_entities_disabled")
    assert "entity_type" not in event.details


# --- relationships ---------------------------------------------------------------------------


async def test_create_relationship(world: World, infra: Infra) -> None:
    editor = world.clients["editor"]
    await add_entity(editor, world.case_id, "domain", "example.org")
    await add_entity(editor, world.case_id, "domain", "www.example.org")
    mark = await last_event_id(infra)

    response = await add_relationship(
        editor, world.case_id, "HAS_SUBDOMAIN", "domain:example.org", "domain:www.example.org"
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == "domain:example.org|HAS_SUBDOMAIN|domain:www.example.org"
    assert (body["type"], body["source_key"], body["target_key"]) == (
        "HAS_SUBDOMAIN",
        "domain:example.org",
        "domain:www.example.org",
    )
    assert body["provenance"]["admiralty"] == "B2"
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome) == ("relationship.create", "success")
    assert (event.target_type, event.target_id) == ("relationship", body["id"])
    assert event.details == {
        "type": "HAS_SUBDOMAIN",
        "source_key": "domain:example.org",
        "target_key": "domain:www.example.org",
    }

    again = await add_relationship(
        editor, world.case_id, "HAS_SUBDOMAIN", "domain:example.org", "domain:www.example.org"
    )
    assert again.status_code == 200
    assert again.json()["provenance"]["observation_count"] == 2


@pytest.mark.parametrize(
    ("type_", "source", "target", "extra", "message"),
    [
        ("RESOLVES_TO", "domain:example.org", "domain:www.example.org", {}, "cannot connect"),
        (
            "HAS_SUBDOMAIN",
            "domain:example.org",
            "domain:missing.example.org",
            {},
            "target_key does not",
        ),
        (
            "HAS_SUBDOMAIN",
            "domain:missing.example.org",
            "domain:example.org",
            {},
            "source_key does not",
        ),
        ("HAS_SUBDOMAIN", "domain:example.org", "domain:example.org", {}, "two different"),
        ("RELATED_TO", "domain:example.org", "domain:www.example.org", {}, "needs a note"),
        (
            "RELATED_TO",
            "domain:example.org",
            "domain:www.example.org",
            {"note": "  "},
            "needs a note",
        ),
        (
            "HAS_SUBDOMAIN",
            "domain:Example.org",
            "domain:www.example.org",
            {},
            "source_key is not a",
        ),
        ("HAS_SUBDOMAIN", "domain:example.org", "nope:x", {}, "target_key is not a"),
    ],
)
async def test_relationship_errors(
    world: World,
    infra: Infra,
    type_: str,
    source: str,
    target: str,
    extra: dict[str, str],
    message: str,
) -> None:
    editor = world.clients["editor"]
    await add_entity(editor, world.case_id, "domain", "example.org")
    await add_entity(editor, world.case_id, "domain", "www.example.org")
    mark = await last_event_id(infra)

    response = await add_relationship(editor, world.case_id, type_, source, target, **extra)

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert await events_since(infra, mark) == []


async def test_related_to_with_a_note(world: World) -> None:
    editor = world.clients["editor"]
    await add_entity(editor, world.case_id, "organization", "ACME")
    await add_entity(editor, world.case_id, "ip_address", "192.0.2.1")
    response = await add_relationship(
        editor,
        world.case_id,
        "RELATED_TO",
        "organization:acme",
        "ip_address:192.0.2.1",
        note="Office uplink.",
    )
    assert response.status_code == 201
    assert response.json()["provenance"]["note"] == "Office uplink."


# --- reading ---------------------------------------------------------------------------------


async def test_get_graph(world: World) -> None:
    editor, viewer = world.clients["editor"], world.clients["viewer"]
    await add_entity(editor, world.case_id, "domain", "example.org")
    await add_entity(editor, world.case_id, "domain", "www.example.org")
    await add_entity(editor, world.case_id, "ip_address", "192.0.2.1")
    await add_relationship(
        editor, world.case_id, "HAS_SUBDOMAIN", "domain:example.org", "domain:www.example.org"
    )

    body = (await viewer.get(case_url(world.case_id, "/graph"))).json()
    assert [node["key"] for node in body["nodes"]] == [
        "domain:example.org",
        "domain:www.example.org",
        "ip_address:192.0.2.1",
    ]
    assert [edge["id"] for edge in body["edges"]] == [
        "domain:example.org|HAS_SUBDOMAIN|domain:www.example.org"
    ]
    assert body["truncated"] is False

    only_ip = await viewer.get(case_url(world.case_id, "/graph"), params={"types": ["ip_address"]})
    assert [node["key"] for node in only_ip.json()["nodes"]] == ["ip_address:192.0.2.1"]
    both = await viewer.get(
        case_url(world.case_id, "/graph"), params={"types": ["ip_address", "domain"]}
    )
    assert len(both.json()["nodes"]) == 3

    cut = (await viewer.get(case_url(world.case_id, "/graph"), params={"limit": 2})).json()
    assert (len(cut["nodes"]), cut["truncated"]) == (2, True)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 10_001}, {"types": "spaceship"}])
async def test_get_graph_validation(world: World, params: dict[str, Any]) -> None:
    response = await world.clients["viewer"].get(case_url(world.case_id, "/graph"), params=params)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("type_", "value", "key"),
    [
        ("domain", "example.org", "domain:example.org"),
        ("url", "HTTPS://Example.org:443/a/b?q=1", "url:https://example.org/a/b?q=1"),
        ("netblock", "2001:db8::/32", "netblock:2001:db8::/32"),
        ("organization", "ACME / Sons", "organization:acme / sons"),
    ],
)
async def test_get_entity_by_encoded_key(world: World, type_: str, value: str, key: str) -> None:
    await add_entity(world.clients["editor"], world.case_id, type_, value)
    response = await world.clients["viewer"].get(entity_url(world.case_id, key))
    assert response.status_code == 200, response.text
    assert response.json()["key"] == key
    assert response.json()["neighbours"] == 0


async def test_get_entity_errors(world: World) -> None:
    viewer = world.clients["viewer"]
    unknown = await viewer.get(entity_url(world.case_id, "domain:example.org"))
    assert (unknown.status_code, unknown.json()["detail"]) == (404, "Entity not found.")
    for key in ("domain:Example.org", "nope:x", "domain:", "url:https://example.org"):
        response = await viewer.get(entity_url(world.case_id, key))
        assert response.status_code == 422, key
        assert response.json()["detail"] == "key is not a canonical entity key."


# --- deleting entities ---------------------------------------------------------------------------


async def test_delete_entity(world: World, infra: Infra) -> None:
    editor = world.clients["editor"]
    await add_entity(editor, world.case_id, "domain", "example.org")
    await add_entity(editor, world.case_id, "domain", "www.example.org")
    await add_relationship(
        editor, world.case_id, "HAS_SUBDOMAIN", "domain:example.org", "domain:www.example.org"
    )
    mark = await last_event_id(infra)

    response = await delete_entity(editor, world.case_id, "domain:www.example.org")

    assert response.status_code == 204
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome) == ("entity.delete", "success")
    assert event.details == {"type": "domain", "key": "domain:www.example.org"}
    body = (await editor.get(case_url(world.case_id, "/graph"))).json()
    assert [n["key"] for n in body["nodes"]] == ["domain:example.org"]
    assert body["edges"] == []

    missing = await delete_entity(editor, world.case_id, "domain:www.example.org")
    assert missing.status_code == 404
    bad = await delete_entity(editor, world.case_id, "domain:WWW.example.org")
    assert bad.status_code == 422
    assert len(await events_since(infra, mark)) == 1


async def test_delete_person_entity_is_audited_by_digest(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    case_id = await person_case(owner)
    await add_entity(owner, case_id, "email", "jane@example.org")
    mark = await last_event_id(infra)
    assert (await delete_entity(owner, case_id, "email:jane@example.org")).status_code == 204
    event = await only_event_since(infra, mark)
    assert event.details == {"type": "email", "key_sha256": sha256("email:jane@example.org")}


# --- access ---------------------------------------------------------------------------------------

OK, FORBIDDEN, HIDDEN = "ok", 403, 404

MATRIX = {
    "read_graph": {"viewer": OK, "editor": OK, "owner": OK, "admin": HIDDEN},
    "read_entity": {"viewer": OK, "editor": OK, "owner": OK, "admin": HIDDEN},
    "add_entity": {"viewer": FORBIDDEN, "editor": OK, "owner": OK, "admin": HIDDEN},
    "add_relationship": {"viewer": FORBIDDEN, "editor": OK, "owner": OK, "admin": HIDDEN},
    "delete_entity": {"viewer": FORBIDDEN, "editor": OK, "owner": OK, "admin": HIDDEN},
}


async def perform(operation: str, client: AsyncClient, case_id: str) -> Response:
    match operation:
        case "read_graph":
            return await client.get(case_url(case_id, "/graph"))
        case "read_entity":
            return await client.get(entity_url(case_id, "domain:example.org"))
        case "add_entity":
            return await add_entity(client, case_id, "domain", "new.example.org")
        case "add_relationship":
            return await add_relationship(
                client, case_id, "HAS_SUBDOMAIN", "domain:example.org", "domain:www.example.org"
            )
        case _:
            return await delete_entity(client, case_id, "domain:www.example.org")


@pytest.mark.parametrize("operation", list(MATRIX))
@pytest.mark.parametrize("who", ["viewer", "editor", "owner", "admin"])
async def test_capability_matrix(world: World, infra: Infra, operation: str, who: str) -> None:
    owner = world.clients["owner"]
    await add_entity(owner, world.case_id, "domain", "example.org")
    await add_entity(owner, world.case_id, "domain", "www.example.org")
    mark = await last_event_id(infra)

    response = await perform(operation, world.clients[who], world.case_id)

    expected = MATRIX[operation][who]
    if expected == OK:
        assert response.status_code in (200, 201, 204), response.text
        return
    assert response.status_code == expected
    event = await only_event_since(infra, mark)
    assert (event.action, event.case_id) == ("authz.denied", uuid.UUID(world.case_id))
    assert event.details["reason"] == (
        "not_a_member" if expected == HIDDEN else "missing_capability"
    )
    if expected == HIDDEN:
        assert response.json()["detail"] == "Case not found."


async def test_unknown_case_is_404(world: World) -> None:
    response = await world.clients["owner"].get(case_url(str(uuid.uuid4()), "/graph"))
    assert response.status_code == 404


async def test_cases_cannot_see_each_others_entities(world: World) -> None:
    owner = world.clients["owner"]
    other = (await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))).json()["id"]
    await add_entity(owner, world.case_id, "domain", "example.org")

    assert (await owner.get(entity_url(other, "domain:example.org"))).status_code == 404
    assert (await owner.get(case_url(other, "/graph"))).json()["nodes"] == []
    response = await add_relationship(
        owner, other, "HAS_SUBDOMAIN", "domain:example.org", "domain:example.org"
    )
    assert response.status_code == 422


# --- case status ----------------------------------------------------------------------------------


async def test_closed_case_graph_is_read_only(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    await add_entity(owner, world.case_id, "domain", "example.org")
    await add_entity(owner, world.case_id, "domain", "www.example.org")
    await set_status(owner, world.case_id, "closed")
    mark = await last_event_id(infra)

    responses = [
        await add_entity(owner, world.case_id, "domain", "new.example.org"),
        await add_relationship(
            owner, world.case_id, "HAS_SUBDOMAIN", "domain:example.org", "domain:www.example.org"
        ),
        await delete_entity(owner, world.case_id, "domain:example.org"),
    ]

    assert [r.status_code for r in responses] == [409, 409, 409]
    assert responses[0].json()["detail"] == "This case is not open. Reopen it to change its graph."
    assert await events_since(infra, mark) == []
    assert (await owner.get(case_url(world.case_id, "/graph"))).status_code == 200

    await set_status(owner, world.case_id, "open")
    assert (await add_entity(owner, world.case_id, "domain", "new.example.org")).status_code == 201

    await set_status(owner, world.case_id, "archived")
    assert (await add_entity(owner, world.case_id, "domain", "x.example.org")).status_code == 409


# --- case deletion ------------------------------------------------------------------------------


async def delete_case(client: AsyncClient, case_id: str) -> Response:
    return await client.request(
        "DELETE", case_url(case_id), json={"confirm_title": CASE["title"]}, headers=csrf(client)
    )


async def neo4j_count(infra: Infra, case_id: str) -> int:
    async with infra.graph.session() as session:
        result = await session.run("MATCH (n:Entity {case_id: $c}) RETURN count(n) AS n", c=case_id)
        return int((await result.single(strict=True))["n"])


async def test_deleting_a_case_deletes_its_graph(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    other = (await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))).json()["id"]
    await add_entity(owner, world.case_id, "domain", "example.org")
    await add_entity(owner, world.case_id, "domain", "www.example.org")
    await add_entity(owner, other, "domain", "example.org")
    mark = await last_event_id(infra)

    assert (await delete_case(owner, world.case_id)).status_code == 204

    assert await neo4j_count(infra, world.case_id) == 0
    assert await neo4j_count(infra, other) == 1
    deleted, graph_deleted = await events_since(infra, mark)
    assert deleted.action == "case.delete"
    assert (graph_deleted.action, graph_deleted.outcome) == ("case.graph_delete", "success")
    assert graph_deleted.details == {"deleted_nodes": 2}
    assert graph_deleted.actor_user_id == world.owner.id


async def test_failed_graph_deletion_is_recorded_not_raised(
    world: World, infra: Infra, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = world.clients["owner"]
    await add_entity(owner, world.case_id, "domain", "example.org")

    async def broken(self: GraphRepository, case_id: object, **_: object) -> int:
        raise DriverError("connection lost")

    monkeypatch.setattr(GraphRepository, "delete_case_graph", broken)
    mark = await last_event_id(infra)

    response = await delete_case(owner, world.case_id)

    assert response.status_code == 204
    deleted, failed = await events_since(infra, mark)
    assert (deleted.action, deleted.outcome) == ("case.delete", "success")
    assert (failed.action, failed.outcome) == ("case.graph_delete", "failure")
    assert failed.details == {"reason": "DriverError"}
    assert (await owner.get(case_url(world.case_id))).status_code == 404
    monkeypatch.undo()
    # Clean up what the "failed" deletion left behind.
    await GraphRepository(infra.graph).delete_case_graph(world.case_id)


# --- Neo4j failures ----------------------------------------------------------------------------


@pytest.fixture
async def broken_graph(
    db_app: FastAPI, closed_port: int, settings_for_port: Callable[..., Settings]
) -> Any:
    driver = graph.create_driver(settings_for_port(closed_port))
    db_app.dependency_overrides[get_graph_repository] = lambda: GraphRepository(driver)
    yield
    db_app.dependency_overrides.pop(get_graph_repository)
    await driver.close()


async def test_unreachable_neo4j_is_503(
    world: World, infra: Infra, broken_graph: None, caplog: pytest.LogCaptureFixture
) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)

    responses = [
        await owner.get(case_url(world.case_id, "/graph")),
        await owner.get(entity_url(world.case_id, "domain:example.org")),
        await add_entity(owner, world.case_id, "domain", "example.org"),
        await add_relationship(
            owner, world.case_id, "HAS_SUBDOMAIN", "domain:example.org", "domain:www.example.org"
        ),
        await delete_entity(owner, world.case_id, "domain:example.org"),
    ]

    assert [r.status_code for r in responses] == [503] * 5
    assert {r.json()["detail"] for r in responses} == {
        "The graph database is unavailable. Try again later."
    }
    assert await events_since(infra, mark) == []
    # Our own log lines name the operation and error type, never keys or values.
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("nightloom_api")]
    assert ours and not any("example.org" in line for line in ours)


async def test_failed_graph_commit_records_a_failure(
    world: World, infra: Infra, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = world.clients["owner"]

    async def broken_commit(self: AsyncTransaction) -> None:
        raise DriverError("connection lost")

    monkeypatch.setattr(AsyncTransaction, "commit", broken_commit)
    mark = await last_event_id(infra)

    response = await add_entity(owner, world.case_id, "domain", "example.org")

    assert response.status_code == 503
    succeeded, failed = await events_since(infra, mark)
    assert (succeeded.action, succeeded.outcome) == ("entity.create", "success")
    assert (failed.action, failed.outcome) == ("entity.create", "failure")
    assert failed.details == {"reason": "graph_commit_failed"}
    assert failed.target_type == "entity"
    monkeypatch.undo()
    assert (await owner.get(case_url(world.case_id, "/graph"))).json()["nodes"] == []


# --- injection ----------------------------------------------------------------------


async def test_cypher_injection_through_the_api_is_inert(world: World) -> None:
    owner = world.clients["owner"]
    await add_entity(owner, world.case_id, "domain", "example.org")
    payload = "x'}) MATCH (n) DETACH DELETE n //"

    response = await add_entity(owner, world.case_id, "organization", payload)

    assert response.status_code == 201
    assert response.json()["value"] == payload
    nodes = (await owner.get(case_url(world.case_id, "/graph"))).json()["nodes"]
    assert sorted(node["type"] for node in nodes) == ["domain", "organization"]
    fetched = await owner.get(entity_url(world.case_id, response.json()["key"]))
    assert fetched.json()["value"] == payload
