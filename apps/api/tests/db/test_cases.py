"""Cases and case membership against real services: validation, access, audit."""

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text

from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth.models import Role, User
from nightloom_api.cases.models import ETHICS_VERSION, Case, CaseMember
from nightloom_api.infra import Infra
from nightloom_api.request_id import REQUEST_ID_HEADER
from support import PREFIX, csrf, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]

PURPOSE = "Map the public attack surface of example.org for its owner."
DESCRIPTION = "Scope notes: only the example.org zone and its certificates."
VALID_CASE: dict[str, Any] = {
    "title": "Example exposure review",
    "description": DESCRIPTION,
    "purpose": PURPOSE,
    "legal_basis": "owned_assets",
    "ethics_acknowledged": True,
}
PERSON_REASON = "The owner asked us to check what their staff expose publicly."
CHANGE_REASON = "Scope extended by the client."


# --- helpers ------------------------------------------------------------------------


async def all_events(infra: Infra) -> list[AuditEvent]:
    async with infra.sessionmaker() as db:
        return list((await db.execute(select(AuditEvent).order_by(AuditEvent.id))).scalars())


async def last_event_id(infra: Infra) -> int:
    async with infra.sessionmaker() as db:
        return int(await db.scalar(text("SELECT coalesce(max(id), 0) FROM audit_events")) or 0)


async def events_since(infra: Infra, mark: int) -> list[AuditEvent]:
    return [event for event in await all_events(infra) if event.id > mark]


async def only_event_since(infra: Infra, mark: int) -> AuditEvent:
    events = await events_since(infra, mark)
    assert len(events) == 1, [(event.action, event.outcome, event.details) for event in events]
    return events[0]


async def signed_in(new_client: ClientFactory, user: User) -> AsyncClient:
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    return client


async def create_case(client: AsyncClient, **overrides: Any) -> Response:
    return await client.post(
        f"{PREFIX}/cases", json={**VALID_CASE, **overrides}, headers=csrf(client)
    )


async def patch_case(client: AsyncClient, case_id: object, **body: Any) -> Response:
    return await client.patch(f"{PREFIX}/cases/{case_id}", json=body, headers=csrf(client))


async def put_member(client: AsyncClient, case_id: object, email: str, role: str) -> Response:
    return await client.put(
        f"{PREFIX}/cases/{case_id}/members",
        json={"email": email, "role": role},
        headers=csrf(client),
    )


async def remove_member(client: AsyncClient, case_id: object, user_id: object) -> Response:
    return await client.delete(f"{PREFIX}/cases/{case_id}/members/{user_id}", headers=csrf(client))


async def delete_case(client: AsyncClient, case_id: object, confirm_title: str) -> Response:
    return await client.request(
        "DELETE",
        f"{PREFIX}/cases/{case_id}",
        json={"confirm_title": confirm_title},
        headers=csrf(client),
    )


@dataclass
class World:
    """One case with an owner, an editor and a viewer, plus an admin who is not a member."""

    case_id: str
    owner: User
    editor: User
    viewer: User
    admin: User
    clients: dict[str, AsyncClient]


@pytest.fixture
async def world(make_user: UserFactory, new_client: ClientFactory) -> World:
    admin = await make_user("admin@example.org", Role.ADMIN)
    owner = await make_user("owner@example.org", Role.ANALYST)
    editor = await make_user("editor@example.org", Role.ANALYST)
    viewer = await make_user("viewer@example.org", Role.VIEWER)
    clients = {
        "owner": await signed_in(new_client, owner),
        "editor": await signed_in(new_client, editor),
        "viewer": await signed_in(new_client, viewer),
        "admin": await signed_in(new_client, admin),
    }
    created = await create_case(clients["owner"])
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    assert (await put_member(clients["owner"], case_id, editor.email, "editor")).status_code == 201
    assert (await put_member(clients["owner"], case_id, viewer.email, "viewer")).status_code == 201
    return World(case_id, owner, editor, viewer, admin, clients)


# --- creation and validation --------------------------------------------------------


