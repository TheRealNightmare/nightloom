"""The job runner against PostgreSQL and Neo4j, with scripted fake Shuttles."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import dns.rdata
import dns.rdataclass
import dns.rdatatype
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import delete, select, text, update

from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth import clock
from nightloom_api.auth.models import Role, User
from nightloom_api.cases.models import Case, CaseMember, CaseStatus
from nightloom_api.config import Settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra import Infra
from nightloom_api.infra import graph as graph_infra
from nightloom_api.jobs import service
from nightloom_api.jobs.models import Job, JobStatus, empty_stats
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    FailureReason,
    InputRefusedError,
    Mode,
    RefusalReason,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleFailedError,
    ShuttleMeta,
)
from nightloom_sdk.http import create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.shuttle import FAILURE_MESSAGES, REFUSAL_MESSAGES
from nightloom_shuttle_crtsh import CrtShShuttle
from nightloom_shuttle_dns import DnsShuttle
from nightloom_shuttle_dns.client import Answer
from nightloom_shuttle_dns.config import DnsConfig
from nightloom_shuttle_registrable_domain import RegistrableDomain
from nightloom_worker import runner
from nightloom_worker.runner import MAX_TOUCHED_KEYS, RunnerConfig, reap_stale_jobs, run_job
from support import PREFIX, csrf, login

pytestmark = [pytest.mark.db, pytest.mark.graph]

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]
Hook = Callable[[], Awaitable[None]]

CASE: dict[str, Any] = {
    "title": "Example exposure review",
    "purpose": "Map the public attack surface of example.org for its owner.",
    "legal_basis": "owned_assets",
    "ethics_acknowledged": True,
}
PERSON_REASON = "The owner asked us to check what their staff expose publicly."
SUBDOMAIN = "domain:www.shop.example.co.uk"
SECRET = "sk-live-0123456789abcdef"  # must never reach the database or the logs


def meta(name: str = "scripted", **overrides: Any) -> ShuttleMeta:
    values: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "title": "Scripted",
        "description": "A fake Shuttle that yields what the test tells it to.",
        "input_types": {EntityType.DOMAIN},
        "output_entity_types": {EntityType.DOMAIN},
        "output_relationship_types": {RelationType.HAS_SUBDOMAIN},
        "sensitivity": Sensitivity.INFRASTRUCTURE,
        "network": False,
        "default_admiralty": "C3",
        "default_confidence": 0.6,
    }
    values.update(overrides)
    return ShuttleMeta.model_validate(values)


def scripted(steps: Sequence[object], **meta_overrides: Any) -> type[Shuttle]:
    """A Shuttle that yields ``steps`` in order; a callable step is awaited instead."""

    class Scripted(Shuttle):
        meta = globals()["meta"](**meta_overrides)

        async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Any]:
            for step in steps:
                if callable(step) and not isinstance(step, BaseModel):
                    await step()
                    continue
                yield step

    return Scripted


def domain(value: str, **extra: Any) -> EntityFinding:
    return EntityFinding(type=EntityType.DOMAIN, value=value, **extra)


def sub(parent: str, child: str, **extra: Any) -> RelationshipFinding:
    return RelationshipFinding(
        type=RelationType.HAS_SUBDOMAIN,
        source=EntityRef(type=EntityType.DOMAIN, value=parent),
        target=EntityRef(type=EntityType.DOMAIN, value=child),
        **extra,
    )


@dataclass
class World:
    infra: Infra
    case_id: uuid.UUID
    owner: User
    editor: User
    owner_client: AsyncClient
    editor_client: AsyncClient

    def config(self, *shuttles: type[Shuttle], **overrides: Any) -> RunnerConfig:
        values: dict[str, Any] = {
            "sessionmaker": self.infra.sessionmaker,
            "graph": GraphRepository(self.infra.graph),
            "registry": ShuttleRegistry(shuttles),
            "cancel_poll_seconds": 0.05,
        }
        values.update(overrides)
        return RunnerConfig(**values)

    async def queue(
        self, shuttle: type[Shuttle], key: str = SUBDOMAIN, *, case_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        """Insert a queued job directly (the API's own checks are tested elsewhere)."""
        async with self.infra.sessionmaker() as db:
            job = Job(
                case_id=case_id or self.case_id,
                shuttle_name=shuttle.meta.name,
                shuttle_version=shuttle.meta.version,
                input_key=key,
                requested_by=self.editor.id,
                status=JobStatus.QUEUED,
                stats=empty_stats(),
            )
            db.add(job)
            await db.commit()
            return job.id

    async def job(self, job_id: uuid.UUID) -> Job:
        async with self.infra.sessionmaker() as db:
            job = await db.get(Job, job_id)
            assert job is not None
            return job

    async def events(self, job_id: uuid.UUID) -> list[AuditEvent]:
        async with self.infra.sessionmaker() as db:
            rows = await db.execute(
                select(AuditEvent)
                .where(AuditEvent.target_id == str(job_id))
                .order_by(AuditEvent.id)
            )
            return list(rows.scalars())

    async def finish_event(self, job_id: uuid.UUID) -> AuditEvent:
        events = [e for e in await self.events(job_id) if e.action == "job.finish"]
        assert len(events) == 1, [(e.action, e.details) for e in events]
        return events[0]

    async def set_case(self, case_id: uuid.UUID | None = None, **values: Any) -> None:
        async with self.infra.sessionmaker() as db:
            await db.execute(
                update(Case).where(Case.id == (case_id or self.case_id)).values(**values)
            )
            await db.commit()

    async def graph(self, case_id: uuid.UUID | None = None) -> tuple[set[str], set[str]]:
        snapshot = await GraphRepository(self.infra.graph).get_graph(case_id or self.case_id)
        return {n.key for n in snapshot.nodes}, {e.id for e in snapshot.edges}

    async def person_case(self) -> uuid.UUID:
        created = await self.owner_client.post(
            f"{PREFIX}/cases",
            json={**CASE, "allow_person_entities": True, "person_entities_reason": PERSON_REASON},
            headers=csrf(self.owner_client),
        )
        case_id = uuid.UUID(created.json()["id"])
        await self.add_member(case_id, self.editor, "editor")
        await self.add_entity(case_id, "domain", "www.shop.example.co.uk")
        return case_id

    async def add_member(self, case_id: uuid.UUID, user: User, role: str) -> None:
        response = await self.owner_client.put(
            f"{PREFIX}/cases/{case_id}/members",
            json={"email": user.email, "role": role},
            headers=csrf(self.owner_client),
        )
        assert response.status_code == 201

    async def add_entity(self, case_id: uuid.UUID, type_: str, value: str) -> None:
        response = await self.owner_client.post(
            f"{PREFIX}/cases/{case_id}/entities",
            json={"type": type_, "value": value},
            headers=csrf(self.owner_client),
        )
        assert response.status_code == 201, response.text


@pytest.fixture
async def world(
    infra: Infra, make_user: UserFactory, new_client: ClientFactory, db_app: FastAPI
) -> World:
    owner = await make_user("owner@example.org", Role.ANALYST)
    editor = await make_user("editor@example.org", Role.ANALYST)
    clients = []
    for user in (owner, editor):
        client = new_client()
        assert (await login(client, email=user.email)).status_code == 200
        clients.append(client)
    created = await clients[0].post(f"{PREFIX}/cases", json=CASE, headers=csrf(clients[0]))
    w = World(infra, uuid.UUID(created.json()["id"]), owner, editor, clients[0], clients[1])
    await w.add_member(w.case_id, editor, "editor")
    await w.add_entity(w.case_id, "domain", "www.shop.example.co.uk")
    return w


# --- happy path --------------------------------------------------------------------------


async def test_registrable_domain_job_end_to_end_through_the_api(
    world: World, db_app: FastAPI
) -> None:
    db_app.dependency_overrides[service.get_shuttle_registry] = lambda: ShuttleRegistry(
        [RegistrableDomain]
    )
    created = await world.editor_client.post(
        f"{PREFIX}/cases/{world.case_id}/jobs",
        json={"shuttle": "registrable-domain", "entity_key": SUBDOMAIN},
        headers=csrf(world.editor_client),
    )
    assert created.status_code == 201, created.text
    job_id = uuid.UUID(created.json()["id"])

    assert await run_job(job_id, world.config(RegistrableDomain)) == JobStatus.SUCCEEDED

    job = await world.job(job_id)
    assert job.status == JobStatus.SUCCEEDED
    assert job.error is None
    assert job.started_at is not None and job.finished_at is not None
    assert job.stats == {
        "entities_created": 1,
        "entities_updated": 0,
        "relationships_created": 1,
        "relationships_updated": 0,
        "findings_rejected": 0,
        "rejected_reasons": {},
        "entities_touched": ["domain:example.co.uk"],
        "notes": [],
    }
    nodes, edges = await world.graph()
    assert nodes == {"domain:example.co.uk", SUBDOMAIN}
    assert edges == {f"domain:example.co.uk|HAS_SUBDOMAIN|{SUBDOMAIN}"}
    parent = await GraphRepository(world.infra.graph).get_entity(
        world.case_id, "domain:example.co.uk"
    )
    assert parent is not None
    provenance = parent.entity.provenance
    assert provenance.sources == ["shuttle:registrable-domain@1.0.0"]
    assert (provenance.admiralty, provenance.max_confidence) == ("B1", 0.95)

    event = await world.finish_event(job_id)
    assert (event.outcome, event.details["status"]) == ("success", "succeeded")
    assert event.actor_label == "system:worker"
    assert event.details["stats"]["entities_created"] == 1
    # Entity keys never reach the audit log; counts do.
    assert "entities_touched" not in event.details["stats"]
    assert "example.co.uk" not in str(event.details["stats"])
    assert event.details["shuttle"] == "registrable-domain"

    # The job is visible through the API with its final state.
    shown = await world.owner_client.get(f"{PREFIX}/cases/{world.case_id}/jobs/{job_id}")
    assert shown.json()["status"] == "succeeded"
    assert shown.json()["stats"]["relationships_created"] == 1
    assert shown.json()["stats"]["entities_touched"] == ["domain:example.co.uk"]

    # Running it again merges observations instead of creating.
    again = await world.queue(RegistrableDomain)
    await run_job(again, world.config(RegistrableDomain))
    stats = (await world.job(again)).stats
    assert (stats["entities_updated"], stats["relationships_updated"]) == (1, 1)


async def test_finding_overrides_and_defaults(world: World) -> None:
    shuttle = scripted(
        [
            domain("example.co.uk", admiralty="A2", confidence=0.9, note="from the list"),
            domain("other.example.co.uk"),
            sub("example.co.uk", "www.shop.example.co.uk", confidence=0.7),
            sub("example.co.uk", "other.example.co.uk", admiralty="B3", note="reported"),
        ]
    )
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.SUCCEEDED
    repo = GraphRepository(world.infra.graph)
    first = await repo.get_entity(world.case_id, "domain:example.co.uk")
    second = await repo.get_entity(world.case_id, "domain:other.example.co.uk")
    assert first is not None and second is not None
    assert (first.entity.provenance.admiralty, first.entity.provenance.max_confidence) == (
        "A2",
        0.9,
    )
    assert first.entity.provenance.note == "from the list"
    assert (second.entity.provenance.admiralty, second.entity.provenance.max_confidence) == (
        "C3",
        0.6,
    )
    assert second.entity.provenance.sources == ["shuttle:scripted@1.0.0"]
    # Relationships take the same overrides, else the Shuttle's defaults.
    edges = {e.target_key: e.provenance for e in (await repo.get_graph(world.case_id)).edges}
    rated = edges["domain:other.example.co.uk"]
    assert (rated.admiralty, rated.max_confidence, rated.note) == ("B3", 0.6, "reported")
    plain = edges["domain:www.shop.example.co.uk"]
    assert (plain.admiralty, plain.max_confidence) == ("C3", 0.7)


# --- rejected findings -------------------------------------------------------------------


async def test_rejected_findings_are_counted_never_stored(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    shuttle = scripted(
        [
            domain(f"{SECRET}..invalid"),  # invalid_value
            EntityFinding(type=EntityType.IP_ADDRESS, value="192.0.2.1"),  # undeclared type
            domain("example.co.uk", properties={"secret": SECRET}),  # invalid_properties
            f"{SECRET}",  # not a finding at all
            RelationshipFinding(  # undeclared relationship type
                type=RelationType.HAS_MX,
                source=EntityRef(type=EntityType.DOMAIN, value="example.co.uk"),
                target=EntityRef(type=EntityType.DOMAIN, value="www.shop.example.co.uk"),
            ),
            RelationshipFinding(  # declared type, pair not allowed
                type=RelationType.HAS_EMAIL,
                source=EntityRef(type=EntityType.DOMAIN, value="example.co.uk"),
                target=EntityRef(type=EntityType.DOMAIN, value="www.shop.example.co.uk"),
            ),
            RelationshipFinding(  # an endpoint type the Shuttle never declared
                type=RelationType.HAS_SUBDOMAIN,
                source=EntityRef(type=EntityType.IP_ADDRESS, value="192.0.2.1"),
                target=EntityRef(type=EntityType.DOMAIN, value="www.shop.example.co.uk"),
            ),
            sub("missing.example.co.uk", "www.shop.example.co.uk"),  # endpoint_not_found
            sub("bad..value", "www.shop.example.co.uk"),  # invalid_value
            sub("www.shop.example.co.uk", "www.shop.example.co.uk"),  # same entity twice
            domain("example.co.uk"),  # the one good finding
        ],
        output_relationship_types={RelationType.HAS_SUBDOMAIN, RelationType.HAS_EMAIL},
        output_entity_types={EntityType.DOMAIN, EntityType.EMAIL},
        sensitivity=Sensitivity.PERSON,
    )
    # The Shuttle is person-sensitive, so it needs a person case to start.
    case_id = await world.person_case()
    job_id = await world.queue(shuttle, case_id=case_id)
    with caplog.at_level(logging.INFO):
        assert await run_job(job_id, world.config(shuttle)) == JobStatus.SUCCEEDED

    job = await world.job(job_id)
    assert job.stats["entities_created"] == 1
    assert job.stats["findings_rejected"] == 10
    assert job.stats["rejected_reasons"] == {
        "endpoint_not_found": 1,
        "invalid_properties": 1,
        "invalid_value": 2,
        "not_a_finding": 1,
        "relationship_not_allowed": 2,
        "undeclared_entity_type": 2,
        "undeclared_relationship_type": 1,
    }
    nodes, edges = await world.graph(case_id)
    assert nodes == {"domain:example.co.uk", SUBDOMAIN}
    assert edges == set()
    event = await world.finish_event(job_id)
    assert SECRET not in str(event.details)
    assert SECRET not in str(job.stats)
    assert SECRET not in caplog.text
    assert "rejected a finding (invalid_value)" in caplog.text


async def test_person_findings_rejected_once_the_case_stops_allowing_them(world: World) -> None:
    case_id = await world.person_case()

    async def gate_off() -> None:
        await world.set_case(case_id, allow_person_entities=False)

    shuttle = scripted(
        [
            domain("example.co.uk"),
            gate_off,
            EntityFinding(type=EntityType.EMAIL, value="alex@example.co.uk"),
            RelationshipFinding(
                type=RelationType.HAS_EMAIL,
                source=EntityRef(type=EntityType.DOMAIN, value="example.co.uk"),
                target=EntityRef(type=EntityType.EMAIL, value="alex@example.co.uk"),
            ),
            domain("other.example.co.uk"),
        ],
        output_entity_types={EntityType.DOMAIN, EntityType.EMAIL},
        output_relationship_types={RelationType.HAS_EMAIL},
        sensitivity=Sensitivity.PERSON,
    )
    job_id = await world.queue(shuttle, case_id=case_id)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.SUCCEEDED
    job = await world.job(job_id)
    assert job.stats["rejected_reasons"] == {"person_entities_disabled": 2}
    assert job.stats["entities_created"] == 2
    nodes, _ = await world.graph(case_id)
    assert not any(key.startswith("email:") for key in nodes)


# --- limits, cancellation, timeouts, errors --------------------------------------------


async def test_too_many_findings_stops_the_job(world: World) -> None:
    shuttle = scripted([domain(f"host{i}.example.co.uk") for i in range(10)])
    job_id = await world.queue(shuttle)
    status = await run_job(job_id, world.config(shuttle, max_findings=4))
    assert status == JobStatus.FAILED
    job = await world.job(job_id)
    assert job.error is not None and "more than 4 findings" in job.error
    assert job.stats["entities_created"] == 4
    event = await world.finish_event(job_id)
    assert (event.outcome, event.details["reason"]) == ("failure", "too_many_findings")


def test_default_cap() -> None:
    assert runner.MAX_FINDINGS_PER_JOB == 5_000


async def test_cancellation_between_findings(world: World) -> None:
    job_holder: dict[str, uuid.UUID] = {}

    async def request_cancel() -> None:
        response = await world.editor_client.post(
            f"{PREFIX}/cases/{world.case_id}/jobs/{job_holder['id']}/cancel",
            headers=csrf(world.editor_client),
        )
        assert response.json()["status"] == "running"

    async def wait_forever() -> None:
        await asyncio.sleep(60)

    shuttle = scripted(
        [domain("example.co.uk"), request_cancel, wait_forever, domain("x.example.co.uk")]
    )
    job_id = job_holder["id"] = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.CANCELLED
    job = await world.job(job_id)
    assert job.stats["entities_created"] == 1
    assert job.error is None
    actions = [(e.action, e.outcome) for e in await world.events(job_id)]
    assert actions == [("job.cancel", "success"), ("job.finish", "success")]
    assert (await world.finish_event(job_id)).details["status"] == "cancelled"


async def test_shuttle_can_check_cancellation_itself(world: World) -> None:
    class Polite(Shuttle):
        meta = meta("polite")

        async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Any]:
            async with world.infra.sessionmaker() as db:
                await db.execute(update(Job).values(cancel_requested=True))
                await db.commit()
            while not ctx.cancelled:  # noqa: ASYNC110 - the polling API Shuttles get
                await asyncio.sleep(0.01)
            ctx.raise_if_cancelled()
            yield domain("never.example.co.uk")

    job_id = await world.queue(Polite)
    assert await run_job(job_id, world.config(Polite)) == JobStatus.CANCELLED


async def test_timeout(world: World) -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    shuttle = scripted([domain("example.co.uk"), hang], timeout_seconds=1)
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    job = await world.job(job_id)
    assert job.error == "The Shuttle did not finish within 1 seconds."
    assert job.stats["entities_created"] == 1
    assert (await world.finish_event(job_id)).details["reason"] == "timeout"


async def test_shuttle_exceptions_are_never_stored_raw(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    async def explode() -> None:
        raise RuntimeError(f"upstream said: token={SECRET}")

    shuttle = scripted([domain("example.co.uk"), explode])
    job_id = await world.queue(shuttle)
    with caplog.at_level(logging.DEBUG):
        assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    job = await world.job(job_id)
    assert job.error == runner.ERROR_SHUTTLE
    event = await world.finish_event(job_id)
    assert event.details["reason"] == "shuttle_error"
    for text_ in (job.error, str(job.stats), str(event.details), caplog.text):
        assert SECRET not in text_
        assert "Traceback" not in text_
    assert "the Shuttle raised RuntimeError" in caplog.text


async def test_refused_inputs_fail_with_the_fixed_message(world: World) -> None:
    async def refuse() -> None:
        raise InputRefusedError(RefusalReason.INTERNAL_NAME)

    shuttle = scripted([refuse])
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    job = await world.job(job_id)
    assert job.error == REFUSAL_MESSAGES[RefusalReason.INTERNAL_NAME]
    assert job.stats["entities_created"] == 0
    event = await world.finish_event(job_id)
    assert (event.outcome, event.details["reason"]) == ("failure", "internal_name")


async def test_known_failures_keep_findings_and_notes(world: World) -> None:
    class Noting(Shuttle):
        meta = meta("noting")

        async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Any]:
            ctx.note("Expired certificates were excluded.")
            yield domain("example.co.uk")
            ctx.note(f"token={SECRET}")  # a careless note: kept for users, never audited
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)

    job_id = await world.queue(Noting)
    assert await run_job(job_id, world.config(Noting)) == JobStatus.FAILED
    job = await world.job(job_id)
    assert job.error == FAILURE_MESSAGES[FailureReason.SOURCE_UNAVAILABLE]
    assert job.stats["entities_created"] == 1
    assert job.stats["notes"] == ["Expired certificates were excluded.", f"token={SECRET}"]
    event = await world.finish_event(job_id)
    assert (event.outcome, event.details["reason"]) == ("failure", "source_unavailable")
    assert "notes" not in event.details["stats"]
    assert SECRET not in str(event.details)
    shown = await world.owner_client.get(f"{PREFIX}/cases/{world.case_id}/jobs/{job_id}")
    assert shown.json()["stats"]["notes"][0] == "Expired certificates were excluded."


