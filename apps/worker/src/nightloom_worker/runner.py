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
3. **Run** the Shuttle until the job's **deadline** (``started_at`` + the
   Shuttle's timeout; see :mod:`nightloom_api.jobs.deadlines`). Each finding is normalized with
   ``nightloom_core``, checked against the Shuttle's declared outputs, the
   case's person gate and the relationship allow-list, and upserted with
   the provenance source ``shuttle:<name>@<version>`` and the Shuttle's
   Admiralty defaults (a finding may override them). Every write share-locks
   the case and checks again that it is open and that the requester may run
   Shuttles; if not, the job stops. A finding that fails a check is counted
   by reason and logged without its value; it is never stored. More than
   :data:`MAX_FINDINGS_PER_JOB` findings stop the job. Cancellation is
   noticed between findings (polled every :data:`CANCEL_POLL_SECONDS`).
   **Result cache.** For a passive Shuttle with a ``cache_ttl_seconds``
   (and the cache not ``off``), a job that may reuse results first looks
   for a valid entry for this Shuttle version and entity (see
   :mod:`nightloom_api.jobs.result_cache`). On a hit, the stored raw
   findings are **replayed** through exactly the same checks and writes, so
   case-specific guardrails such as the person gate apply again, and
   provenance keeps the time the source was asked; the job is marked
   ``from_cache``. Otherwise the Shuttle runs, and a run that succeeds
   stores what it yielded (before any check) for the next job. A cache
   that cannot be read or written never fails a job: it runs, or succeeds,
   without it.
4. **Finish** with the status, the stats and a ``job.finish`` event, in one
   transaction. Any error ends as ``failed`` with one of a few fixed,
   user-safe messages: never an exception's text or a stack trace.

Nothing in a run may outlast the deadline by much: the guardrail checks and
the Shuttle's findings (with their graph writes) are cut at the deadline; a
Shuttle that ignores cancellation, the cancellation watcher and closing the
Shuttle's HTTP client each get :data:`CLEANUP_SECONDS` and are then left
behind; recording the outcome gets until the deadline plus
:data:`FINISH_GRACE_SECONDS`. A job that still cannot be recorded is left to
the reaper (:func:`reap_stale_jobs`).

Finishing is a conditional update (only while the job is still active), so
a run, the worker's time-limit handling and the reaper can race without a
job being finished twice.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
from nightloom_api.jobs import deadlines, result_cache, service
from nightloom_api.jobs.models import ACTIVE_STATUSES, Job, JobStatus, empty_stats
from nightloom_api.ownership import service as ownership
from nightloom_api.ownership.models import OwnershipVerification
from nightloom_core.entities import Sensitivity, build_entity, sensitivity_of
from nightloom_core.normalize import NormalizationError, normalize, parse_key
from nightloom_core.provenance import Observation, Reliability
from nightloom_core.relationships import RelationshipError, validate_relationship
from nightloom_sdk.context import JobCancelledError, ShuttleContext
from nightloom_sdk.findings import EntityFinding, RelationshipFinding
from nightloom_sdk.meta import Mode, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.shuttle import EntityInput, InputRefusedError, Shuttle, ShuttleFailedError
from nightloom_worker.ownership import AddressResolver

logger = logging.getLogger(__name__)

MAX_FINDINGS_PER_JOB = 5_000
# Keys of the entities a job created or updated, kept in its stats so the UI
# can highlight them; at most this many.
MAX_TOUCHED_KEYS = 200
CANCEL_POLL_SECONDS = 2.0
# Seconds after the deadline to record the outcome (and only that).
FINISH_GRACE_SECONDS = float(deadlines.FINISH_GRACE_SECONDS)
# Seconds for each clean-up step: a Shuttle ignoring cancellation, the
# cancellation watcher, closing the Shuttle's HTTP client.
CLEANUP_SECONDS = 5.0
# A job still queued this long was lost by the broker.
STALE_QUEUED = timedelta(hours=6)

# The largest result-cache entry stored (bytes of JSON); larger runs are not cached.
DEFAULT_CACHE_MAX_ENTRY_BYTES = 5 * 1024 * 1024

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
ERROR_TIME_LIMIT = "The job ran past its time limit, so the worker stopped it."
ERROR_NOT_PICKED_UP = "The job was not picked up in time."


