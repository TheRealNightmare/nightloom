"""Saved views against PostgreSQL, Valkey and Neo4j."""

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

from nightloom_api.audit.verify import verify_chain
from nightloom_api.auth.models import Role, User
from nightloom_api.infra import Infra
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.routers import views as views_router
from nightloom_api.views.schemas import (
    MAX_BODY_BYTES,
    MAX_EXPANDED_GROUPS,
    MAX_VIEWS_PER_CASE,
    NAME_MAX_LENGTH,
)
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

DOMAIN = "domain:example.org"
WWW = "domain:www.example.org"
IP = "ip_address:192.0.2.1"


def state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": 1,
        "mode": "2d",
        "camera": {"x": 1.5, "y": -2.0, "ratio": 0.5, "angle": 0.25},
        "filters": {
            "hidden_types": ["url"],
            "hidden_sources": ["dns"],
            "hidden_sensitivities": [],
            "seen": {"kind": "range", "from": "2026-11-01", "to": None},
            "min_confidence": 0.5,
            "min_reliability": "B",
            "min_credibility": 3,
            "focus": {"key": DOMAIN, "hops": 2},
            "hide_isolated": True,
        },
        "groups": {"expanded": [f"group:{DOMAIN}|url"]},
        "positions": {DOMAIN: [1, 2], WWW: [3, 4]},
    }
    base.update(overrides)
    return base


@dataclass
class World:
    case_id: str
    users: dict[str, User]
    clients: dict[str, AsyncClient]


def views_url(case_id: str, path: str = "") -> str:
    return f"{PREFIX}/cases/{case_id}/views{path}"


async def signed_in(new_client: ClientFactory, user: User) -> AsyncClient:
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    return client


async def add_entity(client: AsyncClient, case_id: str, type_: str, value: str) -> Response:
    return await client.post(
        f"{PREFIX}/cases/{case_id}/entities",
        json={"type": type_, "value": value},
        headers=csrf(client),
    )


async def set_status(client: AsyncClient, case_id: str, new_status: str) -> None:
    response = await client.patch(
        f"{PREFIX}/cases/{case_id}", json={"status": new_status}, headers=csrf(client)
    )
    assert response.status_code == 200, response.text


@pytest.fixture
async def world(make_user: UserFactory, new_client: ClientFactory) -> World:
    """A case with an owner, two editors and a viewer; an outsider admin."""
    users = {
        "owner": await make_user("owner@example.org", Role.ANALYST, display_name="Olive Owner"),
        "editor": await make_user("editor@example.org", Role.ANALYST, display_name="Eddie Editor"),
        "editor2": await make_user("editor2@example.org", Role.ANALYST, display_name="Erin Two"),
        "viewer": await make_user("viewer@example.org", Role.VIEWER, display_name="Vic Viewer"),
        "admin": await make_user("admin@example.org", Role.ADMIN, display_name="Ada Admin"),
    }
    clients = {name: await signed_in(new_client, user) for name, user in users.items()}
    owner = clients["owner"]
    created = await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    for name, role in (("editor", "editor"), ("editor2", "editor"), ("viewer", "viewer")):
        added = await owner.put(
            f"{PREFIX}/cases/{case_id}/members",
            json={"email": users[name].email, "role": role},
            headers=csrf(owner),
        )
        assert added.status_code == 201
    return World(case_id, users, clients)


async def create(
    client: AsyncClient, case_id: str, name: str = "Overview", body_state: Any = None
) -> Response:
    return await client.post(
        views_url(case_id),
        json={"name": name, "state": state() if body_state is None else body_state},
        headers=csrf(client),
    )


async def patch(client: AsyncClient, case_id: str, view_id: str, body: Any) -> Response:
    return await client.patch(views_url(case_id, f"/{view_id}"), json=body, headers=csrf(client))


async def delete(client: AsyncClient, case_id: str, view_id: str) -> Response:
    return await client.delete(views_url(case_id, f"/{view_id}"), headers=csrf(client))


async def seed(world: World) -> None:
    editor = world.clients["editor"]
    for value in ("example.org", "www.example.org"):
        assert (await add_entity(editor, world.case_id, "domain", value)).status_code == 201
    assert (await add_entity(editor, world.case_id, "ip_address", "192.0.2.1")).status_code == 201