async def test_create_case(make_user: UserFactory, new_client: ClientFactory, infra: Infra) -> None:
    analyst = await make_user("analyst@example.org", Role.ANALYST)
    client = await signed_in(new_client, analyst)
    mark = await last_event_id(infra)

    response = await create_case(client, retention_days=90)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == VALID_CASE["title"]
    assert body["purpose"] == PURPOSE
    assert body["description"] == DESCRIPTION
    assert body["legal_basis"] == "owned_assets"
    assert body["legal_basis_note"] is None
    assert body["ethics_ack_version"] == ETHICS_VERSION == "2026-11"
    assert body["ethics_ack_at"]
    assert body["allow_person_entities"] is False
    assert body["status"] == "open"
    assert body["retention_days"] == 90
    assert body["created_by"] == str(analyst.id)
    assert body["closed_at"] is None
    assert body["my_role"] == "owner"

    async with infra.sessionmaker() as db:
        members = (await db.execute(select(CaseMember))).scalars().all()
    assert [(m.user_id, m.role.value) for m in members] == [(analyst.id, "owner")]

    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome) == ("case.create", "success")
    assert event.actor_user_id == analyst.id
    assert event.actor_label == analyst.email
    assert event.request_id == response.headers[REQUEST_ID_HEADER]
    assert (event.target_type, event.target_id) == ("case", body["id"])
    assert event.case_id == uuid.UUID(body["id"])
    assert event.details == {
        "legal_basis": "owned_assets",
        "allow_person_entities": False,
        "retention_days": 90,
        "ethics_ack_version": "2026-11",
    }


async def test_person_entities_reason_is_kept_only_in_the_audit_event(
    make_user: UserFactory, new_client: ClientFactory, infra: Infra
) -> None:
    client = await signed_in(new_client, await make_user("a@example.org", Role.ANALYST))
    mark = await last_event_id(infra)

    response = await create_case(
        client, allow_person_entities=True, person_entities_reason=PERSON_REASON
    )

    assert response.status_code == 201, response.text
    assert response.json()["allow_person_entities"] is True
    assert PERSON_REASON not in response.text
    event = await only_event_since(infra, mark)
    assert event.details["person_entities_reason"] == PERSON_REASON
    assert event.details["allow_person_entities"] is True


@pytest.mark.parametrize(
    ("legal_basis", "note"),
    [
        ("authorized_engagement", "Engagement letter 2026-17 with Example Ltd."),
        ("other", "Court order 12/2026."),
        ("journalism", None),
        ("threat_intelligence", "Optional context."),
    ],
)
async def test_legal_bases_with_and_without_note(
    make_user: UserFactory, new_client: ClientFactory, legal_basis: str, note: str | None
) -> None:
    client = await signed_in(new_client, await make_user("a@example.org", Role.ANALYST))

    response = await create_case(client, legal_basis=legal_basis, legal_basis_note=note)

    assert response.status_code == 201, response.text
    assert response.json()["legal_basis_note"] == note


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"title": "ab"}, id="title-too-short"),
        pytest.param({"title": "   ab   "}, id="title-short-after-strip"),
        pytest.param({"title": "t" * 201}, id="title-too-long"),
        pytest.param({"description": "d" * 5001}, id="description-too-long"),
        pytest.param({"purpose": "p" * 29}, id="purpose-too-short"),
        pytest.param({"purpose": "p" * 2001}, id="purpose-too-long"),
        pytest.param({"purpose": None}, id="purpose-null"),
        pytest.param({"legal_basis": "because"}, id="unknown-legal-basis"),
        pytest.param({"legal_basis": "authorized_engagement"}, id="engagement-without-note"),
        pytest.param({"legal_basis": "other", "legal_basis_note": "   "}, id="other-blank-note"),
        pytest.param({"legal_basis_note": "n" * 1001}, id="note-too-long"),
        pytest.param({"ethics_acknowledged": False}, id="ethics-false"),
        pytest.param({"allow_person_entities": True}, id="person-entities-without-reason"),
        pytest.param(
            {"allow_person_entities": True, "person_entities_reason": "too short"},
            id="person-entities-reason-too-short",
        ),
        pytest.param({"retention_days": 0}, id="retention-zero"),
        pytest.param({"retention_days": 3651}, id="retention-too-long"),
        pytest.param({"status": "closed"}, id="unknown-field"),
    ],
)
async def test_create_validation(
    make_user: UserFactory, new_client: ClientFactory, infra: Infra, overrides: dict[str, Any]
) -> None:
    client = await signed_in(new_client, await make_user("a@example.org", Role.ANALYST))
    mark = await last_event_id(infra)

    response = await create_case(client, **overrides)

    assert response.status_code == 422, response.text
    assert await events_since(infra, mark) == []
    async with infra.sessionmaker() as db:
        assert (await db.execute(select(Case))).first() is None


async def test_create_needs_the_ethics_acknowledgement_field(
    make_user: UserFactory, new_client: ClientFactory
) -> None:
    client = await signed_in(new_client, await make_user("a@example.org", Role.ANALYST))
    body = {key: value for key, value in VALID_CASE.items() if key != "ethics_acknowledged"}

    response = await client.post(f"{PREFIX}/cases", json=body, headers=csrf(client))

    assert response.status_code == 422


