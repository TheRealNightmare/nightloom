"""The job runner against PostgreSQL and Neo4j, with scripted fake Shuttles."""

import asyncio
import logging
import os
import signal
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import dns.rdata
import dns.rdataclass
import dns.rdatatype
import httpx
import pytest
from billiard.exceptions import WorkerLostError
from celery.exceptions import TimeLimitExceeded
from celery.signals import task_failure
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
from nightloom_api.config import Settings, get_settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra import Infra
from nightloom_api.infra import graph as graph_infra
from nightloom_api.jobs import result_cache, service
from nightloom_api.jobs.models import Job, JobStatus, empty_stats
from nightloom_api.ownership.models import OwnershipVerification
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
from nightloom_sdk.evidence import MemoryEvidenceStore
from nightloom_sdk.http import create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.shuttle import FAILURE_MESSAGES, REFUSAL_MESSAGES
from nightloom_shuttle_crtsh import CrtShShuttle
from nightloom_shuttle_dns import DnsShuttle
from nightloom_shuttle_dns.client import Answer
from nightloom_shuttle_dns.config import DnsConfig
from nightloom_shuttle_registrable_domain import RegistrableDomain
from nightloom_worker import celery_app, runner
from nightloom_worker.runner import MAX_TOUCHED_KEYS, RunnerConfig, reap_stale_jobs, run_job
from support import PREFIX, TEST_DATABASE_URL, TEST_VALKEY_URL, csrf, graph_test_settings, login

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
        "entities_new": ["domain:example.co.uk"],
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
    assert "entities_new" not in event.details["stats"]
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
    # Only what a job created counts as new (CT monitoring alerts on it).
    assert stats["entities_touched"] == ["domain:example.co.uk"]
    assert stats["entities_new"] == []


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
    await expect_denied(world, job_id, "target_not_verified", service.ERROR_ACTIVE)


async def _verify(world: World, domain: str, addresses: list[str]) -> None:
    now = clock.utcnow()
    async with world.infra.sessionmaker() as db:
        db.add(
            OwnershipVerification(
                case_id=world.case_id,
                domain=domain,
                method="dns_txt",
                token="t",
                status="verified",
                checked_at=now,
                verified_at=now,
                expires_at=now + timedelta(days=30),
                addresses=addresses,
            )
        )
        await db.commit()


class _Addresses:
    """Stands in for the ownership prover: a domain's addresses now (None: no answer)."""

    def __init__(self, found: tuple[str, ...] | None) -> None:
        self.found = found
        self.asked: list[str] = []

    async def addresses(self, domain: str) -> tuple[str, ...] | None:
        self.asked.append(domain)
        return self.found


async def test_site_visits_need_the_cases_permission_or_a_verified_domain(world: World) -> None:
    visit = scripted([domain("example.co.uk")], mode=Mode.VISIT)
    job_id = await world.queue(visit)
    assert await run_job(job_id, world.config(visit)) == JobStatus.FAILED
    await expect_denied(world, job_id, "site_visits_disabled", service.ERROR_VISIT)
    await world.set_case(world.case_id, allow_site_visits=True)
    job_id = await world.queue(visit)
    assert await run_job(job_id, world.config(visit)) == JobStatus.SUCCEEDED
    await world.set_case(world.case_id, allow_site_visits=False)
    await _verify(world, "example.co.uk", [])
    job_id = await world.queue(visit)
    assert await run_job(job_id, world.config(visit)) == JobStatus.SUCCEEDED


async def test_active_shuttles_run_on_verified_domains(world: World) -> None:
    active = scripted([domain("example.co.uk")], mode=Mode.ACTIVE)
    await _verify(world, "shop.example.co.uk", [])
    job_id = await world.queue(active)
    assert await run_job(job_id, world.config(active)) == JobStatus.SUCCEEDED


