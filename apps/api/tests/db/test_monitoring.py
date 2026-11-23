"""Certificate Transparency monitoring against PostgreSQL: the API, the sweep, alerts.

The graph is replaced by a stand-in that knows which Domains exist, so these
run without Neo4j.
"""

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update

from db_fixtures import RecordingJobQueue
from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth import clock
from nightloom_api.auth.models import Role, User
from nightloom_api.cases.models import Case, CaseMember, CaseStatus
from nightloom_api.infra import Infra
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.jobs.models import Job, JobStatus, empty_stats
from nightloom_api.monitoring import service
from nightloom_api.monitoring.models import CtAlert, CtWatch
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import EntityInput, Finding, Shuttle, ShuttleContext, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry
from support import PREFIX, csrf, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]

CASE: dict[str, Any] = {
    "title": "Example certificate watch",
    "purpose": "Hear about new certificates for example.org, which the client owns.",
    "legal_basis": "owned_assets",
    "ethics_acknowledged": True,
}
LIMIT = 5


class CrtSh(Shuttle):
    meta = ShuttleMeta.model_validate(
        {
            "name": "crtsh",
            "version": "1.0.0",
            "title": "Test crt.sh",
            "description": "Stands in for the crt.sh Shuttle.",
            "input_types": {EntityType.DOMAIN},
            "output_entity_types": {EntityType.DOMAIN, EntityType.CERTIFICATE},
            "output_relationship_types": {RelationType.HAS_SUBDOMAIN},
            "sensitivity": Sensitivity.INFRASTRUCTURE,
            "network": False,
            "timeout_seconds": 120,
        }
    )

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        return
        yield


REGISTRY = ShuttleRegistry([CrtSh])


class FakeGraph:
    """Knows which entity keys exist in which case; nothing else."""

    def __init__(self) -> None:
        self.keys: set[tuple[uuid.UUID, str]] = set()

    async def get_entity(self, case_id: uuid.UUID, key: str) -> object | None:
        return object() if (case_id, key) in self.keys else None


@dataclass
class World:
    infra: Infra
    case_id: uuid.UUID
    clients: dict[str, AsyncClient]
    users: dict[str, User]
    queue: RecordingJobQueue
    graph: FakeGraph

    @property
    def url(self) -> str:
        return f"{PREFIX}/cases/{self.case_id}/ct-watches"

    @property
    def alerts_url(self) -> str:
        return f"{PREFIX}/cases/{self.case_id}/ct-alerts"

    def in_graph(self, domain: str) -> None:
        self.graph.keys.add((self.case_id, f"domain:{domain}"))

    async def watch(self, domain: str = "example.org") -> dict[str, Any]:
        self.in_graph(domain.strip().lower())
        owner = self.clients["owner"]
        response = await owner.post(self.url, json={"domain": domain}, headers=csrf(owner))
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    async def row(self, watch_id: str | uuid.UUID) -> CtWatch:
        async with self.infra.sessionmaker() as db:
            found = await db.get(CtWatch, uuid.UUID(str(watch_id)))
            assert found is not None
            return found

    async def sweep(self, now: datetime | None = None) -> tuple[int, int]:
        return await service.sweep(
            self.infra.sessionmaker,
            self.queue,
            now or clock.utcnow(),
            registry=REGISTRY,
            limit=LIMIT,
        )

    async def finish(
        self,
        job_id: uuid.UUID,
        status: JobStatus = JobStatus.SUCCEEDED,
        new: tuple[str, ...] | list[str] = (),
    ) -> None:
        stats = {**empty_stats(), "entities_new": list(new), "entities_created": len(new)}
        async with self.infra.sessionmaker() as db, db.begin():
            await db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status=status, finished_at=clock.utcnow(), stats=stats)
            )

    async def events(self, prefix: str) -> list[AuditEvent]:
        async with self.infra.sessionmaker() as db:
            rows = await db.execute(
                select(AuditEvent)
                .where(AuditEvent.action.startswith(prefix))
                .order_by(AuditEvent.id)
            )
            return list(rows.scalars())

    async def jobs(self) -> list[Job]:
        async with self.infra.sessionmaker() as db:
            rows = await db.execute(
                select(Job).where(Job.case_id == self.case_id).order_by(Job.created_at)
            )
            return list(rows.scalars())


