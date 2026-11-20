"""Personal graph layouts against PostgreSQL, Valkey and Neo4j."""

import json
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from neo4j.exceptions import DriverError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from nightloom_api.auth.models import Role, User
from nightloom_api.infra import Infra
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.layouts import service as layout_service
from nightloom_api.layouts.ratelimit import MAX_WRITES
from nightloom_api.layouts.schemas import MAX_BODY_BYTES, MAX_POSITIONS
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


@dataclass
class World:
    case_id: str
    owner: User
    clients: dict[str, AsyncClient]


def case_url(case_id: str, path: str = "") -> str:
    return f"{PREFIX}/cases/{case_id}{path}"


async def signed_in(new_client: ClientFactory, user: User) -> AsyncClient:
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    return client


async def last_event_id(infra: Infra) -> int:
    async with infra.sessionmaker() as db:
        return int(await db.scalar(text("SELECT coalesce(max(id), 0) FROM audit_events")) or 0)


async def add_entity(client: AsyncClient, case_id: str, type_: str, value: str) -> Response:
    return await client.post(
        case_url(case_id, "/entities"), json={"type": type_, "value": value}, headers=csrf(client)
    )


async def delete_entity(client: AsyncClient, case_id: str, key: str) -> Response:
    return await client.delete(
        case_url(case_id, "/entities/" + quote(key, safe="")), headers=csrf(client)
    )


async def set_status(client: AsyncClient, case_id: str, new_status: str) -> None:
    response = await client.patch(
        case_url(case_id), json={"status": new_status}, headers=csrf(client)
    )
    assert response.status_code == 200, response.text


DOMAIN = "domain:example.org"
WWW = "domain:www.example.org"
IP = "ip_address:192.0.2.1"


@pytest.fixture
async def world(make_user: UserFactory, new_client: ClientFactory) -> World:
    """A case with an owner, an editor and a viewer; an outsider admin."""
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


async def put_layout(client: AsyncClient, case_id: str, positions: Any) -> Response:
    return await client.put(
        case_url(case_id, "/layout"), json={"positions": positions}, headers=csrf(client)
    )


async def get_positions(client: AsyncClient, case_id: str) -> dict[str, list[float]]:
    response = await client.get(case_url(case_id, "/layout"))
    assert response.status_code == 200, response.text
    positions: dict[str, list[float]] = response.json()["positions"]
    return positions


async def seed(world: World) -> None:
    editor = world.clients["editor"]
    for value in ("example.org", "www.example.org"):
        assert (await add_entity(editor, world.case_id, "domain", value)).status_code == 201
    assert (await add_entity(editor, world.case_id, "ip_address", "192.0.2.1")).status_code == 201


async def test_empty_until_saved(world: World) -> None:
    response = await world.clients["viewer"].get(case_url(world.case_id, "/layout"))
    assert response.status_code == 200
    assert response.json() == {"positions": {}, "updated_at": None}


async def test_save_and_read_back_without_auditing(world: World, infra: Infra) -> None:
    await seed(world)
    mark = await last_event_id(infra)
    viewer = world.clients["viewer"]

    saved = await put_layout(viewer, world.case_id, {DOMAIN: [1.5, -2], WWW: [0, 1e6]})

    assert saved.status_code == 204, saved.text
    body = (await viewer.get(case_url(world.case_id, "/layout"))).json()
    assert body["positions"] == {DOMAIN: [1.5, -2.0], WWW: [0.0, 1e6]}
    assert body["updated_at"] is not None
    # Replaced, not merged.
    assert (await put_layout(viewer, world.case_id, {IP: [3, 4]})).status_code == 204
    assert await get_positions(viewer, world.case_id) == {IP: [3.0, 4.0]}
    assert (await put_layout(viewer, world.case_id, {})).status_code == 204
    assert await get_positions(viewer, world.case_id) == {}
    # A personal preference: nothing is audited.
    assert await last_event_id(infra) == mark


async def test_layouts_are_per_user(world: World) -> None:
    await seed(world)
    owner, editor = world.clients["owner"], world.clients["editor"]
    assert (await put_layout(owner, world.case_id, {DOMAIN: [1, 1]})).status_code == 204
    assert (
        await put_layout(editor, world.case_id, {DOMAIN: [9, 9], IP: [2, 2]})
    ).status_code == 204

    assert await get_positions(owner, world.case_id) == {DOMAIN: [1.0, 1.0]}
    assert await get_positions(editor, world.case_id) == {DOMAIN: [9.0, 9.0], IP: [2.0, 2.0]}
    assert await get_positions(world.clients["viewer"], world.case_id) == {}


