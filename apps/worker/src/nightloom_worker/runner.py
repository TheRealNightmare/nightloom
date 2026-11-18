"""Running one Shuttle job.

:func:`run_job` takes a job from ``queued`` to a final state:

1. **Load and start.** A job that is no longer queued is skipped (it was
   cancelled, or a duplicate delivery). One found ``running`` was abandoned
   by a worker that died, so it is failed. Otherwise it becomes ``running``.
2. **Re-check every guardrail** with the case share-locked, because anything
   may have changed while the job waited: the Shuttle is still installed
   (under the same name; a new version runs and is recorded), the case is
   still open, the requester is still an active member allowed to run
   Shuttles, the Shuttle is passive and accepts the input's type, the person
   gate still allows it, and the input entity still exists. A failed check
   finishes the job as ``failed`` with a ``denied`` ``job.finish`` event.
3. **Run** the Shuttle with its timeout. Each finding is normalized with
   ``nightloom_core``, checked against the Shuttle's declared outputs, the
   case's person gate and the relationship allow-list, and upserted with
   the provenance source ``shuttle:<name>@<version>`` and the Shuttle's
   Admiralty defaults (a finding may override them). Every write share-locks
   the case and checks again that it is open and that the requester may run
   Shuttles; if not, the job stops. A finding that fails a check is counted
   by reason and logged without its value; it is never stored. More than
   :data:`MAX_FINDINGS_PER_JOB` findings stop the job. Cancellation is
   noticed between findings (polled every :data:`CANCEL_POLL_SECONDS`).
4. **Finish** with the status, the stats and a ``job.finish`` event, in one
   transaction. Any error ends as ``failed`` with one of a few fixed,
   user-safe messages: never an exception's text or a stack trace.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from neo4j.exceptions import DriverError, Neo4jError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit.service import Actor
from nightloom_api.auth import clock
from nightloom_api.cases.models import Case
from nightloom_api.graph.repository import (
    EndpointNotFoundError,
    GraphCommitError,
    GraphRepository,
    PersonEntitiesDisabledError,
    RelationshipNotAllowedError,
)
from nightloom_api.jobs import service
from nightloom_api.jobs.models import FINISHED_STATUSES, Job, JobStatus, empty_stats
from nightloom_core.entities import Sensitivity, build_entity, sensitivity_of
from nightloom_core.normalize import NormalizationError, normalize, parse_key
from nightloom_core.provenance import Observation, Reliability
from nightloom_core.relationships import RelationshipError, validate_relationship
from nightloom_sdk.context import JobCancelledError, ShuttleContext
from nightloom_sdk.findings import EntityFinding, RelationshipFinding
from nightloom_sdk.meta import MAX_TIMEOUT_SECONDS, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.shuttle import EntityInput, InputRefusedError, Shuttle

logger = logging.getLogger(__name__)

MAX_FINDINGS_PER_JOB = 5_000
# Keys of the entities a job created or updated, kept in its stats so the UI
# can highlight them; at most this many.
MAX_TOUCHED_KEYS = 200
CANCEL_POLL_SECONDS = 2.0
# A job still running this long after it started was abandoned by a worker
# that died (the Celery hard limit is far shorter).
STALE_RUNNING = timedelta(seconds=MAX_TIMEOUT_SECONDS * 2)
# A job still queued this long was lost by the broker.
STALE_QUEUED = timedelta(hours=6)

# Who finishes jobs in the audit log; the requester is in the job itself.
WORKER_ACTOR = Actor.system("worker")

ERROR_TIMEOUT = "The Shuttle did not finish within {seconds} seconds."
ERROR_TOO_MANY = (
    "The Shuttle produced more than {limit} findings, so the job was stopped. "
    "The findings before the limit were kept."
)
ERROR_SHUTTLE = "The Shuttle failed. Findings stored before the failure were kept."
ERROR_GRAPH = "The graph database was unavailable. Try again later."
ERROR_INTERNAL = "The job failed because of an internal error."
ERROR_WORKER_LOST = "The worker stopped while running this job."
ERROR_NOT_PICKED_UP = "The job was not picked up in time."


@dataclass
class RunnerConfig:
    """Everything a job run needs; built by the Celery task, or by tests."""

    sessionmaker: async_sessionmaker[AsyncSession]
    graph: GraphRepository
    registry: ShuttleRegistry
    max_findings: int = MAX_FINDINGS_PER_JOB
    cancel_poll_seconds: float = CANCEL_POLL_SECONDS


@dataclass
class Stats:
    entities_created: int = 0
    entities_updated: int = 0
    relationships_created: int = 0
    relationships_updated: int = 0
    rejected: Counter[str] = field(default_factory=Counter)
    touched: dict[str, None] = field(default_factory=dict)

    def touch(self, key: str) -> None:
        """Remember an entity the job created or updated (the first MAX_TOUCHED_KEYS)."""
        if len(self.touched) < MAX_TOUCHED_KEYS:
            self.touched[key] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            **empty_stats(),
            "entities_created": self.entities_created,
            "entities_updated": self.entities_updated,
            "relationships_created": self.relationships_created,
            "relationships_updated": self.relationships_updated,
            "findings_rejected": sum(self.rejected.values()),
            "rejected_reasons": dict(sorted(self.rejected.items())),
            "entities_touched": list(self.touched),
        }


@dataclass(frozen=True)
class Outcome:
    status: JobStatus
    error: str | None = None
    reason: str | None = None
    denied: bool = False


class _StopRunError(Exception):
    """Ends the run early with ``outcome`` (a guardrail failed while writing)."""

    def __init__(self, outcome: Outcome) -> None:
        super().__init__(outcome.reason)
        self.outcome = outcome


class _RejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _denied(error: str, reason: str) -> Outcome:
    return Outcome(JobStatus.FAILED, error=error, reason=reason, denied=True)


# --- finishing -------------------------------------------------------------------------------


async def _finish(
    config: RunnerConfig, job_id: uuid.UUID, outcome: Outcome, stats: Stats | None
) -> JobStatus:
    async with config.sessionmaker() as db:
        job = (
            await db.execute(select(Job).where(Job.id == job_id).with_for_update())
        ).scalar_one_or_none()
        if job is None:  # the case (and with it the job) was deleted meanwhile
            logger.info("Job %s disappeared before it finished.", job_id)
            return outcome.status
        if job.status in FINISHED_STATUSES:  # finished already (never twice)
            return job.status
        await service.finish_job(
            db,
            job,
            status=outcome.status,
            actor=WORKER_ACTOR,
            stats=stats.as_dict() if stats is not None else None,
            error=outcome.error,
            reason=outcome.reason,
            denied=outcome.denied,
        )
    logger.info(
        "Job %s finished: %s%s",
        job_id,
        outcome.status.value,
        f" ({outcome.reason})" if outcome.reason else "",
    )
    return outcome.status


async def finish_abandoned(config: RunnerConfig, job_id: uuid.UUID, outcome: Outcome) -> JobStatus:
    """Finish a job its run could not finish (the worker's time limit); no-op once final."""
    return await _finish(config, job_id, outcome, None)


async def reap_stale_jobs(config: RunnerConfig) -> int:
    """Fail jobs abandoned by a dead worker or lost by the broker. Returns how many."""
    now = clock.utcnow()
    async with config.sessionmaker() as db:
        stale = list(
            (
                await db.execute(
                    select(Job.id, Job.status).where(
                        ((Job.status == JobStatus.RUNNING) & (Job.started_at < now - STALE_RUNNING))
                        | ((Job.status == JobStatus.QUEUED) & (Job.created_at < now - STALE_QUEUED))
                    )
                )
            ).all()
        )
    for job_id, status in stale:
        error, reason = (
            (ERROR_WORKER_LOST, "worker_lost")
            if status == JobStatus.RUNNING
            else (ERROR_NOT_PICKED_UP, "not_picked_up")
        )
        await _finish(config, job_id, Outcome(JobStatus.FAILED, error=error, reason=reason), None)
    return len(stale)


# --- the run ---------------------------------------------------------------------------------


class JobRun:
    """One job's run: guardrails, the Shuttle, its findings."""

    def __init__(self, config: RunnerConfig, job: Job, shuttle: type[Shuttle]) -> None:
        self.config = config
        self.job_id = job.id
        self.case_id = job.case_id
        self.requested_by = job.requested_by
        self.input_key = job.input_key
        self.shuttle = shuttle
        self.meta: ShuttleMeta = shuttle.meta
        self.stats = Stats()
        self.received = 0
        self.cancel_event = asyncio.Event()

    # --- guardrails ---

    @contextlib.asynccontextmanager
    async def locked_case(self) -> AsyncIterator[Case]:
        """The open case, share-locked, with the requester still allowed; else stop the run."""
        async with self.config.sessionmaker() as db, db.begin():
            case = await service.lock_case_for_job(db, self.case_id)
            if not service.case_is_open(case):
                raise _StopRunError(_denied(service.ERROR_CASE_NOT_OPEN, "case_not_open"))
            assert case is not None  # noqa: S101 - case_is_open checked it
            if not await service.requester_may_run(db, self.case_id, self.requested_by):
                raise _StopRunError(_denied(service.ERROR_NOT_ALLOWED, "requester_not_allowed"))
            yield case

    async def check_start(self) -> EntityInput:
        """Every guardrail, again, at execution time. Returns the input entity."""
        input_type = parse_key(self.input_key)[0]
        async with self.locked_case() as case:
            try:
                service.check_shuttle(
                    self.meta,
                    input_type=input_type,
                    allow_person_entities=case.allow_person_entities,
                )
            except service.GuardrailError as error:
                raise _StopRunError(_denied(str(error), error.reason)) from error
            detail = await self.config.graph.get_entity(self.case_id, self.input_key)
            if detail is None:
                raise _StopRunError(
                    Outcome(JobStatus.FAILED, error=service.ERROR_ENTITY_GONE, reason="entity_gone")
                )
        entity = detail.entity
        return EntityInput(
            type=entity.type, value=entity.value, key=entity.key, properties=entity.properties
        )

    # --- findings ---

    def observation(
        self, *, confidence: float | None, admiralty: str | None, note: str | None
    ) -> Observation:
        rating = admiralty or self.meta.default_admiralty
        reliability: Reliability = rating[0]  # type: ignore[assignment]
        return Observation(
            source=self.meta.source,
            observed_at=clock.utcnow(),
            confidence=self.meta.default_confidence if confidence is None else confidence,
            admiralty_reliability=reliability,
            admiralty_credibility=int(rating[1]),
            note=note,
        )

    async def store(self, finding: object) -> None:
        """Check one finding and write it; raises :class:`_RejectedError` or stops the run."""
        match finding:
            case EntityFinding():
                await self.store_entity(finding)
            case RelationshipFinding():
                await self.store_relationship(finding)
            case _:
                raise _RejectedError("not_a_finding")

    async def store_entity(self, finding: EntityFinding) -> None:
        if finding.type not in self.meta.output_entity_types:
            raise _RejectedError("undeclared_entity_type")
        try:
            entity = build_entity(finding.type, finding.value, finding.properties)
        except NormalizationError as error:
            raise _RejectedError("invalid_value") from error
        except ValidationError as error:
            raise _RejectedError("invalid_properties") from error
        observation = self.observation(
            confidence=finding.confidence, admiralty=finding.admiralty, note=finding.note
        )
        async with self.locked_case() as case:
            if entity.sensitivity == Sensitivity.PERSON and not case.allow_person_entities:
                raise _RejectedError("person_entities_disabled")
            record = await self.config.graph.upsert_entity(self.case_id, entity, observation)
        if record.created:
            self.stats.entities_created += 1
        else:
            self.stats.entities_updated += 1
        self.stats.touch(entity.key)

    async def store_relationship(self, finding: RelationshipFinding) -> None:
        if finding.type not in self.meta.output_relationship_types:
            raise _RejectedError("undeclared_relationship_type")
        endpoints = self.meta.output_entity_types | self.meta.input_types
        if finding.source.type not in endpoints or finding.target.type not in endpoints:
            raise _RejectedError("undeclared_entity_type")
        try:
            source = normalize(finding.source.type, finding.source.value)
            target = normalize(finding.target.type, finding.target.value)
        except NormalizationError as error:
            raise _RejectedError("invalid_value") from error
        try:
            validate_relationship(finding.type, source.type, target.type, finding.note)
        except RelationshipError as error:
            raise _RejectedError("relationship_not_allowed") from error
        observation = self.observation(
            confidence=finding.confidence, admiralty=None, note=finding.note
        )
        personal = Sensitivity.PERSON in (sensitivity_of(source.type), sensitivity_of(target.type))
        async with self.locked_case() as case:
            if personal and not case.allow_person_entities:
                raise _RejectedError("person_entities_disabled")
            try:
                record = await self.config.graph.upsert_relationship(
                    self.case_id,
                    finding.type,
                    source.key,
                    target.key,
                    observation,
                    allow_person_entities=case.allow_person_entities,
                )
            except EndpointNotFoundError as error:
                raise _RejectedError("endpoint_not_found") from error
            except RelationshipNotAllowedError as error:
                raise _RejectedError("relationship_not_allowed") from error
            except PersonEntitiesDisabledError as error:
                raise _RejectedError("person_entities_disabled") from error
        if record.created:
            self.stats.relationships_created += 1
        else:
            self.stats.relationships_updated += 1

    # --- cancellation ---

    async def watch_cancel(self) -> None:
        """Poll ``cancel_requested`` and set the cancel event once it is true."""
        while not self.cancel_event.is_set():
            await asyncio.sleep(self.config.cancel_poll_seconds)
            try:
                async with self.config.sessionmaker() as db:
                    if await service.cancel_requested(db, self.job_id):
                        self.cancel_event.set()
            except (SQLAlchemyError, OSError) as error:
                logger.warning("Job %s: cancel check failed: %s", self.job_id, type(error).__name__)

    # --- running ---

    async def consume(self, ctx: ShuttleContext, entity: EntityInput) -> Outcome:
        """Pull findings until the Shuttle ends, the job is cancelled or a limit is hit."""
        findings = self.shuttle().run(ctx, entity)
        pending: asyncio.Future[Any] | None = None
        try:
            while True:
                if self.cancel_event.is_set():
                    return Outcome(JobStatus.CANCELLED, reason="cancelled")
                pending = asyncio.ensure_future(anext(findings))
                cancelled = asyncio.ensure_future(self.cancel_event.wait())
                done, _ = await asyncio.wait(
                    {pending, cancelled}, return_when=asyncio.FIRST_COMPLETED
                )
                cancelled.cancel()
                if pending not in done:
                    return Outcome(JobStatus.CANCELLED, reason="cancelled")
                step, pending = pending, None
                try:
                    finding = step.result()
                except StopAsyncIteration:
                    return Outcome(JobStatus.SUCCEEDED)
                except JobCancelledError:
                    return Outcome(JobStatus.CANCELLED, reason="cancelled")
                except InputRefusedError as refused:
                    # The Shuttle refused the input before doing anything; its
                    # message is one of the SDK's fixed, user-safe ones.
                    return Outcome(
                        JobStatus.FAILED, error=refused.message, reason=refused.reason.value
                    )
                except Exception as error:
                    # A bug or an unhandled error in the Shuttle. Only the type
                    # is logged: the text may quote a response or a key.
                    logger.warning(
                        "Job %s: the Shuttle raised %s.", self.job_id, type(error).__name__
                    )
                    return Outcome(JobStatus.FAILED, error=ERROR_SHUTTLE, reason="shuttle_error")
                self.received += 1
                if self.received > self.config.max_findings:
                    return Outcome(
                        JobStatus.FAILED,
                        error=ERROR_TOO_MANY.format(limit=f"{self.config.max_findings:,}"),
                        reason="too_many_findings",
                    )
                try:
                    await self.store(finding)
                except _RejectedError as rejected:
                    self.stats.rejected[rejected.reason] += 1
                    logger.info("Job %s: rejected a finding (%s).", self.job_id, rejected.reason)
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
            with contextlib.suppress(Exception):
                await findings.aclose()  # type: ignore[attr-defined]

    async def run(self) -> Outcome:
        try:
            entity = await self.check_start()
        except _StopRunError as stop:
            return stop.outcome
        ctx = ShuttleContext(self.meta, cancel_event=self.cancel_event)
        watcher = asyncio.create_task(self.watch_cancel())
        try:
            async with asyncio.timeout(self.meta.timeout_seconds):
                return await self.consume(ctx, entity)
        except TimeoutError:
            return Outcome(
                JobStatus.FAILED,
                error=ERROR_TIMEOUT.format(seconds=self.meta.timeout_seconds),
                reason="timeout",
            )
        except _StopRunError as stop:
            return stop.outcome
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            await ctx.aclose()


async def run_job(job_id: uuid.UUID, config: RunnerConfig) -> JobStatus | None:
    """Run the job and record how it ended. Returns its final status (None if unknown)."""
    async with config.sessionmaker() as db:
        job = await db.get(Job, job_id)
        if job is None:
            logger.warning("Job %s does not exist (its case may have been deleted).", job_id)
            return None
        if job.status == JobStatus.RUNNING:
            # Delivered again after the worker running it died.
            return await _finish(
                config,
                job_id,
                Outcome(JobStatus.FAILED, error=ERROR_WORKER_LOST, reason="worker_lost"),
                None,
            )
        if job.status != JobStatus.QUEUED or job.cancel_requested:
            logger.info("Job %s is %s; skipping it.", job_id, job.status.value)
            return job.status
        if not await service.mark_running(db, job_id):
            await db.refresh(job)
            return job.status
        await db.refresh(job)

    shuttle = config.registry.get(job.shuttle_name)
    if shuttle is None:
        return await _finish(
            config,
            job_id,
            Outcome(JobStatus.FAILED, error=service.ERROR_NOT_INSTALLED, reason="not_installed"),
            None,
        )
    if shuttle.meta.version != job.shuttle_version:
        # Upgraded while the job waited: record the version that actually runs.
        async with config.sessionmaker() as db:
            stored = await db.get(Job, job_id)
            if stored is not None:
                stored.shuttle_version = shuttle.meta.version
                await db.commit()

    run = JobRun(config, job, shuttle)
    try:
        outcome = await run.run()
    except (Neo4jError, DriverError, GraphCommitError) as error:
        logger.error("Job %s: graph error %s.", job_id, type(error).__name__)
        outcome = Outcome(JobStatus.FAILED, error=ERROR_GRAPH, reason="graph_unavailable")
    except Exception as error:
        # Only the type is logged, never the text (it may quote values).
        logger.error("Job %s failed: %s.", job_id, type(error).__name__)
        outcome = Outcome(JobStatus.FAILED, error=ERROR_INTERNAL, reason="internal_error")
    if outcome.status == JobStatus.SUCCEEDED and run.cancel_event.is_set():
        outcome = Outcome(JobStatus.CANCELLED, reason="cancelled")
    return await _finish(config, job_id, outcome, run.stats)


__all__ = [
    "CANCEL_POLL_SECONDS",
    "ERROR_WORKER_LOST",
    "MAX_FINDINGS_PER_JOB",
    "MAX_TOUCHED_KEYS",
    "Outcome",
    "RunnerConfig",
    "Stats",
    "finish_abandoned",
    "reap_stale_jobs",
    "run_job",
]