async def test_an_address_is_confirmed_by_resolving_its_domain_again(world: World) -> None:
    active = scripted(
        [],
        input_types={EntityType.IP_ADDRESS},
        output_relationship_types=set(),
        mode=Mode.ACTIVE,
    )
    await _verify(world, "shop.example.co.uk", ["192.0.2.80"])
    await world.add_entity(world.case_id, "ip_address", "192.0.2.80")
    still = _Addresses(("192.0.2.80",))
    job_id = await world.queue(active, "ip_address:192.0.2.80")
    assert await run_job(job_id, world.config(active, prover=still)) == JobStatus.SUCCEEDED
    assert still.asked == ["shop.example.co.uk"]
    for prover in (_Addresses(("192.0.2.81",)), _Addresses(None), None):
        job_id = await world.queue(active, "ip_address:192.0.2.80")
        assert await run_job(job_id, world.config(active, prover=prover)) == JobStatus.FAILED
        await expect_denied(world, job_id, "target_not_verified", service.ERROR_ACTIVE)


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
    assert await reap_stale_jobs(world.infra.sessionmaker, ShuttleRegistry([shuttle])) == 2
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


# --- deadlines: every job reaches a final status ---------------------------------------------
#
# Each test is bounded from outside, so a regression fails instead of hanging.

GRACE = 2.0  # the finish grace these tests give the runner (seconds)
CLEANUP = 0.5  # each cleanup step's own limit (seconds)


@pytest.fixture
def short_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "FINISH_GRACE_SECONDS", GRACE, raising=False)
    monkeypatch.setattr(runner, "CLEANUP_SECONDS", CLEANUP, raising=False)


def task_environment() -> dict[str, str]:
    """The NIGHTLOOM_* variables that point the real task at the test databases."""
    graph = graph_test_settings()
    return {
        "NIGHTLOOM_DATABASE_URL": TEST_DATABASE_URL,
        "NIGHTLOOM_NEO4J_URI": graph.neo4j_uri,
        "NIGHTLOOM_NEO4J_USER": graph.neo4j_user,
        "NIGHTLOOM_NEO4J_PASSWORD": graph.neo4j_password.get_secret_value(),
        "NIGHTLOOM_VALKEY_URL": TEST_VALKEY_URL,
    }


@pytest.fixture
def task_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the real Celery task (its own engine and driver) at the test databases."""
    for name, value in task_environment().items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def run_task_in_thread(job_id: uuid.UUID, limit: float) -> tuple[bool, float]:
    """Run the Celery task in a thread; (whether it returned within ``limit``, seconds taken)."""
    began = time.monotonic()
    thread = threading.Thread(target=celery_app.run_job_task.run, args=(str(job_id),), daemon=True)
    thread.start()
    await asyncio.to_thread(thread.join, limit)
    return not thread.is_alive(), time.monotonic() - began


async def test_loop_blocked_past_the_timeout_ends_at_the_soft_limit(world: World) -> None:
    """(a) The loop is blocked in a callback when the soft limit's signal arrives.

    A real process, in its main thread, with billiard's own SIGUSR1 handler:
    the job must end as failed by the time limit, never be left running.
    """
    shuttle = scripted([], name="limits", timeout_seconds=1)
    job_id = await world.queue(shuttle)
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("limits_child.py")),
        str(job_id),
        "block",
        env={**os.environ, **task_environment()},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert child.stdout is not None
        assert (await asyncio.wait_for(child.stdout.readline(), 60)).strip() == b"ready"
        await asyncio.sleep(1.5)  # the job runs; its loop is blocked until about 3 s
        child.send_signal(signal.SIGUSR1)  # what billiard sends at the soft limit
        _, stderr = await asyncio.wait_for(child.communicate(), 3 + GRACE + 30)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    job = await world.job(job_id)
    assert job.status == JobStatus.FAILED, stderr.decode()[-2000:]
    assert job.error == runner.ERROR_TIME_LIMIT, stderr.decode()[-2000:]
    assert (await world.finish_event(job_id)).details["reason"] == "worker_time_limit"


async def test_hung_executor_job_does_not_keep_the_task_alive(
    world: World, monkeypatch: pytest.MonkeyPatch, task_settings: None, short_limits: None
) -> None:
    """(b) A thread that never returns (a stuck getaddrinfo) must not hold the task open."""
    stuck = threading.Event()

    async def hung_lookup() -> None:
        await asyncio.get_running_loop().run_in_executor(None, stuck.wait)

    shuttle = scripted([hung_lookup], timeout_seconds=1)
    monkeypatch.setattr(celery_app, "default_registry", lambda: ShuttleRegistry([shuttle]))
    job_id = await world.queue(shuttle)
    try:
        returned, took = await run_task_in_thread(job_id, 1 + GRACE + 5)
    finally:
        stuck.set()
    assert returned, f"the task was still running after {took:.1f} s"
    job = await world.job(job_id)
    assert job.status == JobStatus.FAILED
    assert (await world.finish_event(job_id)).details["reason"] == "timeout"


async def test_shuttle_ignoring_cancellation_once_still_ends(
    world: World, short_limits: None
) -> None:
    """(c) The Shuttle swallows the first CancelledError and keeps waiting."""

    async def stubborn() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(30)

    shuttle = scripted([domain("example.co.uk"), stubborn], timeout_seconds=1)
    job_id = await world.queue(shuttle)
    began = time.monotonic()
    status = await asyncio.wait_for(run_job(job_id, world.config(shuttle)), 1 + GRACE + 5)
    assert status == JobStatus.FAILED
    assert time.monotonic() - began < 1 + GRACE + 1
    job = await world.job(job_id)
    assert job.error == "The Shuttle did not finish within 1 seconds."
    assert job.stats["entities_created"] == 1


async def test_hanging_context_close_still_ends(
    world: World, monkeypatch: pytest.MonkeyPatch, short_limits: None
) -> None:
    """(d) Closing the Shuttle's context (its HTTP client) never returns."""

    async def hang(self: ShuttleContext) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(ShuttleContext, "aclose", hang)
    shuttle = scripted([domain("example.co.uk")])
    job_id = await world.queue(shuttle)
    status = await asyncio.wait_for(run_job(job_id, world.config(shuttle)), 1 + GRACE + 5)
    assert status == JobStatus.SUCCEEDED
    assert (await world.job(job_id)).status == JobStatus.SUCCEEDED


