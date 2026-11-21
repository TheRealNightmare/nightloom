"""Scan profiles and runs against PostgreSQL and Valkey (and Neo4j for starting runs)."""

import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text, update

from db_fixtures import RecordingJobQueue
from nightloom_api.audit.models import AuditEvent
from nightloom_api.audit.service import Actor
from nightloom_api.auth.models import Role, User
from nightloom_api.cases.models import Case, CaseMember
from nightloom_api.infra import Infra
from nightloom_api.jobs import service as jobs_service
from nightloom_api.jobs.models import Job, JobStatus
from nightloom_api.scans import profiles
from nightloom_api.scans import service as scans
from nightloom_api.scans.models import ScanProfile, ScanRun
from nightloom_api.scans.profiles import Profile
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import EntityInput, Finding, Mode, Shuttle, ShuttleContext, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_shuttle_registrable_domain import RegistrableDomain
from support import PREFIX, csrf, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]

CASE: dict[str, Any] = {
    "title": "Example exposure review",
    "purpose": "Map the public attack surface of example.org for its owner.",
    "legal_basis": "owned_assets",
    "ethics_acknowledged": True,
}
LIMIT = 5


def _meta(name: str, **overrides: Any) -> ShuttleMeta:
    values: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "title": f"Test {name}",
        "description": "A Shuttle used by the scan tests.",
        "input_types": {EntityType.DOMAIN},
        "output_entity_types": {EntityType.DOMAIN},
        "output_relationship_types": {RelationType.HAS_SUBDOMAIN},
        "sensitivity": Sensitivity.INFRASTRUCTURE,
        "network": False,
        "timeout_seconds": 45,
    }
    values.update(overrides)
    return ShuttleMeta.model_validate(values)


class _Nothing(Shuttle):
    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        return
        yield


class Lookup(_Nothing):
    meta = _meta("lookup")


class Addresses(_Nothing):
    meta = _meta(
        "addresses",
        input_types={EntityType.IP_ADDRESS},
        output_entity_types={EntityType.NETBLOCK},
        output_relationship_types={RelationType.WITHIN},
    )


class StaffEmails(_Nothing):
    meta = _meta(
        "staff-emails",
        output_entity_types={EntityType.EMAIL},
        output_relationship_types={RelationType.HAS_EMAIL},
        sensitivity=Sensitivity.PERSON,
    )


class ActiveScan(_Nothing):
    meta = _meta("active-scan", mode=Mode.ACTIVE)


REGISTRY = ShuttleRegistry([RegistrableDomain, Lookup, Addresses, StaffEmails, ActiveScan])
PROFILE = Profile(id="test", name="Test", description="", shuttles=("lookup",), built_in=False)


@pytest.fixture(autouse=True)
def _registry(db_app: FastAPI) -> None:
    db_app.dependency_overrides[jobs_service.get_shuttle_registry] = lambda: REGISTRY


@dataclass
class World:
    infra: Infra
    case_id: uuid.UUID
    users: dict[str, User]
    clients: dict[str, AsyncClient]
    queue: RecordingJobQueue

    async def case(self, case_id: uuid.UUID | None = None) -> Case:
        async with self.infra.sessionmaker() as db:
            case = await db.get(Case, case_id or self.case_id)
            assert case is not None
            return case

    async def new_run(
        self,
        jobs: int,
        *,
        role: str = "editor",
        shuttle: str = "lookup",
        case_id: uuid.UUID | None = None,
        use_cache: bool = True,
    ) -> ScanRun:
        plan = scans.Plan(
            items=[
                {"shuttle": shuttle, "entity_key": f"domain:h{i}.example.org"} for i in range(jobs)
            ],
            skipped=Counter({"not_accepted": 1}),
        )
        user = self.users[role]
        async with self.infra.sessionmaker() as db:
            return await scans.create_run(
                db,
                case=await self.case(case_id),
                profile=PROFILE,
                plan=plan,
                entities=jobs,
                user=user,
                actor=Actor.for_user(user, None),
                use_cache=use_cache,
            )

    async def set_status(self, status: str) -> None:
        async with self.infra.sessionmaker() as db:
            await db.execute(
                update(Case)
                .where(Case.id == self.case_id)
                .values(status=status, closed_at=text("now()") if status == "closed" else None)
            )
            await db.commit()

    async def release(self, role: str = "editor") -> list[scans.Released]:
        released = await scans.release(
            self.infra.sessionmaker, user_id=self.users[role].id, limit=LIMIT, registry=REGISTRY
        )
        await scans.enqueue(self.infra.sessionmaker, self.queue, released)
        return released

    async def run(self, run_id: uuid.UUID) -> ScanRun:
        async with self.infra.sessionmaker() as db:
            run = await db.get(ScanRun, run_id)
            assert run is not None
            return run

    async def jobs(self, run_id: uuid.UUID) -> list[Job]:
        async with self.infra.sessionmaker() as db:
            rows = await db.execute(
                select(Job).where(Job.run_id == run_id).order_by(Job.created_at)
            )
            return list(rows.scalars())

    async def finish(self, job_ids: list[uuid.UUID], status: str = "succeeded") -> None:
        async with self.infra.sessionmaker() as db:
            await db.execute(
                update(Job)
                .where(Job.id.in_(job_ids))
                .values(status=status, started_at=text("now()"), finished_at=text("now()"))
            )
            await db.commit()

    async def events(self, action: str) -> list[AuditEvent]:
        async with self.infra.sessionmaker() as db:
            rows = await db.execute(
                select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
            )
            return list(rows.scalars())