async def test_touched_entities_are_capped(world: World) -> None:
    shuttle = scripted([domain(f"n{i}.example.co.uk") for i in range(MAX_TOUCHED_KEYS + 20)])
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.SUCCEEDED
    job = await world.job(job_id)
    touched = job.stats["entities_touched"]
    assert len(touched) == MAX_TOUCHED_KEYS == 200
    assert touched[:2] == ["domain:n0.example.co.uk", "domain:n1.example.co.uk"]
    assert job.stats["entities_created"] == MAX_TOUCHED_KEYS + 20


class _FakeDns:
    """Fixed answers for www.shop.example.co.uk (a CNAME) and its target."""

    def answer(self, name: str, rdtype: dns.rdatatype.RdataType, *texts: str) -> Answer:
        records = tuple(dns.rdata.from_text(dns.rdataclass.IN, rdtype, t) for t in texts)
        return Answer(name=name, records=records)

    async def query(self, name: str, rdtype: dns.rdatatype.RdataType) -> Answer | None:
        types = dns.rdatatype.RdataType
        table = {
            ("www.shop.example.co.uk", types.CNAME): (
                "www.shop.example.co.uk",
                "shop.example.net.",
            ),
            ("www.shop.example.co.uk", types.A): ("shop.example.net", "192.0.2.80", "10.0.0.8"),
            ("www.shop.example.co.uk", types.TXT): ("www.shop.example.co.uk", '"v=spf1 -all"'),
        }
        row = table.get((name, rdtype))
        return self.answer(row[0], rdtype, *row[1:]) if row else None