async def events_since(infra: Infra, mark: int) -> list[dict[str, Any]]:
    async with infra.sessionmaker() as db:
        rows = await db.execute(
            text(
                "SELECT action, outcome, target_type, target_id, case_id::text AS case_id, details "
                "FROM audit_events WHERE id > :mark ORDER BY id"
            ),
            {"mark": mark},
        )
        return [dict(row._mapping) for row in rows]


async def last_event_id(infra: Infra) -> int:
    async with infra.sessionmaker() as db:
        return int(await db.scalar(text("SELECT coalesce(max(id), 0) FROM audit_events")) or 0)


# --- reading and writing ------------------------------------------------------------------


async def test_empty_until_saved(world: World) -> None:
    response = await world.clients["viewer"].get(views_url(world.case_id))
    assert response.status_code == 200
    assert response.json() == []


async def test_save_list_and_read(world: World) -> None:
    await seed(world)
    created = await create(world.clients["editor"], world.case_id, name="  Overview  ")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Overview"
    editor = world.users["editor"]
    assert body["created_by"] == {"id": str(editor.id), "display_name": "Eddie Editor"}
    assert body["updated_by"] == body["created_by"]
    assert body["state"] == state(positions={DOMAIN: [1.0, 2.0], WWW: [3.0, 4.0]})

    listed = (await world.clients["viewer"].get(views_url(world.case_id))).json()
    assert len(listed) == 1
    assert set(listed[0]) == {"id", "name", "created_by", "updated_by", "created_at", "updated_at"}
    assert listed[0]["created_by"]["display_name"] == "Eddie Editor"

    read = await world.clients["viewer"].get(views_url(world.case_id, f"/{body['id']}"))
    assert read.status_code == 200
    assert read.json() == body


async def test_listed_by_name_ignoring_case(world: World) -> None:
    owner = world.clients["owner"]
    for name in ("beta", "Alpha", "gamma"):
        assert (await create(owner, world.case_id, name=name)).status_code == 201
    names = [view["name"] for view in (await owner.get(views_url(world.case_id))).json()]
    assert names == ["Alpha", "beta", "gamma"]


async def test_defaults_fill_missing_filter_fields(world: World) -> None:
    minimal = state(filters={}, groups={}, positions={})
    created = await create(world.clients["owner"], world.case_id, body_state=minimal)
    assert created.status_code == 201, created.text
    assert created.json()["state"]["filters"] == {
        "hidden_types": [],
        "hidden_sources": [],
        "hidden_sensitivities": [],
        "seen": {"kind": "all"},
        "min_confidence": 0.0,
        "min_reliability": None,
        "min_credibility": None,
        "focus": None,
        "hide_isolated": False,
    }
    assert created.json()["state"]["groups"] == {"expanded": []}


