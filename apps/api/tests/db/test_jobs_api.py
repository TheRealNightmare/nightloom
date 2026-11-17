"""Shuttles and jobs endpoints against PostgreSQL, Valkey and Neo4j (no real broker)."""

import hashlib
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select, text, update

from db_fixtures import RecordingJobQueue
from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth.models import Role, User
from nightloom_api.config import Settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra import Infra
from nightloom_api.infra import graph as graph_infra
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.jobs import service
from nightloom_api.jobs.models import Job, JobStatus
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import EntityInput, Finding, Mode, Shuttle, ShuttleContext, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_shuttle_registrable_domain import RegistrableDomain
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


def _meta(name: str, **overrides: Any) -> ShuttleMeta:
    values: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "title": f"Test {name}",
        "description": "A Shuttle used by the jobs API tests.",
        "input_types": {EntityType.DOMAIN},
        "output_entity_types": {EntityType.DOMAIN},
        "output_relationship_types": {RelationType.HAS_SUBDOMAIN},
        "sensitivity": Sensitivity.INFRASTRUCTURE,
        "network": False,
    }
    values.update(overrides)
    return ShuttleMeta.model_validate(values)


class _Nothing(Shuttle):
    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        return
        yield


class ActiveScan(_Nothing):
    meta = _meta("active-scan", mode=Mode.ACTIVE, timeout_seconds=120)


class StaffEmails(_Nothing):
    meta = _meta(
        "staff-emails",
        output_entity_types={EntityType.EMAIL},
        output_relationship_types={RelationType.HAS_EMAIL},
        sensitivity=Sensitivity.PERSON,
    )


class IPInfo(_Nothing):
    meta = _meta(
        "ip-info",
        input_types={EntityType.IP_ADDRESS},
        output_entity_types={EntityType.NETBLOCK},
        output_relationship_types={RelationType.WITHIN},
        network=True,
        rate_limit={"requests": 10, "period_seconds": 60},
        terms_url="https://example.org/terms",
    )


REGISTRY = ShuttleRegistry([RegistrableDomain, ActiveScan, StaffEmails, IPInfo])


@pytest.fixture(autouse=True)
def _registry(db_app: FastAPI) -> None:
    db_app.dependency_overrides[service.get_shuttle_registry] = lambda: REGISTRY


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


async def signed_in(new_client: ClientFactory, user: User) -> AsyncClient:
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    return client


def case_url(case_id: str, path: str = "") -> str:
    return f"{PREFIX}/cases/{case_id}{path}"


async def add_entity(client: AsyncClient, case_id: str, type_: str, value: str) -> str:
    response = await client.post(
        case_url(case_id, "/entities"), json={"type": type_, "value": value}, headers=csrf(client)
    )
    assert response.status_code == 201, response.text
    return str(response.json()["key"])


async def start(
    client: AsyncClient, case_id: str, shuttle: str = "registrable-domain", key: str = ""
) -> Response:
    return await client.post(
        case_url(case_id, "/jobs"),
        json={"shuttle": shuttle, "entity_key": key or "domain:www.shop.example.co.uk"},
        headers=csrf(client),
    )


@dataclass
class World:
    case_id: str
    clients: dict[str, AsyncClient]
    users: dict[str, User]


@pytest.fixture
async def world(make_user: UserFactory, new_client: ClientFactory) -> World:
    """An open case (no person entities) with a subdomain; owner, editor, viewer, outsider."""
    users = {
        "owner": await make_user("owner@example.org", Role.ANALYST),
        "editor": await make_user("editor@example.org", Role.ANALYST),
        "viewer": await make_user("viewer@example.org", Role.VIEWER),
        "admin": await make_user("admin@example.org", Role.ADMIN),
    }
    clients = {name: await signed_in(new_client, user) for name, user in users.items()}
    owner = clients["owner"]
    created = await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))
    assert created.status_code == 201
    case_id = str(created.json()["id"])
    for name in ("editor", "viewer"):
        added = await owner.put(
            case_url(case_id, "/members"),
            json={"email": users[name].email, "role": name},
            headers=csrf(owner),
        )
        assert added.status_code == 201
    await add_entity(owner, case_id, "domain", "www.shop.example.co.uk")
    return World(case_id, clients, users)