async def test_ethics_error_names_the_policy(
    make_user: UserFactory, new_client: ClientFactory
) -> None:
    client = await signed_in(new_client, await make_user("a@example.org", Role.ANALYST))

    response = await create_case(client, ethics_acknowledged=False)

    assert "ETHICS.md" in response.json()["detail"][0]["msg"]


async def test_global_viewer_cannot_create_and_is_audited(
    make_user: UserFactory, new_client: ClientFactory, infra: Infra
) -> None:
    viewer = await make_user("viewer@example.org", Role.VIEWER)
    client = await signed_in(new_client, viewer)
    mark = await last_event_id(infra)

    response = await create_case(client)

    assert response.status_code == 403
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome, event.actor_user_id) == (
        "authz.denied",
        "denied",
        viewer.id,
    )
    assert event.details["required_roles"] == ["admin", "analyst"]


async def test_admin_can_create_and_becomes_owner(
    make_user: UserFactory, new_client: ClientFactory
) -> None:
    client = await signed_in(new_client, await make_user("admin@example.org", Role.ADMIN))

    response = await create_case(client)

    assert response.status_code == 201
    assert response.json()["my_role"] == "owner"


async def test_cases_need_a_session(db_client: AsyncClient) -> None:
    assert (await db_client.get(f"{PREFIX}/cases")).status_code == 401
    assert (await db_client.get(f"{PREFIX}/cases/{uuid.uuid4()}")).status_code == 401


# --- the role matrix ----------------------------------------------------------------

OK, FORBIDDEN, HIDDEN = "ok", 403, 404


async def call(world: World, who: str, operation: str) -> Response:
    """Run one operation on the world's case as ``who``."""
    client = world.clients[who]
    case_id = world.case_id
    match operation:
        case "get":
            return await client.get(f"{PREFIX}/cases/{case_id}")
        case "patch_title":
            return await patch_case(client, case_id, title="A new title")
        case "patch_description":
            return await patch_case(client, case_id, description="New notes.")
        case "patch_purpose":
            return await patch_case(
                client, case_id, purpose=PURPOSE + " Updated.", change_reason=CHANGE_REASON
            )
        case "patch_status":
            return await patch_case(client, case_id, status="closed")
        case "patch_retention":
            return await patch_case(client, case_id, retention_days=30)
        case "members":
            return await client.get(f"{PREFIX}/cases/{case_id}/members")
        case "put_member":
            return await put_member(client, case_id, world.admin.email, "viewer")
        case "remove_other":
            target = world.viewer.id if who != "viewer" else world.editor.id
            return await remove_member(client, case_id, target)
        case "audit":
            return await client.get(f"{PREFIX}/cases/{case_id}/audit")
        case "delete":
            return await delete_case(client, case_id, VALID_CASE["title"])
    raise AssertionError(operation)


MATRIX: dict[str, dict[str, object]] = {
    #                  viewer      editor      owner  admin (non-member)
    "get": {"viewer": OK, "editor": OK, "owner": OK, "admin": HIDDEN},
    "patch_title": {"viewer": FORBIDDEN, "editor": OK, "owner": OK, "admin": HIDDEN},
    "patch_description": {"viewer": FORBIDDEN, "editor": OK, "owner": OK, "admin": HIDDEN},
    "patch_purpose": {"viewer": FORBIDDEN, "editor": FORBIDDEN, "owner": OK, "admin": HIDDEN},
    "patch_status": {"viewer": FORBIDDEN, "editor": FORBIDDEN, "owner": OK, "admin": HIDDEN},
    "patch_retention": {"viewer": FORBIDDEN, "editor": FORBIDDEN, "owner": OK, "admin": HIDDEN},
    "members": {"viewer": OK, "editor": OK, "owner": OK, "admin": HIDDEN},
    "put_member": {"viewer": FORBIDDEN, "editor": FORBIDDEN, "owner": OK, "admin": HIDDEN},
    "remove_other": {"viewer": FORBIDDEN, "editor": FORBIDDEN, "owner": OK, "admin": HIDDEN},
    "audit": {"viewer": FORBIDDEN, "editor": OK, "owner": OK, "admin": HIDDEN},
    "delete": {"viewer": FORBIDDEN, "editor": FORBIDDEN, "owner": OK, "admin": HIDDEN},
}