@pytest.fixture
async def world(
    infra: Infra, make_user: UserFactory, new_client: ClientFactory, job_queue: RecordingJobQueue
) -> World:
    users = {
        "owner": await make_user("owner@example.org", Role.ANALYST),
        "editor": await make_user("editor@example.org", Role.ANALYST),
        "viewer": await make_user("viewer@example.org", Role.ANALYST),
        "outsider": await make_user("outsider@example.org", Role.ANALYST),
        "admin": await make_user("admin@example.org", Role.ADMIN),
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
    return World(infra, case_id, users, clients, job_queue)


# --- releasing -------------------------------------------------------------------------------


async def test_a_run_never_exceeds_the_per_user_job_limit(world: World) -> None:
    run = await world.new_run(8)
    assert (run.status, run.released, run.total, run.skipped) == ("running", 0, 8, 1)
    first = await world.release()
    assert len(first) == LIMIT
    assert [sent for sent, _ in world.queue.sent] == [item.job_id for item in first]
    assert {timeout for _, timeout in world.queue.sent} == {45}
    jobs = await world.jobs(run.id)
    assert [job.input_key for job in jobs] == [f"domain:h{i}.example.org" for i in range(5)]
    assert all(job.status == JobStatus.QUEUED and job.use_cache for job in jobs)
    assert all(job.requested_by == world.users["editor"].id for job in jobs)
    assert await world.release() == []  # no free slot

    await world.finish([jobs[0].id, jobs[1].id])
    assert len(await world.release()) == 2
    assert (await world.run(run.id)).released == 7
    await world.finish([job.id for job in await world.jobs(run.id)][2:])
    assert len(await world.release()) == 1
    # Everything released; finished once its last job is.
    assert (await world.run(run.id)).status == "running"
    last = (await world.jobs(run.id))[-1]
    await world.finish([last.id], "failed")
    assert await world.release() == []
    done = await world.run(run.id)
    assert (done.status, done.released, done.finished_at is not None) == ("finished", 8, True)

    # The jobs were queued by the run, for the requester, and audited as such.
    events = await world.events("job.create")
    assert len(events) == 8
    assert all(e.actor_label == "system:scan-run" for e in events)
    assert all(e.details["run_id"] == str(run.id) for e in events)
    [created] = await world.events("scan_run.create")
    assert created.details == {
        "profile": "test",
        "profile_name": "Test",
        "entities": 8,
        "jobs": 8,
        "skipped": {"not_accepted": 1},
    }


async def test_single_jobs_count_toward_the_limit_and_older_runs_go_first(world: World) -> None:
    async with world.infra.sessionmaker() as db:
        db.add(
            Job(
                case_id=world.case_id,
                shuttle_name="lookup",
                shuttle_version="1.0.0",
                input_key="domain:example.org",
                requested_by=world.users["editor"].id,
                status=JobStatus.RUNNING,
                stats={},
            )
        )
        await db.commit()
    older = await world.new_run(3)
    newer = await world.new_run(4, use_cache=False)
    released = await world.release()
    assert len(released) == 4  # 5 minus the running single job
    assert [len(await world.jobs(older.id)), len(await world.jobs(newer.id))] == [3, 1]
    assert all(not job.use_cache for job in await world.jobs(newer.id))
    with pytest.raises(scans.TooManyRunsError):
        await world.new_run(1)
    # Another user's runs and slots are their own.
    other = await world.new_run(2, role="owner")
    assert len(await world.release("owner")) == 2
    assert (await world.run(other.id)).released == 2


async def test_runs_stop_when_the_case_closes_or_the_requester_may_not_run(world: World) -> None:
    run = await world.new_run(7)
    await world.release()
    await world.set_status("closed")
    await world.finish([job.id for job in await world.jobs(run.id)])
    assert await world.release() == []
    stopped = await world.run(run.id)
    assert (stopped.status, stopped.stop_reason, stopped.released) == (
        "stopped",
        "case_not_open",
        5,
    )

    await world.set_status("open")
    removed = await world.new_run(1)
    async with world.infra.sessionmaker() as db:
        await db.execute(
            CaseMember.__table__.delete().where(  # type: ignore[attr-defined]
                CaseMember.user_id == world.users["editor"].id
            )
        )
        await db.commit()
    assert await world.release() == []
    assert (await world.run(removed.id)).stop_reason == "requester_not_allowed"


async def test_uninstalled_shuttles_are_skipped_and_unqueued_jobs_fail(world: World) -> None:
    run = await world.new_run(2, shuttle="gone")
    assert await world.release() == []
    gone = await world.run(run.id)
    assert (gone.status, gone.released, gone.skipped) == ("finished", 2, 3)

    world.queue.fail = True
    run = await world.new_run(1)
    released = await world.release()
    assert len(released) == 1
    [job] = await world.jobs(run.id)
    assert (job.status, job.error) == (JobStatus.FAILED, jobs_service.ERROR_QUEUE)
    world.queue.fail = False
    assert await world.release() == []
    assert (await world.run(run.id)).status == "finished"


async def test_orphaned_runs_stop_and_running_users_are_listed(world: World) -> None:
    run = await world.new_run(1)
    async with world.infra.sessionmaker() as db:
        assert await scans.users_with_running_runs(db) == [world.users["editor"].id]
        await db.execute(update(ScanRun).values(requested_by=None))
        await db.commit()
    async with world.infra.sessionmaker() as db, db.begin():
        assert await scans.stop_orphaned_runs(db) == 1
    assert (await world.run(run.id)).status == "stopped"
    async with world.infra.sessionmaker() as db:
        assert await scans.users_with_running_runs(db) == []


# --- pivots ----------------------------------------------------------------------------------

PIVOT_PROFILE = Profile(
    id="pivot-test", name="Pivot", description="", shuttles=("lookup", "addresses"), built_in=False
)
SEED = "domain:example.org"


async def _new_pivot(world: World) -> ScanRun:
    user = world.users["editor"]
    plan = scans.plan_run(
        PIVOT_PROFILE, [(SEED, EntityType.DOMAIN)], REGISTRY, allow_person_entities=False
    )
    async with world.infra.sessionmaker() as db:
        return await scans.create_run(
            db,
            case=await world.case(),
            profile=PIVOT_PROFILE,
            plan=plan,
            entities=1,
            user=user,
            actor=Actor.for_user(user, None),
            use_cache=True,
            pivot=True,
            seeds=[SEED],
        )


async def _found(
    world: World, job_id: uuid.UUID, keys: list[str], status: str = "succeeded"
) -> None:
    async with world.infra.sessionmaker() as db:
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=status,
                started_at=text("now()"),
                finished_at=text("now()"),
                stats={"entities_touched": keys},
            )
        )
        await db.commit()