# --- GET /shuttles ------------------------------------------------------------------


async def test_shuttles_need_a_signed_in_user(db_client: AsyncClient) -> None:
    assert (await db_client.get(f"{PREFIX}/shuttles")).status_code == 401


async def test_shuttles_list_metadata(world: World) -> None:
    viewer = world.clients["viewer"]
    response = await viewer.get(f"{PREFIX}/shuttles")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names == ["active-scan", "ip-info", "registrable-domain", "staff-emails"]

    domain = await viewer.get(f"{PREFIX}/shuttles", params={"input_type": "domain"})
    assert [item["name"] for item in domain.json()] == [
        "active-scan",
        "registrable-domain",
        "staff-emails",
    ]
    item = domain.json()[1]
    assert item == {
        "name": "registrable-domain",
        "version": "1.0.0",
        "title": "Registrable domain",
        "description": RegistrableDomain.meta.description,
        "input_types": ["domain"],
        "output_entity_types": ["domain"],
        "output_relationship_types": ["HAS_SUBDOMAIN"],
        "mode": "passive",
        "sensitivity": "infrastructure",
        "network": False,
        "required_keys": [],
        "rate_limit": None,
        "timeout_seconds": 30,
        "terms_url": None,
        "homepage": "https://publicsuffix.org/",
        "default_admiralty": "B1",
        "default_confidence": 0.95,
    }
    ip = await viewer.get(f"{PREFIX}/shuttles", params={"input_type": "ip_address"})
    assert ip.json()[0]["rate_limit"] == {"requests": 10, "period_seconds": 60.0}
    assert ip.json()[0]["terms_url"] == "https://example.org/terms"
    bad = await viewer.get(f"{PREFIX}/shuttles", params={"input_type": "planet"})
    assert bad.status_code == 422


# --- POST /cases/{id}/jobs -----------------------------------------------------------


async def test_owner_and_editor_queue_jobs_after_commit(
    world: World, infra: Infra, job_queue: RecordingJobQueue
) -> None:
    mark = await last_event_id(infra)
    for role in ("owner", "editor"):
        response = await start(world.clients[role], world.case_id)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "queued"
        assert body["shuttle_name"] == "registrable-domain"
        assert body["shuttle_version"] == "1.0.0"
        assert body["input_key"] == "domain:www.shop.example.co.uk"
        assert body["requested_by"] == str(world.users[role].id)
        assert body["stats"]["findings_rejected"] == 0
        assert body["cancel_requested"] is False
        assert body["finished_at"] is None
        # Queued with the job's ID and the Shuttle's timeout, and the row exists.
        job_id = uuid.UUID(body["id"])
        assert job_queue.sent[-1] == (job_id, 30)
        async with infra.sessionmaker() as db:
            assert (await db.get(Job, job_id)) is not None

    events = await events_since(infra, mark)
    assert [(e.action, e.outcome) for e in events] == [("job.create", "success")] * 2
    event = events[0]
    assert event.target_type == "job"
    assert str(event.case_id) == world.case_id
    assert event.details["shuttle"] == "registrable-domain"
    assert event.details["version"] == "1.0.0"
    assert event.details["entity_type"] == "domain"
    assert event.details["input_key"] == "domain:www.shop.example.co.uk"
    assert event.details["mode"] == "passive"


async def test_viewers_and_outsiders_cannot_start_jobs(world: World, infra: Infra) -> None:
    mark = await last_event_id(infra)
    viewer = await start(world.clients["viewer"], world.case_id)
    outsider = await start(world.clients["admin"], world.case_id)
    assert viewer.status_code == 403
    assert outsider.status_code == 404
    events = await events_since(infra, mark)
    assert [(e.action, e.details["reason"]) for e in events] == [
        ("authz.denied", "missing_capability"),
        ("authz.denied", "not_a_member"),
    ]
    assert events[0].details["required"] == "run_shuttles"