async def test_dns_shuttle_through_the_runner(world: World) -> None:
    """The DNS Shuttle's findings (with a fake resolver) pass every runner check."""

    class FakeResolvedDns(DnsShuttle):
        def __init__(self) -> None:
            super().__init__(client=_FakeDns(), config=DnsConfig())

    job_id = await world.queue(FakeResolvedDns)
    assert await run_job(job_id, world.config(FakeResolvedDns)) == JobStatus.SUCCEEDED
    job = await world.job(job_id)
    assert job.stats["findings_rejected"] == 0, job.stats
    assert set(job.stats["entities_touched"]) == {
        "domain:shop.example.net",
        "ip_address:192.0.2.80",
        "ip_address:10.0.0.8",
        SUBDOMAIN,
    }
    _, edges = await world.graph()
    assert edges == {
        f"{SUBDOMAIN}|ALIAS_OF|domain:shop.example.net",
        "domain:shop.example.net|RESOLVES_TO|ip_address:192.0.2.80",
        "domain:shop.example.net|RESOLVES_TO|ip_address:10.0.0.8",
    }
    repo = GraphRepository(world.infra.graph)
    private = await repo.get_entity(world.case_id, "ip_address:10.0.0.8")
    assert private is not None
    assert private.entity.properties == {"scope": "private"}
    assert private.entity.provenance.note is not None
    assert "never contacted" in private.entity.provenance.note
    origin = await repo.get_entity(world.case_id, SUBDOMAIN)
    assert origin is not None
    assert origin.entity.properties == {"dns_txt": ["v=spf1 -all"], "dns_spf": ["v=spf1 -all"]}
    assert origin.entity.provenance.sources == ["manual", "shuttle:dns@1.0.0"]