async def test_a_pivot_continues_from_what_it_finds_up_to_two_hops(world: World) -> None:
    run = await _new_pivot(world)
    assert (run.pivot, run.max_depth, run.max_jobs, run.max_entities) == (True, 2, 200, 500)
    assert run.plan == [{"shuttle": "lookup", "entity_key": SEED, "depth": 0}]
    assert run.seen == [SEED]
    await world.release()
    [seed_job] = await world.jobs(run.id)
    assert seed_job.pivot_depth == 0
    # The seed's job found a subdomain, an address, an email (person), an
    # organization, and the seed itself again.
    await _found(
        world,
        seed_job.id,
        [
            SEED,
            "domain:www.example.org",
            "ip_address:192.0.2.10",
            "email:alex@example.org",
            "organization:example inc",
        ],
    )
    released = await world.release()
    assert len(released) == 2
    hop1 = await world.run(run.id)
    assert hop1.discovered == 2
    assert hop1.plan[1:] == [
        {"shuttle": "lookup", "entity_key": "domain:www.example.org", "depth": 1},
        {"shuttle": "addresses", "entity_key": "ip_address:192.0.2.10", "depth": 1},
    ]
    jobs = {job.input_key: job for job in await world.jobs(run.id)}
    assert jobs["domain:www.example.org"].pivot_depth == 1
    assert jobs[SEED].pivot_expanded is True

    # Hop 2 is planned from hop 1; nothing is planned from hop 2.
    await _found(world, jobs["domain:www.example.org"].id, ["domain:api.example.org"])
    await _found(world, jobs["ip_address:192.0.2.10"].id, ["domain:www.example.org"], "failed")
    assert len(await world.release()) == 1
    hop2 = {job.input_key: job for job in await world.jobs(run.id)}["domain:api.example.org"]
    assert hop2.pivot_depth == 2
    assert (await world.run(run.id)).status == "running"
    await _found(world, hop2.id, ["domain:deep.example.org"])
    assert await world.release() == []
    done = await world.run(run.id)
    assert (done.status, done.discovered, done.total, done.capped) == ("finished", 3, 4, None)
    response = await world.clients["viewer"].get(
        f"{PREFIX}/cases/{world.case_id}/scan-runs/{run.id}"
    )
    body = response.json()
    assert (body["pivot"], body["discovered"], body["depth"], body["max_depth"]) == (
        True,
        3,
        2,
        2,
    )
    [created] = await world.events("scan_run.create")
    assert created.details["pivot"] is True