@pytest.mark.parametrize(
    ("shuttle", "key", "status_code", "message"),
    [
        ("no-such-shuttle", "", 422, "Unknown Shuttle."),
        ("registrable-domain", "domain:Example.ORG", 422, "entity_key is not a canonical"),
        ("registrable-domain", "domain:missing.example.org", 422, "does not name an entity"),
        ("ip-info", "", 422, "does not accept domain entities"),
    ],
)
async def test_bad_job_requests(
    world: World,
    job_queue: RecordingJobQueue,
    shuttle: str,
    key: str,
    status_code: int,
    message: str,
) -> None:
    response = await start(world.clients["editor"], world.case_id, shuttle, key)
    assert response.status_code == status_code
    assert message in response.json()["detail"]
    assert job_queue.sent == []


async def test_body_is_validated(world: World) -> None:
    editor = world.clients["editor"]
    for body in ({"shuttle": "registrable-domain"}, {"shuttle": "", "entity_key": "domain:a.b"}):
        response = await editor.post(
            case_url(world.case_id, "/jobs"), json=body, headers=csrf(editor)
        )
        assert response.status_code == 422


async def test_active_shuttles_are_refused_and_audited(world: World, infra: Infra) -> None:
    mark = await last_event_id(infra)
    response = await start(world.clients["owner"], world.case_id, "active-scan")
    assert response.status_code == 403
    assert "ownership verification" in response.json()["detail"]
    (event,) = await events_since(infra, mark)
    assert (event.action, event.outcome) == ("authz.denied", "denied")
    assert event.details["reason"] == "active_shuttle"
    assert event.details["shuttle"] == "active-scan"