async def mark_running(world: World, job_id: uuid.UUID, started_at: datetime) -> None:
    async with world.infra.sessionmaker() as db:
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status=JobStatus.RUNNING, started_at=started_at)
        )
        await db.commit()


@pytest.mark.parametrize(
    ("exception", "error", "reason"),
    [
        (TimeLimitExceeded(390), "ERROR_TIME_LIMIT", "worker_time_limit"),
        (WorkerLostError("signal 9 (SIGKILL)"), "ERROR_WORKER_LOST", "worker_lost"),
    ],
)
async def test_killed_worker_process_is_recorded_by_the_main_process(
    world: World, task_settings: None, exception: Exception, error: str, reason: str
) -> None:
    """(e) The pool child was killed mid-run: Celery's main process sends task_failure."""
    shuttle = scripted([])
    job_id = await world.queue(shuttle)
    await mark_running(world, job_id, clock.utcnow())

    def send() -> None:
        task_failure.send(
            sender=celery_app.run_job_task,
            task_id=str(job_id),
            exception=exception,
            args=[str(job_id)],
            kwargs={},
            traceback=None,
            einfo=None,
        )

    await asyncio.wait_for(asyncio.to_thread(send), 30)
    job = await world.job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.error == getattr(runner, error)
    assert (await world.finish_event(job_id)).details["reason"] == reason


# --- the reaper: per-job staleness, idempotent, race-safe --------------------------------------


async def test_reaper_uses_each_jobs_own_shuttle_timeout(world: World) -> None:
    short = scripted([], name="short", timeout_seconds=10)
    long = scripted([], name="long", timeout_seconds=600)
    registry = ShuttleRegistry([short, long])
    now = clock.utcnow()
    # short: deadline 10 s, hard limit 10 + 90 s, stale 60 s after that = 160 s.
    stale_short = await world.queue(short)
    await mark_running(world, stale_short, now - timedelta(seconds=161))
    fresh_short = await world.queue(short)
    await mark_running(world, fresh_short, now - timedelta(seconds=150))
    # long: stale after 600 + 90 + 60 = 750 s.
    fresh_long = await world.queue(long)
    await mark_running(world, fresh_long, now - timedelta(seconds=700))
    stale_long = await world.queue(long)
    await mark_running(world, stale_long, now - timedelta(seconds=751))
    # A Shuttle that is not installed any more: the largest timeout (600 s).
    gone = scripted([], name="uninstalled", timeout_seconds=1)
    fresh_unknown = await world.queue(gone)
    await mark_running(world, fresh_unknown, now - timedelta(seconds=700))

    assert await reap_stale_jobs(world.infra.sessionmaker, registry) == 2
    statuses = {
        name: (await world.job(job_id)).status
        for name, job_id in {
            "stale_short": stale_short,
            "fresh_short": fresh_short,
            "fresh_long": fresh_long,
            "stale_long": stale_long,
            "fresh_unknown": fresh_unknown,
        }.items()
    }
    assert statuses == {
        "stale_short": JobStatus.FAILED,
        "fresh_short": JobStatus.RUNNING,
        "fresh_long": JobStatus.RUNNING,
        "stale_long": JobStatus.FAILED,
        "fresh_unknown": JobStatus.RUNNING,
    }
    event = await world.finish_event(stale_short)
    assert (event.details["reason"], event.actor_label) == ("worker_lost", "system:worker")
    # Reaping again changes nothing and records nothing more.
    assert await reap_stale_jobs(world.infra.sessionmaker, registry) == 0
    await world.finish_event(stale_short)  # still exactly one