@pytest.fixture
async def world(
    db_app: FastAPI,
    infra: Infra,
    make_user: UserFactory,
    new_client: ClientFactory,
    job_queue: RecordingJobQueue,
) -> World:
    graph = FakeGraph()
    db_app.dependency_overrides[get_graph_repository] = lambda: graph
    users = {
        "owner": await make_user("owner@example.org", Role.ANALYST),
        "editor": await make_user("editor@example.org", Role.ANALYST),
        "viewer": await make_user("viewer@example.org", Role.ANALYST),
    }
    clients = {}
    for role, user in users.items():
        client = new_client()
        assert (await login(client, email=user.email)).status_code == 200
        clients[role] = client
    owner = clients["owner"]
    created = await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))
    assert created.status_code == 201, created.text
    case_id = uuid.UUID(created.json()["id"])
    for role in ("editor", "viewer"):
        added = await owner.put(
            f"{PREFIX}/cases/{case_id}/members",
            json={"email": users[role].email, "role": role},
            headers=csrf(owner),
        )
        assert added.status_code == 201, added.text
    return World(infra, case_id, clients, users, job_queue, graph)


# --- watches -----------------------------------------------------------------------------


async def test_owners_watch_a_domain_of_the_graph(world: World) -> None:
    body = await world.watch(" Example.ORG ")
    assert body["domain"] == "example.org"
    assert body["checking"] is False
    assert body["baseline_at"] is None
    assert body["problem"] is None
    listed = await world.clients["viewer"].get(world.url)
    assert listed.status_code == 200
    assert [item["domain"] for item in listed.json()["items"]] == ["example.org"]
    [event] = await world.events("ct_watch.create")
    assert event.details == {"domain": "example.org"}
    assert event.case_id == world.case_id


async def test_only_owners_change_the_list_and_only_graph_domains(world: World) -> None:
    world.in_graph("example.org")
    for role in ("editor", "viewer"):
        client = world.clients[role]
        refused = await client.post(world.url, json={"domain": "example.org"}, headers=csrf(client))
        assert refused.status_code == 403
    owner = world.clients["owner"]
    missing = await owner.post(world.url, json={"domain": "other.org"}, headers=csrf(owner))
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Add other.org to the graph first, then watch it."
    for bad in ("*.example.org", "co.uk", "not a domain"):
        response = await owner.post(world.url, json={"domain": bad}, headers=csrf(owner))
        assert response.status_code == 422, bad
    await world.watch()
    again = await owner.post(world.url, json={"domain": "example.org"}, headers=csrf(owner))
    assert again.status_code == 409


async def test_removing_keeps_the_alerts(world: World) -> None:
    body = await world.watch()
    await world.sweep()
    [job] = await world.jobs()
    await world.finish(job.id)
    await world.sweep()
    async with world.infra.sessionmaker() as db, db.begin():
        await db.execute(
            update(CtWatch).values(next_check_at=clock.utcnow() - timedelta(seconds=1))
        )
    await world.sweep()
    second = (await world.jobs())[-1]
    await world.finish(second.id, new=["certificate:" + "a" * 64])
    await world.sweep()
    owner = world.clients["owner"]
    removed = await owner.delete(f"{world.url}/{body['id']}", headers=csrf(owner))
    assert removed.status_code == 204
    alerts = await owner.get(world.alerts_url)
    assert [item["certificates"] for item in alerts.json()["items"]] == [1]
    [event] = await world.events("ct_watch.delete")
    assert event.details == {"domain": "example.org"}


# --- the sweep ---------------------------------------------------------------------------