async def test_rename_and_replace_state(world: World) -> None:
    await seed(world)
    editor = world.clients["editor"]
    view_id = (await create(editor, world.case_id)).json()["id"]

    renamed = await patch(editor, world.case_id, view_id, {"name": "Infrastructure"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Infrastructure"
    assert renamed.json()["state"]["camera"]["ratio"] == 0.5

    new_state = state(camera={"x": 0, "y": 0, "ratio": 2, "angle": -1}, positions={IP: [9, 9]})
    replaced = await patch(world.clients["owner"], world.case_id, view_id, {"state": new_state})
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["name"] == "Infrastructure"
    assert body["state"]["positions"] == {IP: [9.0, 9.0]}
    assert body["state"]["camera"] == {"x": 0.0, "y": 0.0, "ratio": 2.0, "angle": -1.0}
    assert body["created_by"]["display_name"] == "Eddie Editor"
    assert body["updated_by"]["display_name"] == "Olive Owner"
    assert body["updated_at"] > body["created_at"]


async def test_patch_needs_a_name_or_a_state(world: World) -> None:
    owner = world.clients["owner"]
    view_id = (await create(owner, world.case_id)).json()["id"]
    response = await patch(owner, world.case_id, view_id, {})
    assert response.status_code == 422


async def test_delete(world: World) -> None:
    editor = world.clients["editor"]
    view_id = (await create(editor, world.case_id)).json()["id"]
    assert (await delete(editor, world.case_id, view_id)).status_code == 204
    assert (await editor.get(views_url(world.case_id, f"/{view_id}"))).status_code == 404
    assert (await delete(editor, world.case_id, view_id)).status_code == 404


# --- who may do what ------------------------------------------------------------------------


async def test_viewers_read_but_cannot_write(world: World, infra: Infra) -> None:
    view_id = (await create(world.clients["owner"], world.case_id)).json()["id"]
    viewer = world.clients["viewer"]
    assert (await viewer.get(views_url(world.case_id))).status_code == 200
    assert (await viewer.get(views_url(world.case_id, f"/{view_id}"))).status_code == 200
    mark = await last_event_id(infra)
    assert (await create(viewer, world.case_id, name="Mine")).status_code == 403
    assert (await patch(viewer, world.case_id, view_id, {"name": "Mine"})).status_code == 403
    assert (await delete(viewer, world.case_id, view_id)).status_code == 403
    denied = await events_since(infra, mark)
    assert [event["action"] for event in denied] == ["authz.denied"] * 3
    assert all(event["details"]["required"] == "edit" for event in denied)


async def test_non_members_get_404(world: World) -> None:
    view_id = (await create(world.clients["owner"], world.case_id)).json()["id"]
    outsider = world.clients["admin"]
    for response in (
        await outsider.get(views_url(world.case_id)),
        await outsider.get(views_url(world.case_id, f"/{view_id}")),
        await create(outsider, world.case_id, name="Mine"),
        await patch(outsider, world.case_id, view_id, {"name": "Mine"}),
        await delete(outsider, world.case_id, view_id),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Case not found."}


async def test_a_view_of_another_case_is_404(world: World) -> None:
    owner = world.clients["owner"]
    view_id = (await create(owner, world.case_id)).json()["id"]
    other = (await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))).json()["id"]
    assert (await owner.get(views_url(other, f"/{view_id}"))).status_code == 404
    assert (await patch(owner, other, view_id, {"name": "Moved"})).status_code == 404
    assert (await delete(owner, other, view_id)).status_code == 404
    unknown = str(uuid.uuid4())
    response = await owner.get(views_url(world.case_id, f"/{unknown}"))
    assert response.status_code == 404
    assert response.json() == {"detail": "View not found."}
    assert (await owner.get(views_url(world.case_id, "/not-a-uuid"))).status_code == 422


async def test_only_the_creator_or_an_owner_changes_a_view(world: World, infra: Infra) -> None:
    editor, other = world.clients["editor"], world.clients["editor2"]
    view_id = (await create(editor, world.case_id)).json()["id"]

    mark = await last_event_id(infra)
    assert (await patch(other, world.case_id, view_id, {"name": "Taken over"})).status_code == 403
    assert (await delete(other, world.case_id, view_id)).status_code == 403
    denied = await events_since(infra, mark)
    assert [event["details"]["reason"] for event in denied] == ["not_view_creator"] * 2
    assert denied[0]["details"]["view_id"] == view_id

    # The other editor may still save views of their own, and the creator changes theirs.
    assert (await create(other, world.case_id, name="Second")).status_code == 201
    assert (await patch(editor, world.case_id, view_id, {"name": "Mine"})).status_code == 200
    # An owner may change and delete anyone's.
    owner = world.clients["owner"]
    assert (await patch(owner, world.case_id, view_id, {"name": "Owned"})).status_code == 200
    assert (await delete(owner, world.case_id, view_id)).status_code == 204


async def test_a_demoted_creator_can_no_longer_change_their_view(world: World) -> None:
    editor = world.clients["editor"]
    view_id = (await create(editor, world.case_id)).json()["id"]
    owner = world.clients["owner"]
    demoted = await owner.put(
        f"{PREFIX}/cases/{world.case_id}/members",
        json={"email": world.users["editor"].email, "role": "viewer"},
        headers=csrf(owner),
    )
    assert demoted.status_code == 200, demoted.text
    assert (await patch(editor, world.case_id, view_id, {"name": "Mine"})).status_code == 403


@pytest.mark.parametrize("closed_state", ["closed", "archived"])
async def test_refused_on_closed_and_archived_cases(world: World, closed_state: str) -> None:
    owner = world.clients["owner"]
    view_id = (await create(owner, world.case_id)).json()["id"]
    await set_status(owner, world.case_id, closed_state)

    for response in (
        await create(owner, world.case_id, name="Later"),
        await patch(owner, world.case_id, view_id, {"name": "Later"}),
        await delete(owner, world.case_id, view_id),
    ):
        assert response.status_code == 409
        assert response.json() == {"detail": views_router.CASE_NOT_OPEN}
    # Reading still works.
    assert (await owner.get(views_url(world.case_id))).status_code == 200
    assert (await owner.get(views_url(world.case_id, f"/{view_id}"))).status_code == 200


async def test_needs_csrf_and_a_session(world: World, new_client: ClientFactory) -> None:
    owner = world.clients["owner"]
    no_csrf = await owner.post(views_url(world.case_id), json={"name": "x", "state": state()})
    assert no_csrf.status_code == 403
    anonymous = new_client()
    assert (await anonymous.get(views_url(world.case_id))).status_code == 401


# --- validation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * (NAME_MAX_LENGTH + 1), "tab\there", "new\nline", "bell\x07", "sep\u2028end"],
)
async def test_invalid_names_get_422(world: World, name: str) -> None:
    response = await create(world.clients["owner"], world.case_id, name=name)
    assert response.status_code == 422, response.text
    assert all("input" not in error for error in response.json()["detail"])