@pytest.mark.parametrize(
    ("operation", "who"),
    [(operation, who) for operation, row in MATRIX.items() for who in row],
)
async def test_role_matrix(world: World, infra: Infra, operation: str, who: str) -> None:
    expected = MATRIX[operation][who]
    mark = await last_event_id(infra)

    response = await call(world, who, operation)

    if expected == OK:
        assert response.status_code in {200, 201, 204}, response.text
        return
    assert response.status_code == expected, response.text
    actor = getattr(world, who)
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome) == ("authz.denied", "denied")
    assert event.actor_user_id == actor.id
    assert event.case_id == uuid.UUID(world.case_id)
    assert event.request_id == response.headers[REQUEST_ID_HEADER]
    if expected == HIDDEN:
        assert response.json() == {"detail": "Case not found."}
        assert event.details["reason"] == "not_a_member"
        assert "case_role" not in event.details
    else:
        assert event.details["reason"] == "missing_capability"
        assert event.details["case_role"] == who
        assert event.details["required"] in {"edit", "manage", "read_audit"}
    assert event.details["path"].startswith("/api/v1/cases/{case_id}")


async def test_unknown_case_looks_like_a_hidden_one(world: World, infra: Infra) -> None:
    missing = uuid.uuid4()
    mark = await last_event_id(infra)

    hidden = await world.clients["admin"].get(f"{PREFIX}/cases/{world.case_id}")
    unknown = await world.clients["admin"].get(f"{PREFIX}/cases/{missing}")

    assert (hidden.status_code, hidden.json()) == (unknown.status_code, unknown.json())
    events = await events_since(infra, mark)
    assert [event.case_id for event in events] == [uuid.UUID(world.case_id), missing]
    assert {event.details["reason"] for event in events} == {"not_a_member"}


async def test_invalid_case_id_is_a_validation_error(world: World) -> None:
    assert (await world.clients["owner"].get(f"{PREFIX}/cases/not-a-uuid")).status_code == 422


async def test_global_viewer_can_only_be_a_case_viewer(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)

    for role in ("editor", "owner"):
        response = await put_member(owner, world.case_id, world.viewer.email, role)
        assert response.status_code == 422
        assert "global viewer role" in response.json()["detail"]
    assert await events_since(infra, mark) == []


async def test_global_demotion_caps_the_case_role(world: World, infra: Infra) -> None:
    # The editor becomes a co-owner, then an admin demotes them to a global viewer.
    owner, admin = world.clients["owner"], world.clients["admin"]
    assert (await put_member(owner, world.case_id, world.editor.email, "owner")).status_code == 200
    demoted = await admin.patch(
        f"{PREFIX}/users/{world.editor.id}", json={"role": "viewer"}, headers=csrf(admin)
    )
    assert demoted.status_code == 200

    editor = world.clients["editor"]
    assert (await editor.get(f"{PREFIX}/cases/{world.case_id}")).json()["my_role"] == "viewer"
    assert (await patch_case(editor, world.case_id, title="Nope")).status_code == 403
    listed = (await editor.get(f"{PREFIX}/cases")).json()["items"]
    assert [item["my_role"] for item in listed] == ["viewer"]
    # A capped owner does not count as an owner: the real owner is still the last one.
    response = await remove_member(owner, world.case_id, world.owner.id)
    assert response.status_code == 409


# --- updates ------------------------------------------------------------------------


async def test_editor_updates_title_and_description(world: World, infra: Infra) -> None:
    mark = await last_event_id(infra)

    response = await patch_case(
        world.clients["editor"], world.case_id, title="Renamed case", description=None
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["title"], body["description"], body["my_role"]) == (
        "Renamed case",
        None,
        "editor",
    )
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome, event.actor_user_id) == (
        "case.update",
        "success",
        world.editor.id,
    )
    assert event.details == {"fields": ["description", "title"]}
    assert (event.target_type, event.target_id, event.case_id) == (
        "case",
        world.case_id,
        uuid.UUID(world.case_id),
    )


async def test_reason_fields_need_a_change_reason(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)
    new_purpose = PURPOSE + " Also its mail servers."

    for body in (
        {"purpose": new_purpose},
        {"legal_basis": "threat_intelligence"},
        {"legal_basis_note": "A note"},
        {"allow_person_entities": True},
    ):
        response = await patch_case(owner, world.case_id, **body)
        assert response.status_code == 422, body
        assert "change_reason" in str(response.json()["detail"])
    short = await patch_case(owner, world.case_id, purpose=new_purpose, change_reason="short")
    assert short.status_code == 422
    assert await events_since(infra, mark) == []

    response = await patch_case(
        owner, world.case_id, purpose=new_purpose, change_reason=CHANGE_REASON
    )

    assert response.status_code == 200, response.text
    assert response.json()["purpose"] == new_purpose
    event = await only_event_since(infra, mark)
    assert event.details == {"fields": ["purpose"], "change_reason": CHANGE_REASON}


