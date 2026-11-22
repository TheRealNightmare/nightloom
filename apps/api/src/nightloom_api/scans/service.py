"""Scan runs: a profile's Shuttles on one or more entities, released in batches.

A run is **planned** when it is created: every (Shuttle, entity) pair the
profile allows, entity by entity in the profile's order, skipping pairs the
Shuttle does not accept, that the case's person gate refuses, or whose
Shuttle is not installed. The plan is stored with the run; nothing runs yet.

Jobs are then **released** from the plan into the queue only while the
requester has fewer queued or running jobs than the per-user limit
(``NIGHTLOOM_MAX_ACTIVE_JOBS_PER_USER``, 5): right after the run is created,
after each of the requester's run jobs finishes (the worker), and every
minute from the worker's reaper as a safety net. Releasing takes the same
per-user lock as queueing a single job, so the limit holds. A released job
is an ordinary job (``jobs.run_id`` names the run), checked again by the
worker when it starts. A run whose case is no longer open, or whose
requester may no longer run Shuttles, is **stopped**; one whose jobs have all
been released and finished is **finished**; cancelling a run cancels its
queued jobs, asks its running ones to stop and releases nothing more.

A **pivot** (a run started with ``pivot``, in a case whose owner allows
auto-pivot) also continues from what its jobs find. Each time jobs are
released, the pivot's finished jobs are **expanded** first: the
infrastructure entities a job created or updated (never person-related types
or organizations) that the pivot has not seen yet are planned with the same
profile's Shuttles, one hop further, up to :data:`PIVOT_MAX_DEPTH` hops from
the seeds. A pivot plans at most :data:`PIVOT_MAX_JOBS` jobs and
:data:`PIVOT_MAX_ENTITIES` new entities; reaching either stops the expansion
(``capped``) while what was planned runs to the end. Expanding happens under
the same locks as releasing, before the run can be called finished.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.service import Actor
from nightloom_api.auth import clock
from nightloom_api.auth.models import User
from nightloom_api.cases.models import Case
from nightloom_api.jobs import service as jobs
from nightloom_api.jobs.models import (
    ACTIVE_STATUSES,
    FINISHED_STATUSES,
    Job,
    JobStatus,
    empty_stats,
)
from nightloom_api.jobs.queue import JobQueue
from nightloom_api.scans.models import RunStatus, ScanRun
from nightloom_api.scans.profiles import Profile
from nightloom_core.entities import EntityType, Sensitivity, sensitivity_of
from nightloom_core.normalize import NormalizationError, parse_key
from nightloom_sdk.registry import ShuttleRegistry

logger = logging.getLogger(__name__)

MAX_RUN_ENTITIES = 25
MAX_RUN_JOBS = 100
MAX_ACTIVE_RUNS_PER_USER = 2
# A pivot: hops from the seeds, and at most this many planned jobs and new entities.
PIVOT_MAX_DEPTH = 2
PIVOT_MAX_JOBS = 200
PIVOT_MAX_ENTITIES = 500

# Who queues a run's later jobs in the audit log (the requester is in the job).
RELEASE_ACTOR = Actor.system("scan-run")


class RunError(Exception):
    """``str(error)`` is safe to return to the client."""

    status_code = 422
    reason = "invalid"

    def __init__(
        self, message: str, *, reason: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason
        if status_code is not None:
            self.status_code = status_code


class TooManyRunsError(RunError):
    status_code = 429
    reason = "too_many_runs"

    def __init__(self) -> None:
        super().__init__(
            f"You already have {MAX_ACTIVE_RUNS_PER_USER} profile runs in progress. "
            "Wait for one to finish or cancel it."
        )


class RunNotFoundError(RunError):
    status_code = 404
    reason = "run_not_found"

    def __init__(self) -> None:
        super().__init__("Run not found.")


class RunFinishedError(RunError):
    status_code = 409
    reason = "run_finished"

    def __init__(self) -> None:
        super().__init__("This run has already ended.")


# --- planning --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    items: list[dict[str, str]]
    # Pairs left out, by reason ("not_accepted", "person_entities_disabled", "not_installed",
    # "not_in_pivots", ...).
    skipped: Counter[str]


def plan_run(
    profile: Profile,
    entities: Sequence[tuple[str, EntityType]],
    registry: ShuttleRegistry,
    *,
    allow_person_entities: bool,
    allow_site_visits: bool = False,
    pivot: bool = False,
) -> Plan:
    """Every (Shuttle, entity) pair the guardrails allow, entity by entity, in profile order.

    With ``pivot``, Shuttles that declare ``pivots=False`` are left out
    (``not_in_pivots``).
    """
    items: list[dict[str, str]] = []
    skipped: Counter[str] = Counter()
    for key, entity_type in entities:
        for name in profile.shuttles:
            shuttle = registry.get(name)
            if shuttle is None:
                skipped["not_installed"] += 1
                continue
            if pivot and not shuttle.meta.pivots:
                skipped["not_in_pivots"] += 1
                continue
            try:
                jobs.check_shuttle(
                    shuttle.meta,
                    input_type=entity_type,
                    allow_person_entities=allow_person_entities,
                    allow_site_visits=allow_site_visits,
                )
            except jobs.GuardrailError as error:
                skipped[
                    "not_accepted" if error.reason == "input_type_not_accepted" else error.reason
                ] += 1
                continue
            items.append({"shuttle": name, "entity_key": key})
    return Plan(items=items, skipped=skipped)


# --- creating --------------------------------------------------------------------------------


async def count_running_runs(db: AsyncSession, user_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(ScanRun)
        .where(ScanRun.requested_by == user_id, ScanRun.status == RunStatus.RUNNING.value)
    )
    return int(count or 0)


async def create_run(
    db: AsyncSession,
    *,
    case: Case,
    profile: Profile,
    plan: Plan,
    entities: int,
    user: User,
    actor: Actor,
    use_cache: bool,
    pivot: bool = False,
    seeds: Sequence[str] = (),
) -> ScanRun:
    """Insert a running run (nothing released yet) and its ``scan_run.create`` event; commits.

    With ``pivot`` the run also continues from what it finds (see the module
    docstring); ``seeds`` are the entities it starts from.
    """
    if not plan.items:
        raise RunError(
            "None of this profile's Shuttles can run on the selected entities.", reason="empty"
        )
    if len(plan.items) > MAX_RUN_JOBS:
        raise RunError(
            f"This run would queue {len(plan.items)} jobs; a run may queue at most "
            f"{MAX_RUN_JOBS}. Select fewer entities.",
            reason="too_many_jobs",
        )
    await jobs.lock_user_jobs(db, user.id)
    if await count_running_runs(db, user.id) >= MAX_ACTIVE_RUNS_PER_USER:
        raise TooManyRunsError()
    run = ScanRun(
        case_id=case.id,
        profile_id=profile.id,
        profile_name=profile.name,
        requested_by=user.id,
        status=RunStatus.RUNNING.value,
        plan=[{**item, "depth": 0} for item in plan.items] if pivot else plan.items,
        released=0,
        skipped=sum(plan.skipped.values()),
        entities=entities,
        use_cache=use_cache,
        profile_shuttles=list(profile.shuttles),
        pivot=pivot,
        max_depth=PIVOT_MAX_DEPTH if pivot else 0,
        max_jobs=PIVOT_MAX_JOBS if pivot else len(plan.items),
        max_entities=PIVOT_MAX_ENTITIES if pivot else 0,
        seen=list(dict.fromkeys(seeds)),
        discovered=0,
    )
    db.add(run)
    await db.flush()
    await audit.record(
        db,
        action=AuditAction.SCAN_RUN_CREATE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.SCAN_RUN,
        target_id=run.id,
        case_id=case.id,
        details={
            "profile": profile.id,
            "profile_name": profile.name,
            "entities": entities,
            "jobs": len(plan.items),
            "skipped": dict(sorted(plan.skipped.items())),
            **({} if use_cache else {"fresh": True}),
            **({"pivot": True} if pivot else {}),
        },
    )
    await db.commit()
    return run


# --- releasing -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Released:
    job_id: uuid.UUID
    timeout_seconds: int


async def _active_in_run(db: AsyncSession, run_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.run_id == run_id, Job.status.in_(ACTIVE_STATUSES))
    )
    return int(count or 0)


def pivots_through(entity_type: EntityType) -> bool:
    """Whether a pivot may start from or continue through entities of this type."""
    return sensitivity_of(entity_type) == Sensitivity.INFRASTRUCTURE


async def expand(db: AsyncSession, run: ScanRun, registry: ShuttleRegistry) -> int:
    """Plan what a pivot's finished jobs found, one hop further. Returns how many entities.

    Every finished job is expanded once (``pivot_expanded``). The run and its
    jobs must be locked by the caller (see :func:`release`).
    """
    jobs_done = list(
        (
            await db.execute(
                select(Job)
                .where(
                    Job.run_id == run.id,
                    Job.pivot_expanded.is_(False),
                    Job.status.in_(FINISHED_STATUSES),
                )
                .order_by(Job.created_at, Job.id)
                .with_for_update()
            )
        ).scalars()
    )
    if not jobs_done:
        return 0
    profile = Profile(
        id=run.profile_id,
        name=run.profile_name,
        description="",
        shuttles=tuple(run.profile_shuttles),
        built_in=False,
    )
    seen = list(run.seen)
    known = set(seen)
    items = list(run.plan)
    found = 0
    for job in jobs_done:
        job.pivot_expanded = True
        depth = job.pivot_depth
        if run.capped or job.status == JobStatus.CANCELLED:
            continue
        if depth is None or depth >= run.max_depth:
            continue
        for key in job.stats.get("entities_touched") or []:
            if not isinstance(key, str) or key in known:
                continue
            try:
                entity_type = parse_key(key)[0]
            except NormalizationError:
                continue
            # Only infrastructure: never person-related types or organizations.
            if not pivots_through(entity_type):
                continue
            if run.discovered >= run.max_entities:
                run.capped = "entities"
                break
            plan = plan_run(
                profile, [(key, entity_type)], registry, allow_person_entities=False, pivot=True
            )
            if len(items) + len(plan.items) > run.max_jobs:
                run.capped = "jobs"
                break
            known.add(key)
            seen.append(key)
            run.discovered += 1
            found += 1
            run.skipped += sum(plan.skipped.values())
            items.extend({**item, "depth": depth + 1} for item in plan.items)
    # New lists, so the JSON columns are written.
    run.plan = items
    run.seen = seen
    return found


def _end(run: ScanRun, status: RunStatus, reason: str | None = None) -> None:
    run.status = status.value
    run.finished_at = clock.utcnow()
    run.stop_reason = reason


async def release(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    limit: int,
    registry: ShuttleRegistry,
) -> list[Released]:
    """Queue the user's next planned jobs while they have free job slots. Commits.

    Returns the new jobs, which the caller hands to the queue (after this
    commit, so the worker never receives a job that does not exist). Also
    stops runs that may not go on and finishes runs that are done.
    """
    released: list[Released] = []
    async with sessionmaker() as db, db.begin():
        await jobs.lock_user_jobs(db, user_id)
        free = limit - await jobs.count_active_jobs(db, user_id)
        runs = list(
            (
                await db.execute(
                    select(ScanRun)
                    .where(
                        ScanRun.requested_by == user_id,
                        ScanRun.status == RunStatus.RUNNING.value,
                    )
                    .order_by(ScanRun.created_at, ScanRun.id)
                    .with_for_update()
                )
            ).scalars()
        )
        for run in runs:
            if run.pivot:
                await expand(db, run, registry)
            if run.released < run.total:
                case = await jobs.lock_case_for_job(db, run.case_id)
                if not jobs.case_is_open(case):
                    _end(run, RunStatus.STOPPED, "case_not_open")
                    continue
                if not await jobs.requester_may_run(db, run.case_id, user_id):
                    _end(run, RunStatus.STOPPED, "requester_not_allowed")
                    continue
            while free > 0 and run.released < run.total:
                item: dict[str, Any] = run.plan[run.released]
                run.released += 1
                shuttle = registry.get(str(item.get("shuttle")))
                if shuttle is None:
                    run.skipped += 1
                    continue
                job = Job(
                    case_id=run.case_id,
                    shuttle_name=shuttle.meta.name,
                    shuttle_version=shuttle.meta.version,
                    input_key=str(item["entity_key"]),
                    requested_by=user_id,
                    status=JobStatus.QUEUED,
                    stats=empty_stats(),
                    use_cache=run.use_cache,
                    run_id=run.id,
                    pivot_depth=int(item.get("depth", 0)) if run.pivot else None,
                )
                db.add(job)
                await db.flush()
                await audit.record(
                    db,
                    action=AuditAction.JOB_CREATE,
                    outcome=AuditOutcome.SUCCESS,
                    actor=RELEASE_ACTOR,
                    target_type=TargetType.JOB,
                    target_id=job.id,
                    case_id=run.case_id,
                    details=jobs.job_details(
                        job,
                        mode=shuttle.meta.mode.value,
                        sensitivity=shuttle.meta.sensitivity.value,
                        run_id=str(run.id),
                        **({} if run.use_cache else {"fresh": True}),
                    ),
                )
                released.append(Released(job.id, shuttle.meta.timeout_seconds))
                free -= 1
            if run.released >= run.total and await _active_in_run(db, run.id) == 0:
                _end(run, RunStatus.FINISHED)
    return released


async def enqueue(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    released: Sequence[Released],
) -> None:
    """Hand released jobs to the worker; a job that cannot be queued is failed."""
    for item in released:
        try:
            await queue.enqueue(item.job_id, timeout_seconds=item.timeout_seconds)
        except Exception as error:
            logger.error("Could not queue job %s: %s", item.job_id, type(error).__name__)
            async with sessionmaker() as db:
                job = await db.get(Job, item.job_id)
                if job is not None and job.status == JobStatus.QUEUED:
                    await jobs.finish_job(
                        db,
                        job,
                        status=JobStatus.FAILED,
                        actor=RELEASE_ACTOR,
                        error=jobs.ERROR_QUEUE,
                        reason="queue_unavailable",
                    )


async def release_and_enqueue(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    *,
    user_id: uuid.UUID,
    limit: int,
    registry: ShuttleRegistry,
) -> int:
    """:func:`release`, then :func:`enqueue`. Returns how many jobs were released."""
    released = await release(sessionmaker, user_id=user_id, limit=limit, registry=registry)
    await enqueue(sessionmaker, queue, released)
    return len(released)


async def users_with_running_runs(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.execute(
        select(ScanRun.requested_by)
        .where(ScanRun.status == RunStatus.RUNNING.value, ScanRun.requested_by.is_not(None))
        .distinct()
    )
    return [user_id for user_id in rows.scalars() if user_id is not None]


async def stop_orphaned_runs(db: AsyncSession) -> int:
    """End running runs whose requester was deleted (nobody can release them). Caller commits."""
    runs = list(
        (
            await db.execute(
                select(ScanRun)
                .where(ScanRun.status == RunStatus.RUNNING.value, ScanRun.requested_by.is_(None))
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for run in runs:
        _end(run, RunStatus.STOPPED, "requester_not_allowed")
    return len(runs)


# --- reading and cancelling ------------------------------------------------------------------


async def get_run(
    db: AsyncSession, case_id: uuid.UUID, run_id: uuid.UUID, *, lock: bool = False
) -> ScanRun:
    statement = select(ScanRun).where(ScanRun.id == run_id, ScanRun.case_id == case_id)
    if lock:
        statement = statement.with_for_update()
    run = (await db.execute(statement)).scalar_one_or_none()
    if run is None:
        raise RunNotFoundError()
    return run


async def list_runs(db: AsyncSession, case_id: uuid.UUID, *, limit: int) -> list[ScanRun]:
    """The case's runs, newest first."""
    return list(
        (
            await db.execute(
                select(ScanRun)
                .where(ScanRun.case_id == case_id)
                .order_by(ScanRun.created_at.desc(), ScanRun.id.desc())
                .limit(limit)
            )
        ).scalars()
    )