async def test_non_members_get_404(world: World) -> None:
    await seed(world)
    outsider = world.clients["admin"]
    assert (await outsider.get(case_url(world.case_id, "/layout"))).status_code == 404
    response = await put_layout(outsider, world.case_id, {DOMAIN: [0, 0]})
    assert response.status_code == 404
    assert response.json() == {"detail": "Case not found."}
    missing = str(uuid.uuid4())
    assert (await world.clients["owner"].get(case_url(missing, "/layout"))).status_code == 404


async def test_needs_csrf_and_a_session(world: World, new_client: ClientFactory) -> None:
    viewer = world.clients["viewer"]
    no_csrf = await viewer.put(case_url(world.case_id, "/layout"), json={"positions": {}})
    assert no_csrf.status_code == 403
    anonymous = new_client()
    assert (await anonymous.get(case_url(world.case_id, "/layout"))).status_code == 401


async def test_allowed_on_closed_and_archived_cases(world: World) -> None:
    await seed(world)
    for state in ("closed", "archived"):
        await set_status(world.clients["owner"], world.case_id, state)
        viewer = world.clients["viewer"]
        assert (await put_layout(viewer, world.case_id, {DOMAIN: [5, 5]})).status_code == 204
        assert await get_positions(viewer, world.case_id) == {DOMAIN: [5.0, 5.0]}
        await set_status(world.clients["owner"], world.case_id, "open")


@pytest.mark.parametrize(
    "positions",
    [
        {"domain:Example.org": [0, 0]},  # not canonical
        {"not a key": [0, 0]},
        {"group:domain:example.org|url": [0, 0]},
        {DOMAIN: [0]},
        {DOMAIN: [0, 0, 0]},
        {DOMAIN: ["1", 2]},
        {DOMAIN: [True, 2]},
        {DOMAIN: [None, 2]},
        {DOMAIN: [1e6 + 1, 0]},
        {DOMAIN: [0, -1e6 - 1]},
        {DOMAIN: {"x": 0, "y": 0}},
        [[DOMAIN, 0, 0]],
    ],
)
async def test_invalid_positions_get_422(world: World, positions: Any) -> None:
    response = await put_layout(world.clients["viewer"], world.case_id, positions)
    assert response.status_code == 422, response.text
    # The rejected input is never echoed back.
    assert "input" not in json.dumps(response.json())
    assert "Example.org" not in response.text


@pytest.mark.parametrize("body", [b"NaN", b'{"positions": {"domain:example.org": [NaN, 0]}}'])
async def test_non_finite_or_malformed_bodies_get_422(world: World, body: bytes) -> None:
    viewer = world.clients["viewer"]
    response = await viewer.put(
        case_url(world.case_id, "/layout"),
        content=body,
        headers={**csrf(viewer), "content-type": "application/json"},
    )
    assert response.status_code == 422, response.text


async def test_extra_fields_are_refused(world: World) -> None:
    viewer = world.clients["viewer"]
    response = await viewer.put(
        case_url(world.case_id, "/layout"),
        json={"positions": {}, "other": 1},
        headers=csrf(viewer),
    )
    assert response.status_code == 422


async def test_at_most_max_positions(world: World) -> None:
    viewer = world.clients["viewer"]
    keys = [f"ip_address:10.0.{i // 256}.{i % 256}" for i in range(MAX_POSITIONS + 1)]
    too_many = {key: [0, 0] for key in keys}
    response = await put_layout(viewer, world.case_id, too_many)
    assert response.status_code == 422
    assert "10,000" in response.text
    just_enough = dict(list(too_many.items())[:MAX_POSITIONS])
    assert (await put_layout(viewer, world.case_id, just_enough)).status_code == 204