async def test_person_shuttles_need_a_person_case(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    mark = await last_event_id(infra)
    refused = await start(owner, world.case_id, "staff-emails")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "This case does not allow person-related entities."
    (event,) = await events_since(infra, mark)
    assert event.details["reason"] == "person_entities_disabled"

    created = await owner.post(
        f"{PREFIX}/cases",
        json={**CASE, "allow_person_entities": True, "person_entities_reason": PERSON_REASON},
        headers=csrf(owner),
    )
    person_case = str(created.json()["id"])
    await add_entity(owner, person_case, "domain", "www.shop.example.co.uk")
    assert (await start(owner, person_case, "staff-emails")).status_code == 201


async def test_person_input_keys_are_hashed_in_the_audit_log(
    world: World, infra: Infra, make_user: UserFactory, new_client: ClientFactory
) -> None:
    class EmailDomain(_Nothing):
        meta = _meta(
            "email-domain",
            input_types={EntityType.EMAIL},
            output_relationship_types=set(),
            sensitivity=Sensitivity.PERSON,
        )

    REGISTRY.add(EmailDomain)
    try:
        owner = world.clients["owner"]
        created = await owner.post(
            f"{PREFIX}/cases",
            json={**CASE, "allow_person_entities": True, "person_entities_reason": PERSON_REASON},
            headers=csrf(owner),
        )
        case_id = str(created.json()["id"])
        key = await add_entity(owner, case_id, "email", "alex@example.org")
        mark = await last_event_id(infra)
        assert (await start(owner, case_id, "email-domain", key)).status_code == 201
        (event,) = await events_since(infra, mark)
        assert "input_key" not in event.details
        assert event.details["input_key_sha256"] == hashlib.sha256(key.encode()).hexdigest()
    finally:
        REGISTRY._shuttles.pop("email-domain")


async def test_closed_cases_refuse_new_jobs(world: World) -> None:
    owner = world.clients["owner"]
    closed = await owner.patch(
        case_url(world.case_id), json={"status": "closed"}, headers=csrf(owner)
    )
    assert closed.status_code == 200
    response = await start(owner, world.case_id)
    assert response.status_code == 409


async def test_per_user_limit_of_active_jobs(
    world: World, infra: Infra, job_queue: RecordingJobQueue
) -> None:
    editor = world.clients["editor"]
    jobs = [await start(editor, world.case_id) for _ in range(5)]
    assert [r.status_code for r in jobs] == [201] * 5

    mark = await last_event_id(infra)
    refused = await start(editor, world.case_id)
    assert refused.status_code == 429
    assert "5 queued or running jobs" in refused.json()["detail"]
    (event,) = await events_since(infra, mark)
    assert (event.action, event.outcome, event.details["reason"]) == (
        "job.create",
        "failure",
        "too_many_jobs",
    )
    assert len(job_queue.sent) == 5

    # Other users have their own limit.
    assert (await start(world.clients["owner"], world.case_id)).status_code == 201
    # A running job still counts; a finished one does not.
    async with infra.sessionmaker() as db:
        await db.execute(
            update(Job)
            .where(Job.id == uuid.UUID(jobs[0].json()["id"]))
            .values(status=JobStatus.RUNNING)
        )
        await db.commit()
    assert (await start(editor, world.case_id)).status_code == 429
    cancelled = await editor.post(
        case_url(world.case_id, f"/jobs/{jobs[1].json()['id']}/cancel"), headers=csrf(editor)
    )
    assert cancelled.status_code == 200
    assert (await start(editor, world.case_id)).status_code == 201


async def test_queue_failure_fails_the_job(
    world: World, infra: Infra, job_queue: RecordingJobQueue
) -> None:
    job_queue.fail = True
    mark = await last_event_id(infra)
    response = await start(world.clients["editor"], world.case_id)
    assert response.status_code == 503
    assert response.json()["detail"] == service.ERROR_QUEUE
    events = await events_since(infra, mark)
    assert [(e.action, e.outcome) for e in events] == [
        ("job.create", "success"),
        ("job.finish", "failure"),
    ]
    assert events[1].details["reason"] == "queue_unavailable"
    async with infra.sessionmaker() as db:
        job = (await db.execute(select(Job))).scalar_one()
    assert job.status == JobStatus.FAILED
    assert job.error == service.ERROR_QUEUE
    assert job.finished_at is not None
    # It does not count against the limit.
    job_queue.fail = False
    assert (await start(world.clients["editor"], world.case_id)).status_code == 201


async def test_graph_unavailable_gives_503(world: World, db_app: FastAPI) -> None:
    driver = graph_infra.create_driver(Settings(env="test", neo4j_uri="bolt://127.0.0.1:1"))
    db_app.dependency_overrides[get_graph_repository] = lambda: GraphRepository(driver)
    try:
        response = await start(world.clients["owner"], world.case_id)
    finally:
        del db_app.dependency_overrides[get_graph_repository]
        await driver.close()
    assert response.status_code == 503


# --- listing and reading -------------------------------------------------------------


async def test_list_jobs_newest_first_with_keyset_pages(world: World) -> None:
    editor, viewer = world.clients["editor"], world.clients["viewer"]
    ids = [(await start(editor, world.case_id)).json()["id"] for _ in range(3)]

    first = await viewer.get(case_url(world.case_id, "/jobs"), params={"limit": 2})
    assert first.status_code == 200
    page = first.json()
    assert [item["id"] for item in page["items"]] == ids[::-1][:2]
    assert page["next_cursor"]
    second = await viewer.get(
        case_url(world.case_id, "/jobs"), params={"limit": 2, "cursor": page["next_cursor"]}
    )
    assert [item["id"] for item in second.json()["items"]] == [ids[0]]
    assert second.json()["next_cursor"] is None

    await editor.post(case_url(world.case_id, f"/jobs/{ids[1]}/cancel"), headers=csrf(editor))
    cancelled = await viewer.get(case_url(world.case_id, "/jobs"), params={"status": "cancelled"})
    assert [item["id"] for item in cancelled.json()["items"]] == [ids[1]]

    bad = await viewer.get(case_url(world.case_id, "/jobs"), params={"cursor": "not-a-cursor"})
    assert bad.status_code == 422
    outsider = await world.clients["admin"].get(case_url(world.case_id, "/jobs"))
    assert outsider.status_code == 404


async def test_get_job(world: World) -> None:
    owner = world.clients["owner"]
    job_id = (await start(owner, world.case_id)).json()["id"]
    response = await world.clients["viewer"].get(case_url(world.case_id, f"/jobs/{job_id}"))
    assert response.status_code == 200
    assert response.json()["id"] == job_id

    missing = await owner.get(case_url(world.case_id, f"/jobs/{uuid.uuid4()}"))
    assert (missing.status_code, missing.json()["detail"]) == (404, "Job not found.")
    # A job is only visible through its own case.
    other = await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))
    elsewhere = await owner.get(case_url(other.json()["id"], f"/jobs/{job_id}"))
    assert elsewhere.status_code == 404
    assert (
        await world.clients["admin"].get(case_url(world.case_id, f"/jobs/{job_id}"))
    ).status_code == 404