def _test_certificate(names: list[str]) -> x509.Certificate:
    """A throwaway self-signed certificate (never a real one)."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    start = datetime(2026, 9, 1, tzinfo=UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(0x1234)
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=90))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(n) for n in names] + [x509.RFC822Name("ops@example.co.uk")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )


async def test_crtsh_shuttle_through_the_runner(world: World) -> None:
    """The crt.sh Shuttle's findings (crt.sh faked at the HTTP level) pass every runner check."""
    scope = "www.shop.example.co.uk"
    names = [scope, f"api.{scope}", f"*.cdn.{scope}", "other.example.net"]
    certificate = _test_certificate(names)
    row = {
        "id": 77,
        "common_name": scope,
        "name_value": "\n".join([*names, "ops@example.co.uk"]),
        "not_before": "2026-09-01T00:00:00",
        "not_after": "2026-11-30T00:00:00",
    }

    def crtsh(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "crt.sh"
        params = request.url.params
        if "d" in params:
            return httpx.Response(200, content=certificate.public_bytes(Encoding.PEM))
        return httpx.Response(200, json=[row] if params["q"].startswith("%.") else [])

    now = [0.0]

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    def clock() -> float:
        return now[0]

    meta_ = CrtShShuttle.meta
    assert meta_.rate_limit is not None
    http = create_http_client(
        meta_,
        transport=httpx.MockTransport(crtsh),
        limiter=HostRateLimiter(meta_.rate_limit, clock=clock, sleep=sleep),
    )

    class FakeCrtSh(CrtShShuttle):
        def __init__(self) -> None:
            super().__init__(http=http, sleep=sleep, clock=clock)

    try:
        job_id = await world.queue(FakeCrtSh)
        assert await run_job(job_id, world.config(FakeCrtSh)) == JobStatus.SUCCEEDED
    finally:
        await http.aclose()
    job = await world.job(job_id)
    assert job.stats["findings_rejected"] == 0, job.stats
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    nodes, edges = await world.graph()
    assert nodes == {
        SUBDOMAIN,
        f"domain:api.{scope}",
        f"domain:cdn.{scope}",
        f"certificate:{fingerprint}",
    }
    assert edges == {
        f"{SUBDOMAIN}|HAS_SUBDOMAIN|domain:api.{scope}",
        f"{SUBDOMAIN}|HAS_SUBDOMAIN|domain:cdn.{scope}",
        f"certificate:{fingerprint}|COVERS|{SUBDOMAIN}",
        f"certificate:{fingerprint}|COVERS|domain:api.{scope}",
        f"certificate:{fingerprint}|COVERS|domain:cdn.{scope}",
    }
    assert job.stats["notes"] == [
        "Dropped 2 name(s): 1 email addresses, 1 outside the searched domain."
    ]
    repo = GraphRepository(world.infra.graph)
    stored = await repo.get_entity(world.case_id, f"certificate:{fingerprint}")
    assert stored is not None
    assert stored.entity.properties["crtsh_id"] == 77
    assert stored.entity.properties["self_signed"] is True
    assert stored.entity.properties["san_count"] == 5
    assert stored.entity.provenance.sources == ["shuttle:crtsh@1.0.0"]
    event = await world.finish_event(job_id)
    assert "notes" not in event.details["stats"]


async def test_graph_outage_fails_with_a_safe_message(world: World) -> None:
    shuttle = scripted([domain("example.co.uk")])
    job_id = await world.queue(shuttle)
    driver = graph_infra.create_driver(Settings(env="test", neo4j_uri="bolt://127.0.0.1:1"))
    try:
        config = world.config(shuttle, graph=GraphRepository(driver))
        assert await run_job(job_id, config) == JobStatus.FAILED
    finally:
        await driver.close()
    job = await world.job(job_id)
    assert job.error == runner.ERROR_GRAPH


async def test_internal_errors_fail_with_a_safe_message(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> None:
        raise ValueError(f"internal detail {SECRET}")

    monkeypatch.setattr(runner.JobRun, "check_start", broken)
    shuttle = scripted([])
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    job = await world.job(job_id)
    assert job.error == runner.ERROR_INTERNAL
    assert SECRET not in str((await world.finish_event(job_id)).details)


# --- guardrails re-checked at execution time ----------------------------------------------


async def expect_denied(world: World, job_id: uuid.UUID, reason: str, message: str) -> None:
    job = await world.job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.error == message
    event = await world.finish_event(job_id)
    assert (event.outcome, event.details["reason"]) == ("denied", reason)


async def test_case_closed_after_queueing(world: World) -> None:
    shuttle = scripted([domain("example.co.uk")])
    job_id = await world.queue(shuttle)
    await world.set_case(status=CaseStatus.CLOSED, closed_at=clock.utcnow())
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    await expect_denied(world, job_id, "case_not_open", service.ERROR_CASE_NOT_OPEN)
    nodes, _ = await world.graph()
    assert nodes == {SUBDOMAIN}


async def test_requester_removed_after_queueing(world: World) -> None:
    shuttle = scripted([domain("example.co.uk")])
    job_id = await world.queue(shuttle)
    async with world.infra.sessionmaker() as db:
        await db.execute(delete(CaseMember).where(CaseMember.user_id == world.editor.id))
        await db.commit()
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    await expect_denied(world, job_id, "requester_not_allowed", service.ERROR_NOT_ALLOWED)


async def test_requester_deactivated_or_demoted(world: World) -> None:
    shuttle = scripted([domain("example.co.uk")])
    for change in ({"is_active": False}, {"role": Role.VIEWER}):
        job_id = await world.queue(shuttle)
        async with world.infra.sessionmaker() as db:
            await db.execute(update(User).where(User.id == world.editor.id).values(**change))
            await db.commit()
        assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
        await expect_denied(world, job_id, "requester_not_allowed", service.ERROR_NOT_ALLOWED)
        async with world.infra.sessionmaker() as db:
            await db.execute(
                update(User)
                .where(User.id == world.editor.id)
                .values(is_active=True, role=Role.ANALYST)
            )
            await db.commit()


async def test_person_gate_turned_off_after_queueing(world: World) -> None:
    shuttle = scripted(
        [],
        output_entity_types={EntityType.EMAIL},
        output_relationship_types={RelationType.HAS_EMAIL},
        sensitivity=Sensitivity.PERSON,
    )
    case_id = await world.person_case()
    job_id = await world.queue(shuttle, case_id=case_id)
    await world.set_case(case_id, allow_person_entities=False)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    await expect_denied(world, job_id, "person_entities_disabled", service.ERROR_PERSON)


async def test_shuttle_became_active(world: World) -> None:
    passive = scripted([domain("example.co.uk")])
    active = scripted([domain("example.co.uk")], mode=Mode.ACTIVE)
    job_id = await world.queue(passive)
    assert await run_job(job_id, world.config(active)) == JobStatus.FAILED
    await expect_denied(world, job_id, "active_shuttle", service.ERROR_ACTIVE)


async def test_input_type_no_longer_accepted(world: World) -> None:
    before = scripted([])
    after = scripted([], input_types={EntityType.IP_ADDRESS}, output_relationship_types=set())
    job_id = await world.queue(before)
    assert await run_job(job_id, world.config(after)) == JobStatus.FAILED
    event = await world.finish_event(job_id)
    assert (event.outcome, event.details["reason"]) == ("denied", "input_type_not_accepted")


async def test_shuttle_uninstalled(world: World) -> None:
    shuttle = scripted([])
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config()) == JobStatus.FAILED
    job = await world.job(job_id)
    assert job.error == service.ERROR_NOT_INSTALLED
    assert (await world.finish_event(job_id)).details["reason"] == "not_installed"


async def test_input_entity_deleted(world: World) -> None:
    shuttle = scripted([])
    job_id = await world.queue(shuttle)
    await GraphRepository(world.infra.graph).delete_entity(world.case_id, SUBDOMAIN)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    assert (await world.job(job_id)).error == service.ERROR_ENTITY_GONE


async def test_case_closed_while_running_stops_the_job(world: World) -> None:
    async def close() -> None:
        await world.set_case(status=CaseStatus.CLOSED, closed_at=clock.utcnow())

    shuttle = scripted([domain("example.co.uk"), close, domain("late.example.co.uk")])
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    await expect_denied(world, job_id, "case_not_open", service.ERROR_CASE_NOT_OPEN)
    job = await world.job(job_id)
    assert job.stats["entities_created"] == 1
    nodes, _ = await world.graph()
    assert "domain:late.example.co.uk" not in nodes


async def test_new_version_runs_and_is_recorded(world: World) -> None:
    old = scripted([domain("example.co.uk")])
    new = scripted([domain("example.co.uk")], version="1.1.0")
    job_id = await world.queue(old)
    assert await run_job(job_id, world.config(new)) == JobStatus.SUCCEEDED
    assert (await world.job(job_id)).shuttle_version == "1.1.0"
    repo = GraphRepository(world.infra.graph)
    entity = await repo.get_entity(world.case_id, "domain:example.co.uk")
    assert entity is not None
    assert entity.entity.provenance.sources == ["shuttle:scripted@1.1.0"]


# --- job states the runner skips or repairs ----------------------------------------------


async def test_unknown_and_already_cancelled_jobs_are_skipped(world: World) -> None:
    shuttle = scripted([domain("example.co.uk")])
    assert await run_job(uuid.uuid4(), world.config(shuttle)) is None

    job_id = await world.queue(shuttle)
    await world.editor_client.post(
        f"{PREFIX}/cases/{world.case_id}/jobs/{job_id}/cancel", headers=csrf(world.editor_client)
    )
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.CANCELLED
    assert [e.action for e in await world.events(job_id)] == ["job.cancel"]
    nodes, _ = await world.graph()
    assert nodes == {SUBDOMAIN}

    # A duplicate delivery of a finished job does nothing either.
    done = await world.queue(shuttle)
    await run_job(done, world.config(shuttle))
    assert await run_job(done, world.config(shuttle)) == JobStatus.SUCCEEDED
    assert len([e for e in await world.events(done) if e.action == "job.finish"]) == 1


async def test_redelivered_running_job_is_failed(world: World) -> None:
    shuttle = scripted([domain("example.co.uk")])
    job_id = await world.queue(shuttle)
    async with world.infra.sessionmaker() as db:
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status=JobStatus.RUNNING, started_at=clock.utcnow())
        )
        await db.commit()
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    assert (await world.job(job_id)).error == runner.ERROR_WORKER_LOST