async def test_unchanged_values_need_no_reason_and_record_nothing(
    world: World, infra: Infra
) -> None:
    mark = await last_event_id(infra)

    response = await patch_case(
        world.clients["owner"],
        world.case_id,
        purpose=PURPOSE,
        legal_basis="owned_assets",
        title=VALID_CASE["title"],
    )

    assert response.status_code == 200
    assert await events_since(infra, mark) == []


async def test_change_reason_is_not_stored_for_plain_fields(world: World, infra: Infra) -> None:
    mark = await last_event_id(infra)

    response = await patch_case(
        world.clients["owner"], world.case_id, retention_days=None, change_reason=CHANGE_REASON
    )

    assert response.status_code == 200
    # retention_days was already null: nothing changed.
    assert await events_since(infra, mark) == []
    response = await patch_case(
        world.clients["owner"], world.case_id, retention_days=7, change_reason=CHANGE_REASON
    )
    assert (await only_event_since(infra, mark)).details == {"fields": ["retention_days"]}


async def test_legal_basis_switch_needs_a_note(world: World) -> None:
    owner = world.clients["owner"]

    missing = await patch_case(
        owner, world.case_id, legal_basis="other", change_reason=CHANGE_REASON
    )
    with_note = await patch_case(
        owner,
        world.case_id,
        legal_basis="authorized_engagement",
        legal_basis_note="Engagement letter 2026-17.",
        change_reason=CHANGE_REASON,
    )
    clear_note = await patch_case(
        owner, world.case_id, legal_basis_note=None, change_reason=CHANGE_REASON
    )

    assert missing.status_code == 422
    assert "legal_basis_note is required" in missing.json()["detail"]
    assert with_note.status_code == 200
    assert with_note.json()["legal_basis"] == "authorized_engagement"
    assert clear_note.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"title": None},
        {"purpose": None},
        {"legal_basis": None},
        {"status": None},
        {"allow_person_entities": None},
        {"title": "ab"},
        {"purpose": "too short"},
        {"retention_days": 0},
        {"status": "deleted"},
        {"created_by": str(uuid.uuid4())},
        {"ethics_ack_version": "1999-01"},
    ],
)
async def test_patch_validation(world: World, body: dict[str, Any]) -> None:
    response = await patch_case(world.clients["owner"], world.case_id, **body)

    assert response.status_code == 422, response.text


async def test_status_changes(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)

    closed = (await patch_case(owner, world.case_id, status="closed")).json()
    archived = (await patch_case(owner, world.case_id, status="archived")).json()
    reopened = (await patch_case(owner, world.case_id, status="open")).json()

    assert closed["status"] == "closed"
    assert closed["closed_at"] is not None
    assert archived["closed_at"] == closed["closed_at"]
    assert (reopened["status"], reopened["closed_at"]) == ("open", None)
    events = await events_since(infra, mark)
    assert [(e.action, e.details) for e in events] == [
        ("case.status_change", {"from": "open", "to": "closed"}),
        ("case.status_change", {"from": "closed", "to": "archived"}),
        ("case.status_change", {"from": "archived", "to": "open"}),
    ]


async def test_status_and_fields_in_one_patch_record_two_events(world: World, infra: Infra) -> None:
    mark = await last_event_id(infra)

    response = await patch_case(
        world.clients["owner"], world.case_id, status="closed", title="Closed review"
    )

    assert response.status_code == 200
    assert [(e.action, e.details) for e in await events_since(infra, mark)] == [
        ("case.update", {"fields": ["title"]}),
        ("case.status_change", {"from": "open", "to": "closed"}),
    ]


async def test_update_bumps_updated_at(world: World) -> None:
    owner = world.clients["owner"]
    before = (await owner.get(f"{PREFIX}/cases/{world.case_id}")).json()["updated_at"]

    after = (await patch_case(owner, world.case_id, title="Bumped title")).json()["updated_at"]

    assert after > before


# --- deletion -----------------------------------------------------------------------


async def test_delete_needs_the_exact_title(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)

    for wrong in (VALID_CASE["title"].upper(), VALID_CASE["title"] + " ", ""):
        response = await delete_case(owner, world.case_id, wrong)
        assert response.status_code == 422
        assert response.json() == {"detail": "confirm_title does not match the case title."}

    events = await events_since(infra, mark)
    assert [(e.action, e.outcome, e.details) for e in events] == [
        ("case.delete", "failure", {"reason": "confirm_title_mismatch"})
    ] * 3
    assert (await owner.get(f"{PREFIX}/cases/{world.case_id}")).status_code == 200