async def test_a_pivot_stops_planning_at_its_limits(world: World) -> None:
    run = await _new_pivot(world)
    async with world.infra.sessionmaker() as db:
        await db.execute(update(ScanRun).where(ScanRun.id == run.id).values(max_entities=2))
        await db.commit()
    await world.release()
    [seed_job] = await world.jobs(run.id)
    await _found(world, seed_job.id, [f"domain:h{i}.example.org" for i in range(5)])
    await world.release()
    capped = await world.run(run.id)
    assert (capped.capped, capped.discovered, capped.total) == ("entities", 2, 3)
    # Once capped, finished jobs add nothing more.
    for job in await world.jobs(run.id):
        if job.status == JobStatus.QUEUED:
            await _found(world, job.id, ["domain:more.example.org"])
    assert await world.release() == []
    assert (await world.run(run.id)).status == "finished"

    other = await _new_pivot(world)
    async with world.infra.sessionmaker() as db:
        await db.execute(update(ScanRun).where(ScanRun.id == other.id).values(max_jobs=2))
        await db.commit()
    await world.release()
    [seed_job] = await world.jobs(other.id)
    await _found(world, seed_job.id, ["domain:a.example.org", "domain:b.example.org"])
    await world.release()
    jobs_capped = await world.run(other.id)
    assert (jobs_capped.capped, jobs_capped.total, jobs_capped.discovered) == ("jobs", 2, 1)


async def test_cancelled_jobs_and_ordinary_runs_do_not_expand(world: World) -> None:
    run = await _new_pivot(world)
    await world.release()
    [seed_job] = await world.jobs(run.id)
    await _found(world, seed_job.id, ["domain:www.example.org"], "cancelled")
    await world.release()
    assert (await world.run(run.id)).total == 1
    plain = await world.new_run(1)
    await world.release()
    [job] = await world.jobs(plain.id)
    assert job.pivot_depth is None
    await _found(world, job.id, ["domain:www.example.org"])
    await world.release()
    assert (await world.run(plain.id)).total == 1