async def test_reaper_fails_abandoned_and_lost_jobs(world: World) -> None:
    shuttle = scripted([])
    running = await world.queue(shuttle)
    queued = await world.queue(shuttle)
    fresh = await world.queue(shuttle)
    long_ago = clock.utcnow() - timedelta(days=2)
    async with world.infra.sessionmaker() as db:
        await db.execute(
            update(Job)
            .where(Job.id == running)
            .values(status=JobStatus.RUNNING, started_at=long_ago)
        )
        await db.execute(update(Job).where(Job.id == queued).values(created_at=long_ago))
        await db.commit()
    assert await reap_stale_jobs(world.config(shuttle)) == 2
    assert (await world.job(running)).error == runner.ERROR_WORKER_LOST
    assert (await world.job(queued)).error == runner.ERROR_NOT_PICKED_UP
    assert (await world.job(fresh)).status == JobStatus.QUEUED


async def test_job_of_a_deleted_case_finishes_quietly(world: World) -> None:
    async def delete_case() -> None:
        async with world.infra.sessionmaker() as db:
            await db.execute(text("DELETE FROM cases WHERE id = :id"), {"id": world.case_id})
            await db.commit()

    shuttle = scripted([delete_case])
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.SUCCEEDED
    async with world.infra.sessionmaker() as db:
        assert await db.get(Job, job_id) is None