async def test_delete_needs_a_body(world: World) -> None:
    owner = world.clients["owner"]
    response = await owner.delete(f"{PREFIX}/cases/{world.case_id}", headers=csrf(owner))
    assert response.status_code == 422


async def test_delete_keeps_the_audit_trail(world: World, infra: Infra) -> None:
    owner, admin = world.clients["owner"], world.clients["admin"]
    mark = await last_event_id(infra)

    response = await delete_case(owner, world.case_id, VALID_CASE["title"])

    assert response.status_code == 204
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome, event.actor_user_id) == (
        "case.delete",
        "success",
        world.owner.id,
    )
    assert event.details == {"member_count": 3}
    assert (event.target_type, event.target_id) == ("case", world.case_id)
    async with infra.sessionmaker() as db:
        assert await db.get(Case, uuid.UUID(world.case_id)) is None
        assert (await db.execute(select(CaseMember))).first() is None

    assert (await owner.get(f"{PREFIX}/cases/{world.case_id}")).status_code == 404
    history = await admin.get(f"{PREFIX}/audit", params={"case_id": world.case_id, "limit": 200})
    actions = [item["action"] for item in history.json()["items"]]
    assert actions[:2] == ["authz.denied", "case.delete"]  # the owner's GET came last
    assert {"case.create", "case.member_add"} <= set(actions)


# --- membership ---------------------------------------------------------------------


async def test_list_members(world: World) -> None:
    response = await world.clients["viewer"].get(f"{PREFIX}/cases/{world.case_id}/members")

    assert response.status_code == 200
    members = response.json()
    assert [(m["email"], m["role"]) for m in members] == [
        ("owner@example.org", "owner"),
        ("editor@example.org", "editor"),
        ("viewer@example.org", "viewer"),
    ]
    assert members[1]["added_by"] == str(world.owner.id)
    assert members[0]["added_by"] is None
    assert members[2]["global_role"] == "viewer"
    assert all(m["is_active"] for m in members)


async def test_member_add_and_update_are_recorded(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)

    added = await put_member(owner, world.case_id, " ADMIN@example.org ", "editor")
    same = await put_member(owner, world.case_id, world.admin.email, "editor")
    changed = await put_member(owner, world.case_id, world.admin.email, "owner")

    assert (added.status_code, same.status_code, changed.status_code) == (201, 200, 200)
    assert added.json()["role"] == "editor"
    assert changed.json()["role"] == "owner"
    events = await events_since(infra, mark)
    assert [(e.action, e.target_type, e.target_id, e.details) for e in events] == [
        ("case.member_add", "user", str(world.admin.id), {"role": "editor"}),
        ("case.member_update", "user", str(world.admin.id), {"from": "editor", "to": "owner"}),
    ]
    assert all(e.case_id == uuid.UUID(world.case_id) for e in events)
    # The admin is now a member and can see the case.
    assert (await world.clients["admin"].get(f"{PREFIX}/cases/{world.case_id}")).status_code == 200


@pytest.mark.parametrize("email", ["nobody@example.org", "gone@example.org"])
async def test_member_put_needs_an_active_user(
    world: World, make_user: UserFactory, infra: Infra, email: str
) -> None:
    await make_user("gone@example.org", Role.ANALYST, is_active=False)
    mark = await last_event_id(infra)

    response = await put_member(world.clients["owner"], world.case_id, email, "viewer")

    assert response.status_code == 404
    assert response.json() == {"detail": "No active user with that email."}
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome) == ("case.member_add", "failure")
    assert event.details == {"role": "viewer", "reason": "not_found"}
    assert email not in str(event.details)


async def test_member_put_validates_the_email(world: World) -> None:
    response = await put_member(world.clients["owner"], world.case_id, "not-an-email", "viewer")
    assert response.status_code == 422


async def test_remove_member(world: World, infra: Infra) -> None:
    mark = await last_event_id(infra)

    response = await remove_member(world.clients["owner"], world.case_id, world.editor.id)

    assert response.status_code == 204
    event = await only_event_since(infra, mark)
    assert (event.action, event.target_id, event.details) == (
        "case.member_remove",
        str(world.editor.id),
        {"role": "editor", "self": False},
    )
    gone = await world.clients["editor"].get(f"{PREFIX}/cases/{world.case_id}")
    assert gone.status_code == 404