async def test_name_length_limit(world: World) -> None:
    owner = world.clients["owner"]
    longest = "n" * NAME_MAX_LENGTH
    assert (await create(owner, world.case_id, name=f"  {longest}  ")).status_code == 201
    assert (await create(owner, world.case_id, name="Ünïcode - ok")).status_code == 201


def _camera(**overrides: Any) -> dict[str, Any]:
    return {"x": 0, "y": 0, "ratio": 1, "angle": 0, **overrides}


def _filters(**overrides: Any) -> dict[str, Any]:
    return {**state()["filters"], **overrides}


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 2},
        {"version": "1"},
        {"mode": "3d"},
        {"camera": _camera(ratio=0)},
        {"camera": _camera(ratio=-1)},
        {"camera": _camera(ratio=1e5)},
        {"camera": _camera(angle=7)},
        {"camera": _camera(x=1e6 + 1)},
        {"camera": _camera(y="1")},
        {"camera": _camera(zoom=1)},
        {"camera": {"x": 0, "y": 0, "ratio": 1}},
        {"filters": _filters(hidden_types=["not_a_type"])},
        {"filters": _filters(hidden_sources=[""])},
        {"filters": _filters(hidden_sources=["x" * 101])},
        {"filters": _filters(hidden_sources=["s"] * 101)},
        {"filters": _filters(hidden_sensitivities=["secret"])},
        {"filters": _filters(seen={"kind": "preset", "preset": "1y"})},
        {"filters": _filters(seen={"kind": "range", "from": "yesterday"})},
        {"filters": _filters(seen={"kind": "range", "from": 1700000000})},
        {"filters": _filters(seen={"kind": "sometime"})},
        {"filters": _filters(min_confidence=1.5)},
        {"filters": _filters(min_reliability="G")},
        {"filters": _filters(min_credibility=7)},
        {"filters": _filters(min_credibility="3")},
        {"filters": _filters(focus={"key": "domain:Example.org", "hops": 1})},
        {"filters": _filters(focus={"key": DOMAIN, "hops": 4})},
        {"filters": _filters(hide_isolated="yes")},
        {"filters": _filters(search="x")},
        {"groups": {"expanded": ["domain:example.org"]}},
        {"groups": {"expanded": [f"group:{DOMAIN}|nothing"]}},
        {"groups": {"expanded": ["group:domain:Example.org|url"]}},
        {"groups": {"expanded": ["group:no-separator"]}},
        {"groups": {"expanded": [], "collapsed": []}},
        {"positions": {"domain:Example.org": [0, 0]}},
        {"positions": {DOMAIN: [0]}},
        {"positions": {DOMAIN: [1e6 + 1, 0]}},
        {"positions": [[DOMAIN, 0, 0]]},
        {"extra": True},
    ],
)
async def test_invalid_states_get_422(world: World, overrides: dict[str, Any]) -> None:
    response = await create(world.clients["owner"], world.case_id, body_state=state(**overrides))
    assert response.status_code == 422, response.text
    assert all("input" not in error for error in response.json()["detail"])
    assert "Example.org" not in response.text