async def test_reaper_and_a_finishing_run_never_both_finish_a_job(world: World) -> None:
    shuttle = scripted([], timeout_seconds=1)
    job_id = await world.queue(shuttle)
    await mark_running(world, job_id, clock.utcnow() - timedelta(days=1))

    # The run records its outcome between the reaper's read and its update.
    original = service.finish_job_if
    raced = False

    async def finish_first(db: Any, job_id_: uuid.UUID, **kwargs: Any) -> Any:
        nonlocal raced
        if not raced:
            raced = True
            async with world.infra.sessionmaker() as other:
                await original(
                    other,
                    job_id_,
                    only_if={JobStatus.RUNNING},
                    status=JobStatus.SUCCEEDED,
                    actor=runner.WORKER_ACTOR,
                )
        return await original(db, job_id_, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service, "finish_job_if", finish_first)
        assert await reap_stale_jobs(world.infra.sessionmaker, ShuttleRegistry([shuttle])) == 0
    job = await world.job(job_id)
    assert (job.status, job.error) == (JobStatus.SUCCEEDED, None)
    assert (await world.finish_event(job_id)).details["status"] == "succeeded"


async def test_finish_abandoned_only_fails_running_jobs(world: World) -> None:
    shuttle = scripted([])
    done = await world.queue(shuttle)
    assert await run_job(done, world.config(shuttle)) == JobStatus.SUCCEEDED
    outcome = runner.Outcome(JobStatus.FAILED, error=runner.ERROR_TIME_LIMIT, reason="x")
    assert await runner.finish_abandoned(world.infra.sessionmaker, done, outcome) is False
    assert (await world.job(done)).status == JobStatus.SUCCEEDED
    assert await runner.finish_abandoned(world.infra.sessionmaker, uuid.uuid4(), outcome) is False