async def test_bodies_over_one_mebibyte_get_413(world: World) -> None:
    viewer = world.clients["viewer"]
    padding = " " * (MAX_BODY_BYTES + 1)
    response = await viewer.put(
        case_url(world.case_id, "/layout"),
        content=b'{"positions": {}}' + padding.encode(),
        headers={**csrf(viewer), "content-type": "application/json"},
    )
    assert response.status_code == 413

    # Without a Content-Length (chunked), the size is counted while reading.
    async def chunks() -> Any:
        yield b'{"positions": {}}'
        for _ in range(MAX_BODY_BYTES // 65536 + 1):
            yield b" " * 65536

    streamed = await viewer.put(
        case_url(world.case_id, "/layout"),
        content=chunks(),
        headers={**csrf(viewer), "content-type": "application/json"},
    )
    assert streamed.status_code == 413


async def test_saves_are_rate_limited_per_user(world: World) -> None:
    viewer, owner = world.clients["viewer"], world.clients["owner"]
    for _ in range(MAX_WRITES):
        assert (await put_layout(viewer, world.case_id, {})).status_code == 204
    refused = await put_layout(viewer, world.case_id, {})
    assert refused.status_code == 429
    assert 0 < int(refused.headers["retry-after"]) <= 60
    # Reading is never limited, and other users have their own budget.
    assert (await viewer.get(case_url(world.case_id, "/layout"))).status_code == 200
    assert (await put_layout(owner, world.case_id, {})).status_code == 204


async def test_pins_of_deleted_entities_are_dropped_on_read(world: World) -> None:
    await seed(world)
    viewer = world.clients["viewer"]
    positions = {DOMAIN: [1, 2], WWW: [3, 4], "domain:never-added.example": [5, 6]}
    assert (await put_layout(viewer, world.case_id, positions)).status_code == 204
    assert (await delete_entity(world.clients["editor"], world.case_id, WWW)).status_code == 204

    assert await get_positions(viewer, world.case_id) == {DOMAIN: [1.0, 2.0]}


async def test_deleted_with_the_case_and_the_user(
    world: World, infra: Infra, make_user: UserFactory, new_client: ClientFactory
) -> None:
    await seed(world)
    owner = world.clients["owner"]
    assert (await put_layout(owner, world.case_id, {DOMAIN: [1, 1]})).status_code == 204
    assert (await put_layout(world.clients["viewer"], world.case_id, {})).status_code == 204

    # A second case of the same owner keeps its layout.
    other = await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))
    other_id = other.json()["id"]
    assert (await put_layout(owner, other_id, {})).status_code == 204

    deleted = await owner.request(
        "DELETE",
        case_url(world.case_id),
        json={"confirm_title": CASE["title"]},
        headers=csrf(owner),
    )
    assert deleted.status_code == 204, deleted.text

    async with infra.sessionmaker() as db:
        rows = (await db.execute(text("SELECT case_id::text FROM graph_layouts"))).scalars().all()
    assert rows == [other_id]

    # Deleting a user deletes their layouts too (one who never acted, so has no audit trail).
    quiet = await make_user("quiet@example.org", Role.ANALYST)
    async with infra.sessionmaker() as db:
        await db.execute(
            text("INSERT INTO graph_layouts (case_id, user_id) VALUES (:case, :user)"),
            {"case": other_id, "user": quiet.id},
        )
        await db.commit()
        assert await db.scalar(text("SELECT count(*) FROM graph_layouts")) == 2
        await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": quiet.id})
        await db.commit()
        remaining = await db.scalar(text("SELECT count(*) FROM graph_layouts"))
    assert remaining == 1


async def test_global_viewer_may_arrange_their_view(
    world: World, make_user: UserFactory, new_client: ClientFactory
) -> None:
    # The world's viewer is a global viewer; a fresh one, added as viewer, also may.
    second = await make_user("second-viewer@example.org", Role.VIEWER)
    owner = world.clients["owner"]
    added = await owner.put(
        case_url(world.case_id, "/members"),
        json={"email": second.email, "role": "viewer"},
        headers=csrf(owner),
    )
    assert added.status_code == 201
    client = await signed_in(new_client, second)
    assert (await put_layout(client, world.case_id, {})).status_code == 204


class BrokenGraph:
    """A graph repository whose reads fail like an unreachable Neo4j."""

    async def existing_keys(self, case_id: object, keys: object) -> set[str]:
        raise DriverError("unavailable")


async def test_reading_needs_the_graph_database(world: World, db_app: FastAPI) -> None:
    await seed(world)
    viewer = world.clients["viewer"]
    assert (await put_layout(viewer, world.case_id, {DOMAIN: [1, 1]})).status_code == 204
    db_app.dependency_overrides[get_graph_repository] = BrokenGraph
    try:
        response = await viewer.get(case_url(world.case_id, "/layout"))
    finally:
        del db_app.dependency_overrides[get_graph_repository]
    assert response.status_code == 503
    # An empty layout needs no graph lookup.
    assert (await put_layout(viewer, world.case_id, {})).status_code == 204
    db_app.dependency_overrides[get_graph_repository] = BrokenGraph
    try:
        assert (await viewer.get(case_url(world.case_id, "/layout"))).status_code == 200
    finally:
        del db_app.dependency_overrides[get_graph_repository]


async def test_a_case_deleted_during_the_save_gets_404(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def vanished(*_args: object) -> None:
        raise IntegrityError("INSERT", {}, Exception("fk_graph_layouts_case_id_cases"))

    monkeypatch.setattr(layout_service, "replace_layout", vanished)
    response = await put_layout(world.clients["viewer"], world.case_id, {})
    assert response.status_code == 404
    assert response.json() == {"detail": "Case not found."}