@pytest.mark.parametrize("missing", ["version", "mode", "camera", "filters", "groups", "positions"])
async def test_every_state_field_is_required(world: World, missing: str) -> None:
    incomplete = state()
    del incomplete[missing]
    response = await create(world.clients["owner"], world.case_id, body_state=incomplete)
    assert response.status_code == 422


async def test_non_finite_numbers_get_422(world: World) -> None:
    owner = world.clients["owner"]
    raw = json.dumps({"name": "x", "state": state()}).replace('"ratio": 0.5', '"ratio": NaN')
    response = await owner.post(
        views_url(world.case_id),
        content=raw.encode(),
        headers={**csrf(owner), "content-type": "application/json"},
    )
    assert response.status_code == 422


async def test_extra_top_level_fields_are_refused(world: World) -> None:
    owner = world.clients["owner"]
    response = await owner.post(
        views_url(world.case_id),
        json={"name": "x", "state": state(), "shared": True},
        headers=csrf(owner),
    )
    assert response.status_code == 422
    view_id = (await create(owner, world.case_id)).json()["id"]
    assert (await patch(owner, world.case_id, view_id, {"owner": "me"})).status_code == 422


async def test_duplicates_in_lists_are_dropped(world: World) -> None:
    filters = _filters(hidden_types=["url", "url"], hidden_sources=["dns", "dns"])
    groups = {"expanded": [f"group:{DOMAIN}|url", f"group:{DOMAIN}|url"]}
    created = await create(
        world.clients["owner"], world.case_id, body_state=state(filters=filters, groups=groups)
    )
    assert created.status_code == 201, created.text
    saved = created.json()["state"]
    assert saved["filters"]["hidden_types"] == ["url"]
    assert saved["filters"]["hidden_sources"] == ["dns"]
    assert saved["groups"]["expanded"] == [f"group:{DOMAIN}|url"]


# --- limits ------------------------------------------------------------------------------------


async def test_case_insensitive_unique_names(world: World) -> None:
    owner, editor = world.clients["owner"], world.clients["editor"]
    first = await create(owner, world.case_id, name="Overview")
    assert first.status_code == 201
    clash = await create(editor, world.case_id, name="  OVERVIEW ")
    assert clash.status_code == 409
    assert clash.json() == {"detail": views_router.NAME_TAKEN}

    second = (await create(owner, world.case_id, name="Hosts")).json()["id"]
    assert (await patch(owner, world.case_id, second, {"name": "overview"})).status_code == 409
    # A view may change the case of its own name.
    renamed = await patch(owner, world.case_id, first.json()["id"], {"name": "OVERVIEW"})
    assert renamed.status_code == 200
    # Another case may use the same name.
    other = (await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))).json()["id"]
    assert (await create(owner, other, name="Overview")).status_code == 201