@pytest.mark.parametrize("who", ["viewer", "editor"])
async def test_members_can_remove_themselves(world: World, infra: Infra, who: str) -> None:
    user = getattr(world, who)
    mark = await last_event_id(infra)

    response = await remove_member(world.clients[who], world.case_id, user.id)

    assert response.status_code == 204
    assert (await only_event_since(infra, mark)).details == {"role": who, "self": True}


async def test_removing_a_non_member(world: World, infra: Infra) -> None:
    mark = await last_event_id(infra)

    response = await remove_member(world.clients["owner"], world.case_id, world.admin.id)

    assert response.status_code == 404
    assert response.json() == {"detail": "That user is not a member of this case."}
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome, event.details) == (
        "case.member_remove",
        "failure",
        {"reason": "not_found"},
    )


# --- last-owner protection ----------------------------------------------------------


@pytest.mark.parametrize("how", ["demote", "remove_self", "remove_by_id"])
async def test_the_last_owner_stays(world: World, infra: Infra, how: str) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)

    if how == "demote":
        response = await put_member(owner, world.case_id, world.owner.email, "editor")
        action = "case.member_update"
    else:
        response = await remove_member(owner, world.case_id, world.owner.id)
        action = "case.member_remove"

    assert response.status_code == 409
    assert response.json() == {"detail": "A case must keep at least one owner."}
    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome, event.details["reason"]) == (
        action,
        "failure",
        "last_owner",
    )
    members = (await owner.get(f"{PREFIX}/cases/{world.case_id}/members")).json()
    assert members[0]["role"] == "owner"


async def test_an_owner_can_leave_once_there_is_another(world: World) -> None:
    owner = world.clients["owner"]
    assert (await put_member(owner, world.case_id, world.editor.email, "owner")).status_code == 200

    assert (await put_member(owner, world.case_id, world.owner.email, "viewer")).status_code == 200
    assert (await remove_member(owner, world.case_id, world.owner.id)).status_code == 204
    # The former owner is no longer a member at all.
    assert (await owner.get(f"{PREFIX}/cases/{world.case_id}")).status_code == 404
    # And the editor, now the only owner, cannot leave.
    editor = world.clients["editor"]
    assert (await remove_member(editor, world.case_id, world.editor.id)).status_code == 409


async def test_a_deactivated_co_owner_does_not_count(
    world: World, make_user: UserFactory, new_client: ClientFactory
) -> None:
    owner, admin = world.clients["owner"], world.clients["admin"]
    other = await make_user("other@example.org", Role.ANALYST)
    assert (await put_member(owner, world.case_id, other.email, "owner")).status_code == 201
    deactivated = await admin.patch(
        f"{PREFIX}/users/{other.id}", json={"is_active": False}, headers=csrf(admin)
    )
    assert deactivated.status_code == 200

    response = await remove_member(owner, world.case_id, world.owner.id)

    assert response.status_code == 409


async def test_two_owners_demoting_each_other_at_once(world: World) -> None:
    owner, editor = world.clients["owner"], world.clients["editor"]
    assert (await put_member(owner, world.case_id, world.editor.email, "owner")).status_code == 200

    responses = await asyncio.gather(
        put_member(owner, world.case_id, world.editor.email, "viewer"),
        put_member(editor, world.case_id, world.owner.email, "viewer"),
    )

    # Serialized by the case lock: one succeeds; the other is then either no
    # longer an owner (403) or would remove the last owner (409).
    codes = sorted(response.status_code for response in responses)
    assert codes[0] == 200
    assert codes[1] in {403, 409}
    members = (await owner.get(f"{PREFIX}/cases/{world.case_id}/members")).json()
    if not members or members[0]["role"] != "owner":
        members = (await editor.get(f"{PREFIX}/cases/{world.case_id}/members")).json()
    assert [m["role"] for m in members].count("owner") == 1


# --- listing ------------------------------------------------------------------------


async def test_list_shows_only_my_cases_with_my_role(world: World) -> None:
    other = await create_case(world.clients["admin"], title="Admin's own case")
    assert other.status_code == 201

    viewer_items = (await world.clients["viewer"].get(f"{PREFIX}/cases")).json()["items"]
    admin_items = (await world.clients["admin"].get(f"{PREFIX}/cases")).json()["items"]

    assert [(i["id"], i["my_role"]) for i in viewer_items] == [(world.case_id, "viewer")]
    assert [(i["title"], i["my_role"]) for i in admin_items] == [("Admin's own case", "owner")]