async def test_an_unanswering_database_leaves_the_job_to_the_reaper(
    world: World, caplog: pytest.LogCaptureFixture, short_limits: None
) -> None:
    """Recording the outcome is bounded by the deadline plus the grace period."""
    shuttle = scripted([domain("example.co.uk")], timeout_seconds=1)
    job_id = await world.queue(shuttle)

    async def hang_on_finish(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(3600)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_try_finish", hang_on_finish)
        began = time.monotonic()
        assert await run_job(job_id, world.config(shuttle)) is None
        assert time.monotonic() - began < 1 + GRACE + 1
    assert (await world.job(job_id)).status == JobStatus.RUNNING
    assert "its outcome (succeeded) was not recorded" in caplog.text


async def test_start_and_end_are_logged_with_duration(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nightloom_worker.runner")
    shuttle = scripted([domain("example.co.uk")], timeout_seconds=45)
    job_id = await world.queue(shuttle)
    await run_job(job_id, world.config(shuttle))
    assert f"Job {job_id} started: shuttle=scripted version=1.0.0 timeout=45s" in caplog.text
    assert f"Job {job_id} finished: shuttle=scripted status=succeeded reason=- duration=" in (
        caplog.text
    )
    assert "example.co.uk" not in caplog.text


async def test_the_deadline_warning_names_the_phase(
    world: World, caplog: pytest.LogCaptureFixture, short_limits: None
) -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    shuttle = scripted([hang], timeout_seconds=1)
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    assert "the deadline cut the run short after 1.0 s, while running the Shuttle" in caplog.text


async def test_failing_clean_up_is_logged_by_type_only(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fail(self: ShuttleContext) -> None:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(ShuttleContext, "aclose", fail)
    shuttle = scripted([domain("example.co.uk")])
    job_id = await world.queue(shuttle)
    assert await run_job(job_id, world.config(shuttle)) == JobStatus.SUCCEEDED
    assert "closing the HTTP client failed: RuntimeError." in caplog.text
    assert SECRET not in caplog.text


async def test_a_job_started_long_ago_gets_no_time_at_all(world: World) -> None:
    """The deadline counts from started_at, not from when this process got the job."""

    async def hang() -> None:
        await asyncio.sleep(30)

    shuttle = scripted([hang], timeout_seconds=5)
    job_id = await world.queue(shuttle)
    original = service.mark_running

    async def started_earlier(db: Any, job_id_: uuid.UUID) -> bool:
        started = await original(db, job_id_)
        await db.execute(
            update(Job)
            .where(Job.id == job_id_)
            .values(started_at=clock.utcnow() - timedelta(seconds=10))
        )
        await db.commit()
        return started

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service, "mark_running", started_earlier)
        began = time.monotonic()
        assert await run_job(job_id, world.config(shuttle)) == JobStatus.FAILED
    assert time.monotonic() - began < 2
    assert (await world.finish_event(job_id)).details["reason"] == "timeout"


# --- the result cache ----------------------------------------------------------------------


def cached_shuttle(steps: Sequence[object], **overrides: Any) -> tuple[type[Shuttle], list[int]]:
    """A scripted Shuttle with a cache lifetime, and a list counting its runs."""
    runs: list[int] = []

    async def counted() -> None:
        runs.append(1)

    values: dict[str, Any] = {"name": "cached", "cache_ttl_seconds": 3600, **overrides}
    return scripted([counted, *steps], **values), runs


def cache_config(world: World, *shuttles: type[Shuttle], **overrides: Any) -> RunnerConfig:
    return world.config(*shuttles, cache_scope=result_cache.CacheScope.CASE, **overrides)


async def cache_entries(world: World) -> list[result_cache.ResultCacheEntry]:
    async with world.infra.sessionmaker() as db:
        return list((await db.execute(select(result_cache.ResultCacheEntry))).scalars())


async def queue_fresh(world: World, shuttle: type[Shuttle], **kwargs: Any) -> uuid.UUID:
    job_id = await world.queue(shuttle, **kwargs)
    async with world.infra.sessionmaker() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(use_cache=False))
        await db.commit()
    return job_id


async def test_a_second_job_replays_the_cached_findings(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    shuttle, runs = cached_shuttle(
        [domain("example.co.uk"), sub("example.co.uk", "www.shop.example.co.uk")]
    )
    config = cache_config(world, shuttle)
    repo = GraphRepository(world.infra.graph)
    first = await world.queue(shuttle)
    assert await run_job(first, config) == JobStatus.SUCCEEDED
    before = await repo.get_entity(world.case_id, "domain:example.co.uk")
    assert before is not None
    live = await world.job(first)
    assert (live.from_cache, live.cached_fetched_at) == (False, None)
    [entry] = await cache_entries(world)
    assert (entry.scope, entry.case_id, entry.finding_count) == ("case", world.case_id, 2)
    assert entry.findings[0] == {"kind": "entity", "type": "domain", "value": "example.co.uk"}
    assert entry.expires_at - entry.fetched_at == timedelta(hours=1)

    second = await world.queue(shuttle)
    with caplog.at_level(logging.INFO):
        assert await run_job(second, config) == JobStatus.SUCCEEDED
    assert runs == [1]  # the Shuttle ran once
    replayed = await world.job(second)
    assert replayed.from_cache is True
    assert replayed.cached_fetched_at == entry.fetched_at
    assert replayed.stats["entities_updated"] == 1
    assert replayed.stats["relationships_updated"] == 1
    assert "answered from the result cache" in caplog.text
    [entry] = await cache_entries(world)
    assert entry.hits == 1
    # Provenance keeps the time the source was asked: the replay does not
    # make the entity look more recently seen than the live run did.
    after = await repo.get_entity(world.case_id, "domain:example.co.uk")
    assert after is not None
    assert after.entity.provenance.last_seen == before.entity.provenance.last_seen
    assert entry.fetched_at <= before.entity.provenance.first_seen


async def test_fresh_runs_and_new_versions_run_the_shuttle(world: World) -> None:
    shuttle, runs = cached_shuttle([domain("example.co.uk")])
    config = cache_config(world, shuttle)
    assert await run_job(await world.queue(shuttle), config) == JobStatus.SUCCEEDED
    [before] = await cache_entries(world)
    fresh = await queue_fresh(world, shuttle)
    assert await run_job(fresh, config) == JobStatus.SUCCEEDED
    assert runs == [1, 1]
    assert (await world.job(fresh)).from_cache is False
    [after] = await cache_entries(world)
    assert after.fetched_at > before.fetched_at  # a fresh run refreshes the entry

    upgraded, upgraded_runs = cached_shuttle([domain("example.co.uk")], version="1.1.0")
    job_id = await world.queue(upgraded)
    assert await run_job(job_id, cache_config(world, upgraded)) == JobStatus.SUCCEEDED
    assert upgraded_runs == [1]
    assert (await world.job(job_id)).from_cache is False
    assert len(await cache_entries(world)) == 2


async def test_expired_entries_and_other_cases_are_not_reused(world: World) -> None:
    shuttle, runs = cached_shuttle([domain("example.co.uk")])
    config = cache_config(world, shuttle)
    assert await run_job(await world.queue(shuttle), config) == JobStatus.SUCCEEDED
    async with world.infra.sessionmaker() as db:
        await db.execute(update(result_cache.ResultCacheEntry).values(expires_at=clock.utcnow()))
        await db.commit()
    assert await run_job(await world.queue(shuttle), config) == JobStatus.SUCCEEDED
    assert runs == [1, 1]
    # Per case by default: another case asks the source itself.
    other = await world.person_case()
    job_id = await world.queue(shuttle, case_id=other)
    assert await run_job(job_id, config) == JobStatus.SUCCEEDED
    assert runs == [1, 1, 1]
    assert (await world.job(job_id)).from_cache is False


async def test_only_complete_successful_runs_are_cached(world: World) -> None:
    async def boom() -> None:
        raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)

    failing, _ = cached_shuttle([domain("example.co.uk"), boom])
    assert await run_job(await world.queue(failing), cache_config(world, failing)) == (
        JobStatus.FAILED
    )
    too_many, _ = cached_shuttle([domain(f"h{i}.example.co.uk") for i in range(5)])
    status = await run_job(
        await world.queue(too_many), cache_config(world, too_many, max_findings=3)
    )
    assert status == JobStatus.FAILED
    assert await cache_entries(world) == []


async def test_the_cache_is_off_unless_configured_and_never_for_uncached_shuttles(
    world: World,
) -> None:
    shuttle, runs = cached_shuttle([domain("example.co.uk")])
    for _ in range(2):
        assert await run_job(await world.queue(shuttle), world.config(shuttle)) == (
            JobStatus.SUCCEEDED
        )
    uncached, uncached_runs = cached_shuttle([domain("example.co.uk")], cache_ttl_seconds=0)
    for _ in range(2):
        job_id = await world.queue(uncached)
        assert await run_job(job_id, cache_config(world, uncached)) == JobStatus.SUCCEEDED
    assert (runs, uncached_runs) == ([1, 1], [1, 1])
    assert await cache_entries(world) == []


async def test_too_large_results_are_not_cached(world: World) -> None:
    shuttle, _ = cached_shuttle([domain(f"h{i}.example.co.uk") for i in range(20)])
    config = cache_config(world, shuttle, cache_max_entry_bytes=200)
    assert await run_job(await world.queue(shuttle), config) == JobStatus.SUCCEEDED
    assert await cache_entries(world) == []


async def test_replay_applies_the_case_guardrails_again(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raw findings are cached; each replay checks them against its own case."""
    shuttle, runs = cached_shuttle(
        [
            domain("example.co.uk"),
            EntityFinding(type=EntityType.EMAIL, value="alex@example.co.uk"),
            RelationshipFinding(
                type=RelationType.HAS_EMAIL,
                source=EntityRef(type=EntityType.DOMAIN, value="example.co.uk"),
                target=EntityRef(type=EntityType.EMAIL, value="alex@example.co.uk"),
            ),
        ],
        output_entity_types={EntityType.DOMAIN, EntityType.EMAIL},
        output_relationship_types={RelationType.HAS_EMAIL},
        sensitivity=Sensitivity.PERSON,
    )
    config = world.config(shuttle, cache_scope=result_cache.CacheScope.INSTANCE)
    person_case = await world.person_case()
    first = await world.queue(shuttle, case_id=person_case)
    assert await run_job(first, config) == JobStatus.SUCCEEDED
    [entry] = await cache_entries(world)
    assert (entry.scope, entry.case_id, entry.finding_count) == ("instance", None, 3)
    nodes, _ = await world.graph(person_case)
    assert "email:alex@example.co.uk" in nodes

    # Another case, whose person gate is turned off after the job started:
    # the replay rejects the person findings, as a live run would.
    second_case = await world.person_case()
    replay = runner.JobRun.replay

    async def gate_off_then_replay(
        self: runner.JobRun, cached: result_cache.CachedResult
    ) -> runner.Outcome:
        await world.set_case(second_case, allow_person_entities=False)
        return await replay(self, cached)

    monkeypatch.setattr(runner.JobRun, "replay", gate_off_then_replay)
    job_id = await world.queue(shuttle, case_id=second_case)
    assert await run_job(job_id, config) == JobStatus.SUCCEEDED
    assert runs == [1]
    job = await world.job(job_id)
    assert job.from_cache is True
    assert job.stats["rejected_reasons"] == {"person_entities_disabled": 2}
    nodes, edges = await world.graph(second_case)
    assert "domain:example.co.uk" in nodes
    assert not any(key.startswith("email:") for key in nodes)
    assert edges == set()


def noting_shuttle(runs: list[int]) -> type[Shuttle]:
    """A cached Shuttle that adds a note to its job."""

    class Noting(Shuttle):
        meta = globals()["meta"](name="noting", cache_ttl_seconds=3600)

        async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Any]:
            runs.append(1)
            ctx.note("Results were capped.")
            yield domain("example.co.uk")

    return Noting


async def test_notes_are_cached_and_a_broken_cache_never_fails_a_job(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    runs: list[int] = []
    shuttle = noting_shuttle(runs)
    config = cache_config(world, shuttle)
    assert await run_job(await world.queue(shuttle), config) == JobStatus.SUCCEEDED
    [entry] = await cache_entries(world)
    assert entry.notes == ["Results were capped."]
    replayed = await world.queue(shuttle)
    assert await run_job(replayed, config) == JobStatus.SUCCEEDED
    assert (await world.job(replayed)).stats["notes"] == ["Results were capped."]
    assert runs == [1]

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise OSError("down")

    monkeypatch.setattr(result_cache, "lookup", broken)
    monkeypatch.setattr(result_cache, "store", broken)
    job_id = await world.queue(shuttle)
    with caplog.at_level(logging.INFO):
        assert await run_job(job_id, config) == JobStatus.SUCCEEDED
    assert runs == [1, 1]  # ran live instead
    assert (await world.job(job_id)).from_cache is False
    assert "reading the result cache failed (OSError)" in caplog.text
    assert "storing the results in the cache failed: OSError" in caplog.text


async def test_shuttles_keep_evidence_with_the_job_case(world: World) -> None:
    stores: dict[uuid.UUID, MemoryEvidenceStore] = {}
    names: list[str] = []

    def store_for(case_id: uuid.UUID) -> MemoryEvidenceStore:
        return stores.setdefault(case_id, MemoryEvidenceStore())

    class Keeper(Shuttle):
        meta = meta("keeper")

        async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Any]:
            names.append(await ctx.save_evidence(b"\x89PNG picture", content_type="image/png"))
            yield domain("example.co.uk")

    job_id = await world.queue(Keeper)
    assert await run_job(job_id, world.config(Keeper, evidence=store_for)) == JobStatus.SUCCEEDED
    assert list(stores) == [world.case_id]
    assert list(stores[world.case_id].files) == names

    # Without a store the Shuttle is told so (and here, fails).
    failed = await world.queue(Keeper)
    assert await run_job(failed, world.config(Keeper)) == JobStatus.FAILED