async def test_the_sweep_queues_an_uncached_crtsh_job_as_the_owner(world: World) -> None:
    body = await world.watch()
    now = clock.utcnow()
    assert await world.sweep(now) == (0, 1)
    [job] = await world.jobs()
    assert job.shuttle_name == "crtsh"
    assert job.input_key == "domain:example.org"
    assert job.requested_by == world.users["owner"].id
    assert job.use_cache is False
    assert world.queue.sent == [(job.id, CrtSh.meta.timeout_seconds)]
    row = await world.row(body["id"])
    assert row.pending_job_id == job.id
    assert row.next_check_at == now + service.CHECK_EVERY
    [created] = await world.events("job.create")
    assert created.actor_label == "system:ct-monitor"
    # Nothing more until the job finishes and the hour has passed.
    assert await world.sweep(now + timedelta(hours=2)) == (0, 0)


async def test_the_first_check_is_the_baseline_and_later_news_is_an_alert(world: World) -> None:
    body = await world.watch()
    now = clock.utcnow()
    await world.sweep(now)
    [first] = await world.jobs()
    await world.finish(first.id, new=["domain:www.example.org", "certificate:" + "b" * 64])
    assert await world.sweep(now) == (0, 0)
    row = await world.row(body["id"])
    assert row.baseline_at is not None
    assert row.pending_job_id is None
    assert row.last_job_id == first.id

    later = now + service.CHECK_EVERY
    assert await world.sweep(later) == (0, 1)
    second = (await world.jobs())[-1]
    new = [
        "domain:example.org",  # the watched domain itself is not news
        "domain:shop.example.org",
        "domain:mail.example.org",
        "certificate:" + "c" * 64,
        "ip_address:192.0.2.1",  # neither a name nor a certificate
    ]
    await world.finish(second.id, new=new)
    assert await world.sweep(later) == (1, 0)

    viewer = world.clients["viewer"]
    listed = (await viewer.get(world.alerts_url, params={"unseen": True})).json()
    assert listed["unseen"] == 1
    [alert] = listed["items"]
    assert alert["domain"] == "example.org"
    assert alert["certificates"] == 1
    assert alert["names"] == ["shop.example.org", "mail.example.org"]
    assert alert["names_total"] == 2
    assert alert["keys"] == [
        "domain:shop.example.org",
        "domain:mail.example.org",
        "certificate:" + "c" * 64,
    ]
    assert alert["job_id"] == str(second.id)
    [event] = await world.events("ct_alert.create")
    assert event.details is not None
    assert event.details["certificates"] == 1
    assert event.details["names"] == 2

    # Nothing new: no alert.
    await world.sweep(later + service.CHECK_EVERY)
    third = (await world.jobs())[-1]
    await world.finish(third.id)
    assert await world.sweep(later + service.CHECK_EVERY) == (0, 0)


async def test_editors_mark_alerts_seen(world: World) -> None:
    body = await world.watch()
    async with world.infra.sessionmaker() as db, db.begin():
        for n in range(2):
            db.add(
                CtAlert(
                    case_id=world.case_id,
                    watch_id=uuid.UUID(body["id"]),
                    domain="example.org",
                    certificates=n + 1,
                    names=[],
                    names_total=0,
                    keys=[],
                )
            )
    viewer = world.clients["viewer"]
    refused = await viewer.post(f"{world.alerts_url}/acknowledge", headers=csrf(viewer))
    assert refused.status_code == 403
    editor = world.clients["editor"]
    listed = (await editor.get(world.alerts_url)).json()
    first = listed["items"][0]["id"]
    one = await editor.post(
        f"{world.alerts_url}/acknowledge", json={"ids": [first]}, headers=csrf(editor)
    )
    assert one.json() == {"acknowledged": 1}
    rest = await editor.post(f"{world.alerts_url}/acknowledge", headers=csrf(editor))
    assert rest.json() == {"acknowledged": 1}
    after = (await viewer.get(world.alerts_url)).json()
    assert after["unseen"] == 0
    assert all(item["acknowledged_at"] is not None for item in after["items"])
    assert (await viewer.get(world.alerts_url, params={"unseen": True})).json()["items"] == []
    events = await world.events("ct_alert.acknowledge")
    assert [event.details for event in events] == [{"alerts": 1}, {"alerts": 1}]