async def test_pagination_walks_every_case_newest_update_first(
    make_user: UserFactory, new_client: ClientFactory
) -> None:
    client = await signed_in(new_client, await make_user("a@example.org", Role.ANALYST))
    ids = [(await create_case(client, title=f"Case number {n}")).json()["id"] for n in range(5)]
    # Touch the oldest one, so it moves to the front.
    await patch_case(client, ids[0], title="Case number 0, touched")
    expected = [ids[0], ids[4], ids[3], ids[2], ids[1]]

    seen: list[str] = []
    params: dict[str, Any] = {"limit": 2}
    pages = 0
    while True:
        page = (await client.get(f"{PREFIX}/cases", params=params)).json()
        pages += 1
        seen.extend(item["id"] for item in page["items"])
        if page["next_cursor"] is None:
            break
        params["cursor"] = page["next_cursor"]

    assert seen == expected
    assert pages == 3


async def test_list_filters(make_user: UserFactory, new_client: ClientFactory) -> None:
    client = await signed_in(new_client, await make_user("a@example.org", Role.ANALYST))
    titles = ["100% coverage", "100x coverage", "snake_case", "snakeXcase", "back\\slash"]
    ids = {title: (await create_case(client, title=title)).json()["id"] for title in titles}
    await patch_case(client, ids["snakeXcase"], status="closed")

    async def search(**params: str) -> set[str]:
        response = await client.get(f"{PREFIX}/cases", params=params)
        assert response.status_code == 200, response.text
        return {item["title"] for item in response.json()["items"]}

    assert await search(q="100%") == {"100% coverage"}
    assert await search(q="e_c") == {"snake_case"}
    assert await search(q="%") == {"100% coverage"}
    assert await search(q="_") == {"snake_case"}
    assert await search(q="\\") == {"back\\slash"}
    assert await search(q="COVERAGE") == {"100% coverage", "100x coverage"}
    assert await search(status="closed") == {"snakeXcase"}
    assert await search(status="open", q="snake") == {"snake_case"}
    assert await search(status="archived") == set()


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"status": "deleted"},
        {"q": ""},
        {"q": "q" * 201},
        {"cursor": "not a cursor"},
        {"cursor": "bm90LWEtZGF0ZXxub3QtYS11dWlk"},  # "not-a-date|not-a-uuid"
    ],
)
async def test_list_validates_parameters(world: World, params: dict[str, Any]) -> None:
    response = await world.clients["owner"].get(f"{PREFIX}/cases", params=params)
    assert response.status_code == 422


# --- the case's audit view ----------------------------------------------------------


async def test_case_audit_shows_only_this_case(world: World) -> None:
    owner = world.clients["owner"]
    other = await create_case(owner, title="Another case")
    await patch_case(owner, world.case_id, title="Renamed")

    response = await world.clients["editor"].get(f"{PREFIX}/cases/{world.case_id}/audit")

    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["case_id"] for item in items} == {world.case_id}
    assert [item["action"] for item in items] == [
        "case.update",
        "case.member_add",
        "case.member_add",
        "case.create",
    ]
    assert "hash" not in items[0]
    assert other.json()["id"] not in response.text


async def test_case_audit_pages_like_the_admin_listing(world: World) -> None:
    editor = world.clients["editor"]
    seen: list[int] = []
    params: dict[str, Any] = {"limit": 1}
    while True:
        page = (await editor.get(f"{PREFIX}/cases/{world.case_id}/audit", params=params)).json()
        seen.extend(item["id"] for item in page["items"])
        if page["next_before_id"] is None:
            break
        params["before_id"] = page["next_before_id"]

    assert len(seen) == 3
    assert seen == sorted(seen, reverse=True)
    filtered = await editor.get(
        f"{PREFIX}/cases/{world.case_id}/audit", params={"action": "case.create"}
    )
    assert [item["action"] for item in filtered.json()["items"]] == ["case.create"]


async def test_audit_never_holds_case_text(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    new_purpose = "A completely different purpose, long enough to be valid."
    await patch_case(owner, world.case_id, description="Secret-ish scope notes.")
    await patch_case(owner, world.case_id, purpose=new_purpose, change_reason=CHANGE_REASON)
    await patch_case(owner, world.case_id, title="Another title")
    await delete_case(owner, world.case_id, "Another title")

    for event in await all_events(infra):
        text_of_details = str(event.details)
        for secret in (
            PURPOSE,
            new_purpose,
            DESCRIPTION,
            "Secret-ish",
            VALID_CASE["title"],
            "Another title",
        ):
            assert secret not in text_of_details, (event.action, event.details)