async def test_owners_turn_auto_pivot_on(world: World) -> None:
    url = f"{PREFIX}/cases/{world.case_id}"
    editor = world.clients["editor"]
    assert (await editor.get(url)).json()["allow_auto_pivot"] is False
    refused = await editor.patch(url, json={"allow_auto_pivot": True}, headers=csrf(editor))
    assert refused.status_code == 403
    owner = world.clients["owner"]
    turned = await owner.patch(url, json={"allow_auto_pivot": True}, headers=csrf(owner))
    assert turned.status_code == 200, turned.text
    assert turned.json()["allow_auto_pivot"] is True
    assert (
        await owner.patch(url, json={"allow_auto_pivot": None}, headers=csrf(owner))
    ).status_code == 422
    [event] = [e for e in await world.events("case.update") if e.details.get("fields")]
    assert event.details["fields"] == ["allow_auto_pivot"]


# --- runs API (reading, cancelling) ----------------------------------------------------------


async def test_members_read_runs_with_their_jobs(world: World) -> None:
    run = await world.new_run(7)
    await world.release()
    jobs = await world.jobs(run.id)
    await world.finish([jobs[0].id])
    await world.finish([jobs[1].id], "failed")
    url = f"{PREFIX}/cases/{world.case_id}/scan-runs"
    response = await world.clients["viewer"].get(url)
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["id"] == str(run.id)
    assert (item["status"], item["total"], item["released"], item["waiting"]) == (
        "running",
        7,
        5,
        2,
    )
    assert item["jobs"] == {"queued": 3, "running": 0, "succeeded": 1, "failed": 1, "cancelled": 0}
    assert (item["profile_id"], item["profile_name"], item["entities"]) == ("test", "Test", 7)
    one = await world.clients["viewer"].get(f"{url}/{run.id}")
    assert one.json() == item
    assert (await world.clients["outsider"].get(url)).status_code == 404
    assert (await world.clients["viewer"].get(f"{url}/{uuid.uuid4()}")).status_code == 404
    # Jobs name their run.
    page = await world.clients["viewer"].get(f"{PREFIX}/cases/{world.case_id}/jobs")
    assert {job["run_id"] for job in page.json()["items"]} == {str(run.id)}


async def test_cancelling_a_run(world: World) -> None:
    run = await world.new_run(8)
    await world.release()
    jobs = await world.jobs(run.id)
    async with world.infra.sessionmaker() as db:
        await db.execute(
            update(Job)
            .where(Job.id == jobs[0].id)
            .values(status="running", started_at=text("now()"))
        )
        await db.commit()
    url = f"{PREFIX}/cases/{world.case_id}/scan-runs/{run.id}/cancel"
    viewer = world.clients["viewer"]
    assert (await viewer.post(url, headers=csrf(viewer))).status_code == 403
    # Also allowed in a closed case.
    await world.set_status("closed")
    editor = world.clients["editor"]
    response = await editor.post(url, headers=csrf(editor))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["waiting"]) == ("cancelled", 0)
    assert body["jobs"]["cancelled"] == 4
    after = {job.id: job for job in await world.jobs(run.id)}
    assert after[jobs[0].id].status == JobStatus.RUNNING
    assert after[jobs[0].id].cancel_requested is True
    assert (await editor.post(url, headers=csrf(editor))).status_code == 409
    assert await world.release() == []
    [event] = await world.events("scan_run.cancel")
    assert event.details == {"profile": "test", "jobs_cancelled": 5, "not_released": 3}
    assert len(await world.events("job.cancel")) == 5


# --- profiles API ----------------------------------------------------------------------------


async def test_everyone_signed_in_lists_the_profiles(
    world: World, new_client: ClientFactory
) -> None:
    assert (await new_client().get(f"{PREFIX}/scan-profiles")).status_code == 401
    response = await world.clients["viewer"].get(f"{PREFIX}/scan-profiles")
    assert response.status_code == 200
    items = response.json()
    assert [item["id"] for item in items] == [p.id for p in profiles.BUILT_IN_PROFILES]
    quick = items[0]
    assert quick["built_in"] is True
    assert quick["shuttles"] == ["dns", "rdap", "registrable-domain"]
    # With this test registry only registrable-domain is installed.
    assert quick["missing_shuttles"] == ["dns", "rdap"]
    assert quick["input_types"] == ["domain"]