@dataclass
class RunnerConfig:
    """Everything a job run needs; built by the Celery task, or by tests."""

    sessionmaker: async_sessionmaker[AsyncSession]
    graph: GraphRepository
    registry: ShuttleRegistry
    max_findings: int = MAX_FINDINGS_PER_JOB
    cancel_poll_seconds: float = CANCEL_POLL_SECONDS
    # Read when the config is built, so tests can shorten them.
    finish_grace_seconds: float = field(default_factory=lambda: FINISH_GRACE_SECONDS)
    cleanup_seconds: float = field(default_factory=lambda: CLEANUP_SECONDS)
    # The result cache: off unless the worker passes the configured scope.
    cache_scope: result_cache.CacheScope = result_cache.CacheScope.OFF
    cache_max_entry_bytes: int = DEFAULT_CACHE_MAX_ENTRY_BYTES
    # Resolves a verified domain again before an active Shuttle runs on one of
    # its addresses; without one, such runs are refused.
    prover: AddressResolver | None = None


@dataclass
class Stats:
    entities_created: int = 0
    entities_updated: int = 0
    relationships_created: int = 0
    relationships_updated: int = 0
    rejected: Counter[str] = field(default_factory=Counter)
    touched: dict[str, None] = field(default_factory=dict)
    # The Shuttle's own remarks (ShuttleContext.note), shown with the job.
    notes: list[str] = field(default_factory=list)

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
            "notes": list(self.notes),
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


# --- bounded waits --------------------------------------------------------------------------