async def test_at_most_fifty_views_per_case(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    # Inserted directly: saving 50 through the API would hit the write limit.
    async with infra.sessionmaker() as db:
        for index in range(MAX_VIEWS_PER_CASE - 1):
            await db.execute(
                text(
                    "INSERT INTO saved_views (id, case_id, name, state) "
                    "VALUES (:id, :case, :name, '{}'::jsonb)"
                ),
                {"id": uuid.uuid4(), "case": world.case_id, "name": f"View {index}"},
            )
        await db.commit()
    assert (await create(owner, world.case_id, name="Last one")).status_code == 201
    refused = await create(owner, world.case_id, name="One too many")
    assert refused.status_code == 409
    assert refused.json() == {"detail": views_router.TOO_MANY_VIEWS}
    assert "50" in refused.json()["detail"]


async def test_at_most_ten_thousand_positions(world: World) -> None:
    owner = world.clients["owner"]
    keys = [f"ip_address:10.0.{i // 256}.{i % 256}" for i in range(10_001)]
    too_many = {key: [0, 0] for key in keys}
    response = await create(owner, world.case_id, body_state=state(positions=too_many))
    assert response.status_code == 422
    assert "10,000" in response.text
    just_enough = dict(list(too_many.items())[:10_000])
    assert (
        await create(owner, world.case_id, body_state=state(positions=just_enough))
    ).status_code == 201


async def test_at_most_five_hundred_expanded_groups(world: World) -> None:
    owner = world.clients["owner"]
    groups = [f"group:ip_address:10.1.{i // 256}.{i % 256}|url" for i in range(501)]
    too_many = state(groups={"expanded": groups})
    assert (await create(owner, world.case_id, body_state=too_many)).status_code == 422
    enough = state(groups={"expanded": groups[:MAX_EXPANDED_GROUPS]})
    assert (await create(owner, world.case_id, body_state=enough)).status_code == 201


async def test_bodies_over_one_mebibyte_get_413(world: World) -> None:
    owner = world.clients["owner"]
    padding = b" " * (MAX_BODY_BYTES + 1)
    body = json.dumps({"name": "x", "state": state()}).encode() + padding
    response = await owner.post(
        views_url(world.case_id),
        content=body,
        headers={**csrf(owner), "content-type": "application/json"},
    )
    assert response.status_code == 413
    view_id = (await create(owner, world.case_id)).json()["id"]

    async def chunks() -> Any:
        yield b'{"name": "y"}'
        for _ in range(MAX_BODY_BYTES // 65536 + 1):
            yield b" " * 65536

    streamed = await owner.patch(
        views_url(world.case_id, f"/{view_id}"),
        content=chunks(),
        headers={**csrf(owner), "content-type": "application/json"},
    )
    assert streamed.status_code == 413


async def test_writes_are_rate_limited_per_user(world: World) -> None:
    owner, editor = world.clients["owner"], world.clients["editor"]
    view_id = (await create(owner, world.case_id)).json()["id"]
    for index in range(views_router.MAX_WRITES - 1):
        renamed = await patch(owner, world.case_id, view_id, {"name": f"Name {index}"})
        assert renamed.status_code == 200, renamed.text
    refused = await patch(owner, world.case_id, view_id, {"name": "One more"})
    assert refused.status_code == 429
    assert 0 < int(refused.headers["retry-after"]) <= 60
    assert refused.json() == {"detail": views_router.TOO_MANY_WRITES}
    assert (await delete(owner, world.case_id, view_id)).status_code == 429
    # Reading is never limited, and other users have their own budget.
    assert (await owner.get(views_url(world.case_id))).status_code == 200
    assert (await create(editor, world.case_id, name="Editor's")).status_code == 201


# --- audit ---------------------------------------------------------------------------------------


async def test_writes_are_audited_without_name_or_state(world: World, infra: Infra) -> None:
    await seed(world)
    owner = world.clients["owner"]
    mark = await last_event_id(infra)
    secret_name = "Alice Example's accounts"
    view_id = (await create(owner, world.case_id, name=secret_name)).json()["id"]
    assert (await patch(owner, world.case_id, view_id, {"name": "Bob"})).status_code == 200
    assert (await delete(owner, world.case_id, view_id)).status_code == 204

    events = await events_since(infra, mark)
    assert [event["action"] for event in events] == ["view.create", "view.update", "view.delete"]
    for event in events:
        assert event["outcome"] == "success"
        assert event["target_type"] == "view"
        assert event["target_id"] == view_id
        assert event["case_id"] == world.case_id
        assert event["details"] == {}
    dumped = json.dumps(events, default=str)
    for leaked in ("Alice", "Bob", DOMAIN, "camera", "positions"):
        assert leaked not in dumped
    # The events extend the hash chain like any other.
    async with infra.sessionmaker() as db:
        assert (await verify_chain(db)).ok


async def test_refused_writes_record_no_view_event(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    assert (await create(owner, world.case_id)).status_code == 201
    mark = await last_event_id(infra)
    assert (await create(owner, world.case_id)).status_code == 409  # duplicate
    assert (await create(owner, world.case_id, name="")).status_code == 422
    assert await events_since(infra, mark) == []


# --- lifecycle ---------------------------------------------------------------------------


async def test_stale_positions_are_dropped_on_read(world: World) -> None:
    await seed(world)
    owner = world.clients["owner"]
    positions = {DOMAIN: [1, 2], WWW: [3, 4], "domain:never-added.example": [5, 6]}
    view_id = (await create(owner, world.case_id, body_state=state(positions=positions))).json()[
        "id"
    ]
    deleted = await owner.delete(
        f"{PREFIX}/cases/{world.case_id}/entities/" + quote(WWW, safe=""), headers=csrf(owner)
    )
    assert deleted.status_code == 204

    read = (await owner.get(views_url(world.case_id, f"/{view_id}"))).json()
    assert read["state"]["positions"] == {DOMAIN: [1.0, 2.0]}
    # The rest of the state is untouched.
    assert read["state"]["camera"] == state()["camera"]


class BrokenGraph:
    """A graph repository whose reads fail like an unreachable Neo4j."""

    async def existing_keys(self, case_id: object, keys: object) -> set[str]:
        raise DriverError("unavailable")


async def test_reading_a_view_needs_the_graph_database(world: World, db_app: FastAPI) -> None:
    owner = world.clients["owner"]
    with_pins = (await create(owner, world.case_id)).json()["id"]
    without = (
        await create(owner, world.case_id, name="Bare", body_state=state(positions={}))
    ).json()["id"]
    db_app.dependency_overrides[get_graph_repository] = BrokenGraph
    try:
        failed = await owner.get(views_url(world.case_id, f"/{with_pins}"))
        bare = await owner.get(views_url(world.case_id, f"/{without}"))
        listed = await owner.get(views_url(world.case_id))
    finally:
        del db_app.dependency_overrides[get_graph_repository]
    assert failed.status_code == 503
    # No positions, no lookup; the list never needs the graph.
    assert bare.status_code == 200
    assert listed.status_code == 200


async def test_deleted_with_the_case(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    assert (await create(owner, world.case_id)).status_code == 201
    other = (await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))).json()["id"]
    assert (await create(owner, other)).status_code == 201

    deleted = await owner.request(
        "DELETE",
        f"{PREFIX}/cases/{world.case_id}",
        json={"confirm_title": CASE["title"]},
        headers=csrf(owner),
    )
    assert deleted.status_code == 204, deleted.text
    async with infra.sessionmaker() as db:
        rows = (await db.execute(text("SELECT case_id::text FROM saved_views"))).scalars().all()
    assert rows == [other]


async def test_authors_are_cleared_when_the_user_is_deleted(
    world: World, infra: Infra, make_user: UserFactory
) -> None:
    # A user who never acted (so has no audit trail) can be deleted.
    quiet = await make_user("quiet@example.org", Role.ANALYST, display_name="Quinn Quiet")
    view_id = uuid.uuid4()
    async with infra.sessionmaker() as db:
        await db.execute(
            text(
                "INSERT INTO saved_views (id, case_id, name, state, created_by, updated_by) "
                "VALUES (:id, :case, 'Quiet', CAST(:state AS jsonb), :user, :user)"
            ),
            {
                "id": view_id,
                "case": world.case_id,
                "state": json.dumps(state(positions={})),
                "user": quiet.id,
            },
        )
        await db.commit()
    owner = world.clients["owner"]
    listed = (await owner.get(views_url(world.case_id))).json()
    assert listed[0]["created_by"]["display_name"] == "Quinn Quiet"

    async with infra.sessionmaker() as db:
        await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": quiet.id})
        await db.commit()
    read = (await owner.get(views_url(world.case_id, f"/{view_id}"))).json()
    assert read["created_by"] is None
    assert read["updated_by"] is None
    assert read["name"] == "Quiet"
    # Only the creator or an owner may change it, so now only owners can.
    renamed = await patch(owner, world.case_id, str(view_id), {"name": "Kept"})
    assert renamed.status_code == 200
    assert renamed.json()["updated_by"]["display_name"] == "Olive Owner"
    assert renamed.json()["created_by"] is None