async def test_failed_checks_are_retried_next_hour(world: World) -> None:
    body = await world.watch()
    now = clock.utcnow()
    await world.sweep(now)
    [job] = await world.jobs()
    await world.finish(job.id, JobStatus.FAILED)
    await world.sweep(now)
    row = await world.row(body["id"])
    assert row.last_problem == "check_failed"
    assert row.baseline_at is None
    listed = (await world.clients["viewer"].get(world.url)).json()["items"][0]
    assert listed["problem"] == "check_failed"
    assert listed["problem_message"] == service.PROBLEMS["check_failed"]
    assert await world.sweep(now + service.CHECK_EVERY) == (0, 1)


async def test_closed_cases_and_owners_who_left_get_no_jobs(world: World) -> None:
    body = await world.watch()
    async with world.infra.sessionmaker() as db, db.begin():
        await db.execute(
            update(Case)
            .where(Case.id == world.case_id)
            .values(status=CaseStatus.CLOSED.value, closed_at=clock.utcnow())
        )
    now = clock.utcnow()
    assert await world.sweep(now) == (0, 0)
    assert (await world.row(body["id"])).last_problem == "case_closed"
    async with world.infra.sessionmaker() as db, db.begin():
        await db.execute(
            update(Case)
            .where(Case.id == world.case_id)
            .values(status=CaseStatus.OPEN.value, closed_at=None)
        )
        await db.execute(
            update(CaseMember)
            .where(
                CaseMember.case_id == world.case_id,
                CaseMember.user_id == world.users["owner"].id,
            )
            .values(role="viewer")
        )
    assert await world.sweep(now + service.CHECK_EVERY) == (0, 0)
    assert (await world.row(body["id"])).last_problem == "owner_cannot_run"
    assert await world.jobs() == []


async def test_a_full_job_limit_waits_a_few_minutes(world: World) -> None:
    body = await world.watch()
    owner = world.users["owner"]
    async with world.infra.sessionmaker() as db, db.begin():
        for _ in range(LIMIT):
            db.add(
                Job(
                    case_id=world.case_id,
                    shuttle_name="other",
                    shuttle_version="1.0.0",
                    input_key="domain:example.org",
                    requested_by=owner.id,
                    status=JobStatus.QUEUED,
                    stats=empty_stats(),
                )
            )
    now = clock.utcnow()
    assert await world.sweep(now) == (0, 0)
    row = await world.row(body["id"])
    assert row.last_problem == "job_limit"
    assert row.next_check_at == now + service.RETRY_SOON


async def test_check_now_moves_the_next_check(world: World) -> None:
    body = await world.watch()
    now = clock.utcnow()
    await world.sweep(now)
    owner = world.clients["owner"]
    busy = await owner.post(f"{world.url}/{body['id']}/check", headers=csrf(owner))
    assert busy.status_code == 202
    assert busy.json()["checking"] is True
    [job] = await world.jobs()
    await world.finish(job.id)
    await world.sweep(now)
    async with world.infra.sessionmaker() as db, db.begin():
        await db.execute(update(CtWatch).values(checked_at=clock.utcnow() - timedelta(minutes=5)))
    moved = await owner.post(f"{world.url}/{body['id']}/check", headers=csrf(owner))
    assert moved.status_code == 202
    row = await world.row(body["id"])
    assert row.next_check_at <= clock.utcnow()
    editor = world.clients["editor"]
    refused = await editor.post(f"{world.url}/{body['id']}/check", headers=csrf(editor))
    assert refused.status_code == 403