async def bounded(awaitable: Awaitable[Any], seconds: float, what: str) -> bool:
    """Await ``awaitable`` for at most ``seconds``; False (and a warning) if it did not finish.

    It runs as a task of its own and is abandoned (cancelled, not awaited)
    when the time is up, so even code that ignores cancellation cannot hold
    the caller. Errors are logged by type only and swallowed.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=max(0.0, seconds))
    except asyncio.CancelledError:
        task.cancel()
        raise
    if not done:
        task.cancel()
        logger.warning("%s did not finish within %.1f s; left behind.", what, seconds)
        return False
    if task.cancelled():
        return True
    error = task.exception()
    if error is not None:
        logger.warning("%s failed: %s.", what, type(error).__name__)
        return False
    return True


# --- finishing -------------------------------------------------------------------------------


async def _try_finish(
    sessionmaker: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    outcome: Outcome,
    stats: Stats | None,
    *,
    only_if: Collection[JobStatus] = ACTIVE_STATUSES,
) -> tuple[JobStatus | None, bool]:
    """Finish the job if its status is in ``only_if``: (its status now, whether this call did)."""
    async with sessionmaker() as db:
        job = await service.finish_job_if(
            db,
            job_id,
            only_if=only_if,
            status=outcome.status,
            actor=WORKER_ACTOR,
            stats=stats.as_dict() if stats is not None else None,
            error=outcome.error,
            reason=outcome.reason,
            denied=outcome.denied,
        )
        if job is None:
            current = await db.scalar(select(Job.status).where(Job.id == job_id))
            if current is None:  # the case (and with it the job) was deleted meanwhile
                logger.info("Job %s disappeared before it finished.", job_id)
            return current, False
    duration = (
        (job.finished_at - job.started_at).total_seconds()
        if job.finished_at is not None and job.started_at is not None
        else 0.0
    )
    logger.info(
        "Job %s finished: shuttle=%s status=%s reason=%s duration=%.1fs",
        job_id,
        job.shuttle_name,
        outcome.status.value,
        outcome.reason or "-",
        duration,
    )
    return outcome.status, True


async def _finish(
    config: RunnerConfig,
    job_id: uuid.UUID,
    outcome: Outcome,
    stats: Stats | None,
    *,
    seconds: float | None = None,
) -> JobStatus | None:
    """Record how the job ended (no-op once final), within ``seconds``.

    Returns the job's status, or None if it is unknown (the job is gone, or
    the database did not answer in time: the reaper fails the job later).
    """
    limit = config.finish_grace_seconds if seconds is None else seconds
    task = asyncio.ensure_future(_try_finish(config.sessionmaker, job_id, outcome, stats))
    if not await bounded(task, limit, f"Job {job_id}: recording the outcome"):
        logger.error("Job %s: its outcome (%s) was not recorded.", job_id, outcome.status.value)
        return None
    status, _ = task.result()
    return status if status is not None else outcome.status


async def finish_abandoned(
    sessionmaker: async_sessionmaker[AsyncSession], job_id: uuid.UUID, outcome: Outcome
) -> bool:
    """Fail a job its run could not finish (time limit, lost worker) if it is still running.

    Returns whether this call finished it.
    """
    _, finished = await _try_finish(
        sessionmaker, job_id, outcome, None, only_if={JobStatus.RUNNING}
    )
    return finished


async def reap_stale_jobs(
    sessionmaker: async_sessionmaker[AsyncSession], registry: ShuttleRegistry
) -> int:
    """Fail jobs abandoned by a dead worker or lost by the broker. Returns how many.

    A running job is stale :data:`~nightloom_api.jobs.deadlines.REAPER_MARGIN_SECONDS`
    after its own hard limit (from its Shuttle's timeout; the largest allowed
    timeout for a Shuttle that is not installed). Each is failed with a
    conditional update, so a job finishing at the same moment is left alone.
    """
    now = clock.utcnow()
    # No job can be stale sooner than one with the shortest timeout (1 s).
    earliest = deadlines.stale_at(now, 1) - now
    async with sessionmaker() as db:
        candidates = (
            await db.execute(
                select(Job.id, Job.status, Job.shuttle_name, Job.started_at).where(
                    ((Job.status == JobStatus.RUNNING) & (Job.started_at < now - earliest))
                    | ((Job.status == JobStatus.QUEUED) & (Job.created_at < now - STALE_QUEUED))
                )
            )
        ).all()
    reaped = 0
    for job_id, status, shuttle_name, started_at in candidates:
        if status == JobStatus.RUNNING:
            timeout = deadlines.shuttle_timeout(registry, shuttle_name)
            if started_at is None or deadlines.stale_at(started_at, timeout) > now:
                continue
            outcome = Outcome(JobStatus.FAILED, error=ERROR_WORKER_LOST, reason="worker_lost")
        else:
            outcome = Outcome(JobStatus.FAILED, error=ERROR_NOT_PICKED_UP, reason="not_picked_up")
        _, finished = await _try_finish(sessionmaker, job_id, outcome, None, only_if={status})
        if finished:
            logger.warning("Reaped job %s (%s): %s.", job_id, status.value, outcome.reason)
            reaped += 1
    return reaped


# --- the run ---------------------------------------------------------------------------------


class JobRun:
    """One job's run: guardrails, the Shuttle, its findings."""

    def __init__(self, config: RunnerConfig, job: Job, shuttle: type[Shuttle]) -> None:
        self.config = config
        self.job_id = job.id
        self.case_id = job.case_id
        self.requested_by = job.requested_by
        self.input_key = job.input_key
        self.use_cache = job.use_cache
        self.shuttle = shuttle
        self.meta: ShuttleMeta = shuttle.meta
        self.stats = Stats()
        self.received = 0
        self.cancel_event = asyncio.Event()
        # What the run is doing, for the log line when the deadline cuts it.
        self.phase = "starting"
        # The result cache: when a live run started (the entry's fetched_at),
        # the time to record for replayed findings (the entry's), the replayed
        # entry, and what a live run yielded so far (None once too large).
        self.fetched_at: datetime | None = None
        self.observed_at: datetime | None = None
        self.cached: result_cache.CachedResult | None = None
        self.recorded: list[dict[str, Any]] | None = None
        self.recorded_bytes = 0

    # --- guardrails ---

    @contextlib.asynccontextmanager
    async def locked(self) -> AsyncIterator[tuple[AsyncSession, Case]]:
        """The open case, share-locked, with the requester still allowed; else stop the run."""
        async with self.config.sessionmaker() as db, db.begin():
            case = await service.lock_case_for_job(db, self.case_id)
            if not service.case_is_open(case):
                raise _StopRunError(_denied(service.ERROR_CASE_NOT_OPEN, "case_not_open"))
            assert case is not None  # noqa: S101 - case_is_open checked it
            if not await service.requester_may_run(db, self.case_id, self.requested_by):
                raise _StopRunError(_denied(service.ERROR_NOT_ALLOWED, "requester_not_allowed"))
            yield db, case

    @contextlib.asynccontextmanager
    async def locked_case(self) -> AsyncIterator[Case]:
        async with self.locked() as (_db, case):
            yield case

    async def check_start(self) -> EntityInput:
        """Every guardrail, again, at execution time. Returns the input entity.

        An active Shuttle needs a verification of the case that covers its
        target; one covering an IP address through its domain's addresses is
        confirmed by resolving the domain again, now.
        """
        input_type = parse_key(self.input_key)[0]
        covering: OwnershipVerification | None = None
        async with self.locked() as (db, case):
            if self.meta.mode == Mode.ACTIVE:
                covering = await ownership.covers(db, self.case_id, self.input_key, clock.utcnow())
            try:
                service.check_shuttle(
                    self.meta,
                    input_type=input_type,
                    allow_person_entities=case.allow_person_entities,
                    target_verified=covering is not None,
                )
            except service.GuardrailError as error:
                raise _StopRunError(_denied(str(error), error.reason)) from error
            domain = covering.domain if covering is not None else None
            detail = await self.config.graph.get_entity(self.case_id, self.input_key)
            if detail is None:
                raise _StopRunError(
                    Outcome(JobStatus.FAILED, error=service.ERROR_ENTITY_GONE, reason="entity_gone")
                )
        if domain is not None:
            await self.confirm_address(domain)
        entity = detail.entity
        return EntityInput(
            type=entity.type, value=entity.value, key=entity.key, properties=entity.properties
        )

    async def confirm_address(self, domain: str) -> None:
        """An address target must still be one of the verified domain's addresses."""
        target = ownership.target_of(self.input_key)
        if target is None or target.address is None:
            return
        if target.host is not None and ownership.under(target.host, domain):
            return
        prover = self.config.prover
        found = await prover.addresses(domain) if prover is not None else None
        if found is None or target.address not in found:
            raise _StopRunError(_denied(service.ERROR_ACTIVE, "target_not_verified"))

    # --- findings ---

    def observation(
        self, *, confidence: float | None, admiralty: str | None, note: str | None
    ) -> Observation:
        rating = admiralty or self.meta.default_admiralty
        reliability: Reliability = rating[0]  # type: ignore[assignment]
        return Observation(
            source=self.meta.source,
            observed_at=self.observed_at or clock.utcnow(),
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
            confidence=finding.confidence, admiralty=finding.admiralty, note=finding.note
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

    # --- the result cache ---

    @property
    def cacheable(self) -> bool:
        """Whether this Shuttle's results may be cached at all."""
        return (
            self.config.cache_scope != result_cache.CacheScope.OFF
            and self.meta.mode == Mode.PASSIVE
            and self.meta.cache_ttl_seconds > 0
        )

    @property
    def cache_key(self) -> str:
        return result_cache.cache_key(
            scope=self.config.cache_scope,
            case_id=self.case_id,
            shuttle_name=self.meta.name,
            shuttle_version=self.meta.version,
            entity_key=self.input_key,
        )

    def record(self, finding: object) -> None:
        """Keep a live run's finding for the cache, as JSON (before any check)."""
        if self.recorded is None:
            return
        match finding:
            case EntityFinding():
                kind = "entity"
            case RelationshipFinding():
                kind = "relationship"
            case _:
                return  # rejected anyway, now and on every replay
        item = {"kind": kind, **finding.model_dump(mode="json", exclude_none=True)}
        self.recorded_bytes += result_cache.encoded_size([item], [])
        if self.recorded_bytes > self.config.cache_max_entry_bytes:
            self.recorded = None  # too large to store: stop keeping it
            return
        self.recorded.append(item)

    async def load_cached(self) -> result_cache.CachedResult | None:
        """A valid entry for this run, or None (also when the cache cannot be read)."""
        try:
            async with self.config.sessionmaker() as db, db.begin():
                cached = await result_cache.lookup(
                    db, key=self.cache_key, scope=self.config.cache_scope, now=clock.utcnow()
                )
                if cached is not None:
                    await service.mark_from_cache(db, self.job_id, cached.fetched_at)
                return cached
        except (SQLAlchemyError, OSError) as error:
            logger.warning(
                "Job %s: reading the result cache failed (%s); running the Shuttle.",
                self.job_id,
                type(error).__name__,
            )
            return None

    async def save_to_cache(self, notes: list[str]) -> None:
        """Store a successful live run's findings for later jobs."""
        if self.recorded is None or self.fetched_at is None:
            return
        async with self.config.sessionmaker() as db, db.begin():
            stored = await result_cache.store(
                db,
                key=self.cache_key,
                scope=self.config.cache_scope,
                case_id=self.case_id,
                shuttle_name=self.meta.name,
                shuttle_version=self.meta.version,
                entity_key=self.input_key,
                findings=self.recorded,
                notes=notes,
                fetched_at=self.fetched_at,
                ttl_seconds=self.meta.cache_ttl_seconds,
                max_entry_bytes=self.config.cache_max_entry_bytes,
            )
        if not stored:
            logger.info("Job %s: the results are too large to cache.", self.job_id)

    async def replay(self, cached: result_cache.CachedResult) -> Outcome:
        """Write a cache entry's findings with the same checks as a live run's."""
        for item in cached.findings:
            if self.cancel_event.is_set():
                return Outcome(JobStatus.CANCELLED, reason="cancelled")
            self.received += 1
            if self.received > self.config.max_findings:
                return Outcome(
                    JobStatus.FAILED,
                    error=ERROR_TOO_MANY.format(limit=f"{self.config.max_findings:,}"),
                    reason="too_many_findings",
                )
            fields = {name: value for name, value in item.items() if name != "kind"}
            try:
                finding: EntityFinding | RelationshipFinding = (
                    RelationshipFinding.model_validate(fields)
                    if item.get("kind") == "relationship"
                    else EntityFinding.model_validate(fields)
                )
                await self.store(finding)
            except ValidationError:
                self.stats.rejected["invalid_cached_finding"] += 1
            except _RejectedError as rejected:
                self.stats.rejected[rejected.reason] += 1
                logger.info("Job %s: rejected a finding (%s).", self.job_id, rejected.reason)
        return Outcome(JobStatus.SUCCEEDED)

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
                except ShuttleFailedError as failed:
                    # A known failure (e.g. the source is down); again a fixed
                    # SDK message, never the Shuttle's own text.
                    return Outcome(
                        JobStatus.FAILED, error=failed.message, reason=failed.reason.value
                    )
                except Exception as error:
                    # A bug or an unhandled error in the Shuttle. Only the type
                    # is logged: the text may quote a response or a key.
                    logger.warning(
                        "Job %s: the Shuttle raised %s.", self.job_id, type(error).__name__
                    )
                    return Outcome(JobStatus.FAILED, error=ERROR_SHUTTLE, reason="shuttle_error")
                self.received += 1
                self.record(finding)
                if self.received > self.config.max_findings:
                    return Outcome(
                        JobStatus.FAILED,
                        error=ERROR_TOO_MANY.format(limit=f"{self.config.max_findings:,}"),
                        reason="too_many_findings",
                    )
                self.phase = "writing findings"
                try:
                    await self.store(finding)
                except _RejectedError as rejected:
                    self.stats.rejected[rejected.reason] += 1
                    logger.info("Job %s: rejected a finding (%s).", self.job_id, rejected.reason)
                self.phase = "running the Shuttle"
        finally:
            await self.stop_shuttle(pending, findings)

    async def stop_shuttle(self, pending: "asyncio.Future[Any] | None", findings: Any) -> None:
        """Cancel the Shuttle's step in progress and close it, each within the clean-up time."""
        seconds = self.config.cleanup_seconds
        if pending is not None and not pending.done():
            pending.cancel()
            done, _ = await asyncio.wait({pending}, timeout=seconds)
            if not done:
                logger.warning(
                    "Job %s: the Shuttle ignored cancellation for %.1f s; left behind.",
                    self.job_id,
                    seconds,
                )
                return  # it is still running: closing it would fail
            if not pending.cancelled():
                pending.exception()  # retrieved: the run is over either way
        await bounded(findings.aclose(), seconds, f"Job {self.job_id}: closing the Shuttle")

    async def run(self, deadline: float) -> Outcome:
        """Run until ``deadline`` (a time of the running loop's clock)."""
        loop = asyncio.get_running_loop()
        began = loop.time()
        ctx = ShuttleContext(self.meta, cancel_event=self.cancel_event)
        watcher = asyncio.create_task(self.watch_cancel())
        try:
            async with asyncio.timeout_at(deadline):
                self.phase = "checking guardrails"
                entity = await self.check_start()
                if self.cacheable and self.use_cache:
                    self.phase = "reading the result cache"
                    self.cached = await self.load_cached()
                if self.cached is not None:
                    self.phase = "writing cached findings"
                    self.observed_at = self.cached.fetched_at
                    logger.info(
                        "Job %s: answered from the result cache (fetched %s).",
                        self.job_id,
                        self.cached.fetched_at.isoformat(),
                    )
                    return await self.replay(self.cached)
                self.phase = "running the Shuttle"
                self.fetched_at = clock.utcnow()
                if self.cacheable:
                    self.recorded = []
                outcome = await self.consume(ctx, entity)
            if outcome.status == JobStatus.SUCCEEDED and not self.cancel_event.is_set():
                # Never at the cost of the job: within what is left before the
                # deadline (at most a clean-up step), and errors only logged.
                seconds = min(deadline - loop.time(), self.config.cleanup_seconds)
                if self.recorded is not None and seconds > 0:
                    await bounded(
                        self.save_to_cache(list(ctx.notes)),
                        seconds,
                        f"Job {self.job_id}: storing the results in the cache",
                    )
            return outcome
        except TimeoutError:
            logger.warning(
                "Job %s: the deadline cut the run short after %.1f s, while %s.",
                self.job_id,
                loop.time() - began,
                self.phase,
            )
            return Outcome(
                JobStatus.FAILED,
                error=ERROR_TIMEOUT.format(seconds=self.meta.timeout_seconds),
                reason="timeout",
            )
        except _StopRunError as stop:
            return stop.outcome
        finally:
            self.stats.notes = list(self.cached.notes if self.cached else ctx.notes)
            seconds = self.config.cleanup_seconds
            watcher.cancel()
            await bounded(watcher, seconds, f"Job {self.job_id}: stopping the cancel watcher")
            await bounded(ctx.aclose(), seconds, f"Job {self.job_id}: closing the HTTP client")


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

    timeout = shuttle.meta.timeout_seconds
    loop = asyncio.get_running_loop()
    started_at = job.started_at or clock.utcnow()
    remaining = (deadlines.deadline(started_at, timeout) - clock.utcnow()).total_seconds()
    deadline = loop.time() + max(0.0, remaining)
    logger.info(
        "Job %s started: shuttle=%s version=%s timeout=%ds",
        job_id,
        shuttle.meta.name,
        shuttle.meta.version,
        timeout,
    )
    run = JobRun(config, job, shuttle)
    try:
        outcome = await run.run(deadline)
    except (Neo4jError, DriverError, GraphCommitError) as error:
        logger.error("Job %s: graph error %s.", job_id, type(error).__name__)
        outcome = Outcome(JobStatus.FAILED, error=ERROR_GRAPH, reason="graph_unavailable")
    except Exception as error:
        # Only the type is logged, never the text (it may quote values).
        logger.error("Job %s failed: %s.", job_id, type(error).__name__)
        outcome = Outcome(JobStatus.FAILED, error=ERROR_INTERNAL, reason="internal_error")
    if outcome.status == JobStatus.SUCCEEDED and run.cancel_event.is_set():
        outcome = Outcome(JobStatus.CANCELLED, reason="cancelled")
    # Until the deadline plus the grace period; never less than a clean-up step.
    seconds = max(deadline + config.finish_grace_seconds - loop.time(), config.cleanup_seconds)
    return await _finish(config, job_id, outcome, run.stats, seconds=seconds)


__all__ = [
    "CANCEL_POLL_SECONDS",
    "CLEANUP_SECONDS",
    "ERROR_TIME_LIMIT",
    "ERROR_WORKER_LOST",
    "FINISH_GRACE_SECONDS",
    "MAX_FINDINGS_PER_JOB",
    "MAX_TOUCHED_KEYS",
    "Outcome",
    "RunnerConfig",
    "Stats",
    "bounded",
    "finish_abandoned",
    "reap_stale_jobs",
    "run_job",
]