async def test_admins_manage_custom_profiles(world: World) -> None:
    admin = world.clients["admin"]
    url = f"{PREFIX}/scan-profiles"
    analyst = world.clients["owner"]
    body = {"name": "  Mail   check ", "description": "Domains", "shuttles": ["lookup", "lookup"]}
    assert (await analyst.post(url, json=body, headers=csrf(analyst))).status_code == 403
    created = await admin.post(url, json=body, headers=csrf(admin))
    assert created.status_code == 201, created.text
    profile = created.json()
    assert (profile["name"], profile["shuttles"], profile["built_in"]) == (
        "Mail check",
        ["lookup"],
        False,
    )
    assert profile["input_types"] == ["domain"]
    profile_url = f"{url}/{profile['id']}"

    for name in ("mail CHECK", "Quick domain recon"):
        clash = await admin.post(url, json={**body, "name": name}, headers=csrf(admin))
        assert clash.status_code == 409
    for shuttles, message in ((["active-scan"], "active Shuttle"), (["nope"], "Unknown Shuttle")):
        bad = await admin.post(
            url, json={"name": "Other", "shuttles": shuttles}, headers=csrf(admin)
        )
        assert bad.status_code == 422
        assert message in bad.text
    assert (
        await admin.post(url, json={"name": "x", "shuttles": []}, headers=csrf(admin))
    ).status_code == 422

    renamed = await admin.patch(
        profile_url,
        json={"name": "Mail servers", "shuttles": ["lookup", "staff-emails"]},
        headers=csrf(admin),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["shuttles"] == ["lookup", "staff-emails"]
    listed = (await world.clients["viewer"].get(url)).json()
    assert listed[-1]["name"] == "Mail servers"

    built_in = f"{url}/quick-domain-recon"
    assert (
        await admin.patch(built_in, json={"name": "Mine"}, headers=csrf(admin))
    ).status_code == 403
    assert (await admin.delete(built_in, headers=csrf(admin))).status_code == 403
    assert (
        await admin.patch(f"{url}/{uuid.uuid4()}", json={}, headers=csrf(admin))
    ).status_code == 404
    assert (await admin.delete(profile_url, headers=csrf(admin))).status_code == 204
    assert (await admin.delete(profile_url, headers=csrf(admin))).status_code == 404

    events = [
        (e.action, e.details)
        for e in await world.events("scan_profile.create")
        + await world.events("scan_profile.update")
        + await world.events("scan_profile.delete")
    ]
    assert events == [
        ("scan_profile.create", {"name": "Mail check", "shuttles": ["lookup"]}),
        (
            "scan_profile.update",
            {
                "name": "Mail servers",
                "changed": ["name", "shuttles"],
                "shuttles": ["lookup", "staff-emails"],
            },
        ),
        ("scan_profile.delete", {"name": "Mail servers"}),
    ]


async def test_at_most_fifty_custom_profiles(world: World) -> None:
    async with world.infra.sessionmaker() as db:
        for i in range(profiles.MAX_CUSTOM_PROFILES):
            db.add(ScanProfile(name=f"Profile {i}", description="", shuttles=["lookup"]))
        await db.commit()
    admin = world.clients["admin"]
    response = await admin.post(
        f"{PREFIX}/scan-profiles",
        json={"name": "One more", "shuttles": ["lookup"]},
        headers=csrf(admin),
    )
    assert response.status_code == 409
    assert "Delete one first" in response.text


# --- starting runs (needs Neo4j) -------------------------------------------------------------


async def _add_entity(client: AsyncClient, case_id: uuid.UUID, type_: str, value: str) -> str:
    response = await client.post(
        f"{PREFIX}/cases/{case_id}/entities",
        json={"type": type_, "value": value},
        headers=csrf(client),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["key"])


@pytest.mark.graph
async def test_starting_a_run(world: World) -> None:
    editor = world.clients["editor"]
    admin = world.clients["admin"]
    created = await admin.post(
        f"{PREFIX}/scan-profiles",
        json={
            "name": "Mixed",
            "shuttles": ["lookup", "addresses", "staff-emails", "registrable-domain"],
        },
        headers=csrf(admin),
    )
    profile_id = created.json()["id"]
    keys = [
        await _add_entity(editor, world.case_id, "domain", f"h{i}.example.org") for i in range(3)
    ]
    keys.append(await _add_entity(editor, world.case_id, "ip_address", "192.0.2.10"))
    url = f"{PREFIX}/cases/{world.case_id}/scan-runs"

    response = await editor.post(
        url,
        json={"profile_id": profile_id, "entity_keys": [*keys, keys[0]], "fresh": True},
        headers=csrf(editor),
    )
    assert response.status_code == 201, response.text
    run = response.json()
    # 3 domains x (lookup, registrable-domain) + the address x addresses = 7;
    # staff-emails needs a person case; the rest do not accept the type.
    assert (run["entities"], run["total"], run["released"], run["waiting"]) == (4, 7, 5, 2)
    # Each domain: addresses (type) and staff-emails (person gate); the address: three types.
    assert run["skipped"] == 2 * 3 + 3
    assert run["use_cache"] is False
    assert run["jobs"]["queued"] == 5
    assert len(world.queue.sent) == 5
    [event] = await world.events("scan_run.create")
    assert event.details["fresh"] is True

    # Pivots need the case owner's permission.
    refused = await editor.post(
        url,
        json={"profile_id": profile_id, "entity_keys": keys[:1], "pivot": True},
        headers=csrf(editor),
    )
    assert refused.status_code == 403
    assert "Auto-pivot is off" in refused.text
    denied = [e for e in await world.events("scan_run.create") if e.outcome == "denied"]
    assert [e.details["reason"] for e in denied] == ["auto_pivot_disabled"]

    bad_bodies: list[tuple[dict[str, Any], int]] = [
        ({"profile_id": "nope", "entity_keys": keys}, 422),
        ({"profile_id": profile_id, "entity_keys": ["domain:missing.example.org"]}, 422),
        ({"profile_id": profile_id, "entity_keys": ["not a key"]}, 422),
        ({"profile_id": profile_id, "entity_keys": []}, 422),
        ({"profile_id": profile_id, "entity_keys": [f"domain:{i}.org" for i in range(26)]}, 422),
        ({"profile_id": "exposure-check", "entity_keys": keys[:3]}, 422),  # nothing to run
    ]
    for body, expected in bad_bodies:
        response = await editor.post(url, json=body, headers=csrf(editor))
        assert response.status_code == expected, (body, response.text)

    second = await editor.post(
        url,
        json={"profile_id": "quick-domain-recon", "entity_keys": keys[:1]},
        headers=csrf(editor),
    )
    assert second.status_code == 201, second.text
    third = await editor.post(
        url,
        json={"profile_id": "quick-domain-recon", "entity_keys": keys[:1]},
        headers=csrf(editor),
    )
    assert third.status_code == 429
    failures = [e for e in await world.events("scan_run.create") if e.outcome == "failure"]
    assert [e.details["reason"] for e in failures] == ["too_many_runs"]

    viewer = world.clients["viewer"]
    refused = await viewer.post(
        url, json={"profile_id": profile_id, "entity_keys": keys[:1]}, headers=csrf(viewer)
    )
    assert refused.status_code == 403
    await world.set_status("closed")
    closed = await world.clients["owner"].post(
        url,
        json={"profile_id": profile_id, "entity_keys": keys[:1]},
        headers=csrf(world.clients["owner"]),
    )
    assert closed.status_code == 409


@pytest.mark.graph
async def test_starting_a_pivot(world: World) -> None:
    owner = world.clients["owner"]
    turned = await owner.patch(
        f"{PREFIX}/cases/{world.case_id}", json={"allow_auto_pivot": True}, headers=csrf(owner)
    )
    assert turned.status_code == 200, turned.text
    editor = world.clients["editor"]
    domain = await _add_entity(editor, world.case_id, "domain", "example.org")
    organization = await _add_entity(editor, world.case_id, "organization", "Example Inc")
    url = f"{PREFIX}/cases/{world.case_id}/scan-runs"

    # Not from an organization (or anything else that is not infrastructure).
    mixed = await editor.post(
        url,
        json={
            "profile_id": "quick-domain-recon",
            "entity_keys": [domain, organization],
            "pivot": True,
        },
        headers=csrf(editor),
    )
    assert mixed.status_code == 422
    assert "infrastructure" in mixed.text

    started = await editor.post(
        url,
        json={"profile_id": "quick-domain-recon", "entity_keys": [domain], "pivot": True},
        headers=csrf(editor),
    )
    assert started.status_code == 201, started.text
    run = started.json()
    assert (run["pivot"], run["max_depth"], run["max_jobs"], run["max_entities"]) == (
        True,
        2,
        200,
        500,
    )
    assert (run["discovered"], run["depth"], run["capped"]) == (0, 0, None)
    [event] = [e for e in await world.events("scan_run.create") if e.outcome == "success"]
    assert event.details["pivot"] is True