async def job_counts(
    db: AsyncSession, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Counter[str]]:
    """Each run's released jobs by status."""
    counts: dict[uuid.UUID, Counter[str]] = {run_id: Counter() for run_id in run_ids}
    if not run_ids:
        return counts
    rows = await db.execute(
        select(Job.run_id, Job.status, func.count())
        .where(Job.run_id.in_(run_ids))
        .group_by(Job.run_id, Job.status)
    )
    for run_id, status, count in rows.all():
        if run_id is not None:
            counts[run_id][JobStatus(status).value] = int(count)
    return counts


async def cancel_run(db: AsyncSession, run: ScanRun, *, actor: Actor) -> ScanRun:
    """Stop releasing, cancel queued jobs and ask running ones to stop; commits.

    ``run`` must have been loaded with ``lock=True``.
    """
    if run.status != RunStatus.RUNNING.value:
        raise RunFinishedError()
    _end(run, RunStatus.CANCELLED)
    active = list(
        (
            await db.execute(
                select(Job)
                .where(Job.run_id == run.id, Job.status.in_(ACTIVE_STATUSES))
                .with_for_update()
            )
        ).scalars()
    )
    for job in active:
        await jobs.cancel_job(db, job, actor=actor, commit=False)
    await audit.record(
        db,
        action=AuditAction.SCAN_RUN_CANCEL,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.SCAN_RUN,
        target_id=run.id,
        case_id=run.case_id,
        details={
            "profile": run.profile_id,
            "jobs_cancelled": len(active),
            "not_released": run.total - run.released,
        },
    )
    await db.commit()
    return run


__all__ = [
    "MAX_ACTIVE_RUNS_PER_USER",
    "MAX_RUN_ENTITIES",
    "MAX_RUN_JOBS",
    "PIVOT_MAX_DEPTH",
    "PIVOT_MAX_ENTITIES",
    "PIVOT_MAX_JOBS",
    "Plan",
    "Released",
    "RunError",
    "RunFinishedError",
    "RunNotFoundError",
    "TooManyRunsError",
    "cancel_run",
    "create_run",
    "enqueue",
    "expand",
    "get_run",
    "job_counts",
    "list_runs",
    "pivots_through",
    "plan_run",
    "release",
    "release_and_enqueue",
    "stop_orphaned_runs",
    "users_with_running_runs",
]