# --- cancelling ----------------------------------------------------------------------


async def test_cancel_a_queued_job(world: World, infra: Infra) -> None:
    editor = world.clients["editor"]
    job_id = (await start(editor, world.case_id)).json()["id"]
    mark = await last_event_id(infra)

    response = await editor.post(
        case_url(world.case_id, f"/jobs/{job_id}/cancel"), headers=csrf(editor)
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["cancel_requested"]) == ("cancelled", True)
    assert body["finished_at"] is not None
    (event,) = await events_since(infra, mark)
    assert (event.action, event.outcome) == ("job.cancel", "success")
    assert (event.details["status_before"], event.details["status"]) == ("queued", "cancelled")
    assert event.target_id == job_id

    again = await editor.post(
        case_url(world.case_id, f"/jobs/{job_id}/cancel"), headers=csrf(editor)
    )
    assert (again.status_code, again.json()["detail"]) == (409, "This job has already finished.")


async def test_cancel_a_running_job_asks_the_worker(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    job_id = (await start(owner, world.case_id)).json()["id"]
    async with infra.sessionmaker() as db:
        await db.execute(
            update(Job)
            .where(Job.id == uuid.UUID(job_id))
            .values(status=JobStatus.RUNNING, started_at=Job.created_at)
        )
        await db.commit()
    response = await owner.post(
        case_url(world.case_id, f"/jobs/{job_id}/cancel"), headers=csrf(owner)
    )
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["cancel_requested"]) == ("running", True)


async def test_cancel_permissions_and_closed_cases(world: World) -> None:
    owner = world.clients["owner"]
    job_id = (await start(owner, world.case_id)).json()["id"]
    url = case_url(world.case_id, f"/jobs/{job_id}/cancel")
    viewer = world.clients["viewer"]
    assert (await viewer.post(url, headers=csrf(viewer))).status_code == 403
    admin = world.clients["admin"]
    assert (await admin.post(url, headers=csrf(admin))).status_code == 404
    missing = case_url(world.case_id, f"/jobs/{uuid.uuid4()}/cancel")
    assert (await owner.post(missing, headers=csrf(owner))).status_code == 404
    # Cancelling still works once the case is closed.
    await owner.patch(case_url(world.case_id), json={"status": "closed"}, headers=csrf(owner))
    assert (await owner.post(url, headers=csrf(owner))).status_code == 200


async def test_jobs_are_deleted_with_their_case(world: World, infra: Infra) -> None:
    owner = world.clients["owner"]
    await start(owner, world.case_id)
    deleted = await owner.request(
        "DELETE",
        case_url(world.case_id),
        json={"confirm_title": CASE["title"]},
        headers=csrf(owner),
    )
    assert deleted.status_code == 204
    async with infra.sessionmaker() as db:
        assert (await db.execute(select(Job))).first() is None
