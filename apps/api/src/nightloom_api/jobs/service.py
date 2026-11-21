"""Shuttle jobs: the guardrails, queueing, listing, cancelling and finishing.

The API checks every guardrail before it queues a job, and the worker checks
them all again when the job starts (see ``nightloom_worker.runner``), using
the same functions from here: a case can be closed, a member removed or the
person gate turned off while a job waits in the queue.

Errors are :class:`JobError` subclasses whose message is safe to return.
"""

import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.service import Actor
from nightloom_api.auth import clock
from nightloom_api.auth.models import User
from nightloom_api.cases.access import Capability, can, effective_role
from nightloom_api.cases.models import Case, CaseMember, CaseStatus
from nightloom_api.cases.service import decode_cursor, encode_cursor
from nightloom_api.graph.privacy import key_details
from nightloom_api.jobs.models import (
    ACTIVE_STATUSES,
    ERROR_MAX_LENGTH,
    FINISHED_STATUSES,
    Job,
    JobStatus,
    empty_stats,
)
from nightloom_core.entities import EntityType, Sensitivity, sensitivity_of
from nightloom_core.normalize import NormalizationError, parse_key
from nightloom_sdk.meta import Mode, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry, default_registry

# pg_advisory_xact_lock(int4, int4) class for "jobs of one user"; the second
# key is the hash of the user's ID. Two-key locks never collide with the audit
# log's one-key lock.
_USER_JOBS_LOCK_CLASS = 0x6E6A  # "nj"

# Stats that stay out of the audit log: entity keys (which can be personal
# data) and the Shuttle's free-text notes are never recorded there, only
# counts and reason codes.
_UNAUDITED_STATS = frozenset({"entities_touched", "notes"})

# Fixed, user-safe messages: the only text ever stored in jobs.error.
ERROR_ACTIVE = (
    "Active Shuttles run only on targets whose ownership is verified in this case. "
    "A case owner can verify the domain in the case's settings."
)
ERROR_PERSON = "This case does not allow person-related entities."
ERROR_CASE_NOT_OPEN = "This case is not open. Reopen it to run Shuttles."
ERROR_NOT_ALLOWED = "The requester may no longer run Shuttles in this case."
ERROR_NOT_INSTALLED = "This Shuttle is no longer installed."
ERROR_ENTITY_GONE = "The input entity no longer exists in this case."
ERROR_QUEUE = "The job could not be queued. Try again later."


class JobError(Exception):
    """Base class; ``str(error)`` is safe to return to the client."""

    status_code = 422
    # The audit `reason`; refusals with status 403 are recorded as authz.denied.
    reason = "invalid"

    def __init__(
        self, message: str, *, reason: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason
        if status_code is not None:
            self.status_code = status_code


class UnknownShuttleError(JobError):
    reason = "unknown_shuttle"

    def __init__(self) -> None:
        super().__init__("Unknown Shuttle.")


class GuardrailError(JobError):
    """A guardrail refuses this Shuttle for this case or input."""


class EntityNotFoundError(JobError):
    reason = "entity_not_found"

    def __init__(self) -> None:
        super().__init__("entity_key does not name an entity in this case.")


class TooManyJobsError(JobError):
    status_code = 429
    reason = "too_many_jobs"

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"You already have {limit} queued or running jobs. Wait for one to finish."
        )


class JobNotFoundError(JobError):
    status_code = 404
    reason = "job_not_found"

    def __init__(self) -> None:
        super().__init__("Job not found.")


class JobFinishedError(JobError):
    status_code = 409
    reason = "job_finished"

    def __init__(self) -> None:
        super().__init__("This job has already finished.")


@lru_cache(maxsize=1)
def get_shuttle_registry() -> ShuttleRegistry:
    """FastAPI dependency: the installed Shuttles (overridden in tests)."""
    return default_registry()


# --- guardrails --------------------------------------------------------------------------


def check_shuttle(
    meta: ShuttleMeta,
    *,
    input_type: EntityType,
    allow_person_entities: bool,
    target_verified: bool = False,
) -> None:
    """Raise :class:`GuardrailError` unless ``meta`` may run on ``input_type`` in such a case.

    - the Shuttle accepts the input's type (422);
    - it is passive, or ``target_verified``: an active Shuttle runs only on a
      target a verification of the case covers (403, ``target_not_verified``;
      see :mod:`nightloom_api.ownership`);
    - a person-sensitive Shuttle, or a person-related input, needs a case
      that allows person entities (403, ``person_entities_disabled``).
    """
    if not meta.accepts(input_type):
        raise GuardrailError(
            f"This Shuttle does not accept {EntityType(input_type).value} entities.",
            reason="input_type_not_accepted",
        )
    if meta.mode != Mode.PASSIVE and not target_verified:
        raise GuardrailError(ERROR_ACTIVE, reason="target_not_verified", status_code=403)
    personal = Sensitivity.PERSON in (meta.sensitivity, sensitivity_of(input_type))
    if personal and not allow_person_entities:
        raise GuardrailError(ERROR_PERSON, reason="person_entities_disabled", status_code=403)


async def requester_may_run(
    db: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID | None
) -> bool:
    """Whether the user is still an active member of the case allowed to run Shuttles."""
    if user_id is None:
        return False
    row = (
        await db.execute(
            select(User.role, CaseMember.role)
            .join(CaseMember, CaseMember.user_id == User.id)
            .where(CaseMember.case_id == case_id, User.id == user_id, User.is_active)
        )
    ).first()
    if row is None:
        return False
    global_role, case_role = row
    return can(effective_role(global_role, case_role), Capability.RUN_SHUTTLES)


def input_sensitivity(input_key: str) -> tuple[EntityType, Sensitivity]:
    try:
        entity_type = parse_key(input_key)[0]
    except NormalizationError:
        # Not expected (keys are checked before a job exists); hash it to be safe.
        return EntityType.PERSON, Sensitivity.PERSON
    return entity_type, sensitivity_of(entity_type)


def job_details(job: Job, **extra: Any) -> dict[str, Any]:
    """The audit details of a job: Shuttle, version, input (hashed if personal)."""
    entity_type, sensitivity = input_sensitivity(job.input_key)
    return {
        "job_id": str(job.id),
        "shuttle": job.shuttle_name,
        "version": job.shuttle_version,
        "entity_type": entity_type.value,
        **key_details(job.input_key, sensitivity, "input_key"),
        **extra,
    }


# --- creating ------------------------------------------------------------------------------


async def lock_user_jobs(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Serialize job creation per user for the rest of the transaction."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:class, hashtext(:user))"),
        {"class": _USER_JOBS_LOCK_CLASS, "user": str(user_id)},
    )


async def count_active_jobs(db: AsyncSession, user_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.requested_by == user_id, Job.status.in_(ACTIVE_STATUSES))
    )
    return int(count or 0)


async def create_job(
    db: AsyncSession,
    *,
    case: Case,
    meta: ShuttleMeta,
    input_key: str,
    user: User,
    actor: Actor,
    limit: int,
    use_cache: bool = True,
) -> Job:
    """Insert a queued job and its ``job.create`` event, and commit.

    ``use_cache=False`` (a fresh run) makes the worker run the Shuttle even
    when recent results are cached; the run still refreshes the cache.

    Holds a per-user lock while counting, so concurrent requests cannot get
    past the limit of ``limit`` queued or running jobs together.
    """
    await lock_user_jobs(db, user.id)
    if await count_active_jobs(db, user.id) >= limit:
        raise TooManyJobsError(limit)
    job = Job(
        case_id=case.id,
        shuttle_name=meta.name,
        shuttle_version=meta.version,
        input_key=input_key,
        requested_by=user.id,
        status=JobStatus.QUEUED,
        stats=empty_stats(),
        use_cache=use_cache,
    )
    db.add(job)
    await db.flush()
    await audit.record(
        db,
        action=AuditAction.JOB_CREATE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.JOB,
        target_id=job.id,
        case_id=case.id,
        details=job_details(
            job,
            mode=meta.mode.value,
            sensitivity=meta.sensitivity.value,
            **({} if use_cache else {"fresh": True}),
        ),
    )
    await db.commit()
    return job


# --- reading -------------------------------------------------------------------------------


@dataclass(frozen=True)
class JobListing:
    items: list[Job]
    next_cursor: str | None


async def list_jobs(
    db: AsyncSession,
    case_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
    status: JobStatus | None = None,
) -> JobListing:
    """A case's jobs, newest first (keyset pagination on ``(created_at, id)``)."""
    statement = select(Job).where(Job.case_id == case_id)
    if status is not None:
        statement = statement.where(Job.status == status)
    if cursor is not None:
        created_at, job_id = decode_cursor(cursor)
        statement = statement.where(
            or_(
                Job.created_at < created_at,
                and_(Job.created_at == created_at, Job.id < job_id),
            )
        )
    rows = list(
        (
            await db.execute(
                statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit + 1)
            )
        ).scalars()
    )
    last = rows[limit - 1] if len(rows) > limit else None
    return JobListing(
        items=rows[:limit],
        next_cursor=encode_cursor(last.created_at, last.id) if last else None,
    )


async def get_job(
    db: AsyncSession, case_id: uuid.UUID, job_id: uuid.UUID, *, lock: bool = False
) -> Job:
    statement = select(Job).where(Job.id == job_id, Job.case_id == case_id)
    if lock:
        statement = statement.with_for_update()
    job = (await db.execute(statement)).scalar_one_or_none()
    if job is None:
        raise JobNotFoundError()
    return job


# --- cancelling and finishing ----------------------------------------------------------------


async def cancel_job(db: AsyncSession, job: Job, *, actor: Actor, commit: bool = True) -> Job:
    """Cancel a queued job at once, or ask the worker to stop a running one; commits.

    ``job`` must have been loaded with ``lock=True``. With ``commit=False`` the
    caller commits (cancelling a scan run's jobs together).
    """
    if job.status in FINISHED_STATUSES:
        raise JobFinishedError()
    before = job.status
    job.cancel_requested = True
    if job.status == JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        job.finished_at = clock.utcnow()
    await audit.record(
        db,
        action=AuditAction.JOB_CANCEL,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.JOB,
        target_id=job.id,
        case_id=job.case_id,
        details=job_details(job, status_before=before.value, status=job.status.value),
    )
    if commit:
        await db.commit()
    return job


_OUTCOMES: Mapping[JobStatus, AuditOutcome] = {
    JobStatus.SUCCEEDED: AuditOutcome.SUCCESS,
    JobStatus.CANCELLED: AuditOutcome.SUCCESS,
    JobStatus.FAILED: AuditOutcome.FAILURE,
}


async def finish_job(
    db: AsyncSession,
    job: Job,
    *,
    status: JobStatus,
    actor: Actor,
    stats: Mapping[str, Any] | None = None,
    error: str | None = None,
    reason: str | None = None,
    denied: bool = False,
) -> Job:
    """Set the job's final state and record ``job.finish`` in the same transaction; commits.

    ``error`` must be one of the fixed, user-safe messages (never an
    exception's text); ``reason`` is a short code for the audit event.
    ``denied`` records the event as ``denied`` (a guardrail stopped the job).
    The event carries the counts from ``stats``, never ``entities_touched`` or ``notes``.
    """
    if status not in FINISHED_STATUSES:
        raise ValueError("finish_job needs a final status")
    job.status = status
    job.finished_at = clock.utcnow()
    job.error = error[:ERROR_MAX_LENGTH] if error else None
    if stats is not None:
        job.stats = dict(stats)
    await _record_finish(db, job, actor=actor, reason=reason, denied=denied)
    await db.commit()
    return job


async def finish_job_if(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    only_if: Collection[JobStatus],
    status: JobStatus,
    actor: Actor,
    stats: Mapping[str, Any] | None = None,
    error: str | None = None,
    reason: str | None = None,
    denied: bool = False,
) -> Job | None:
    """Like :func:`finish_job`, but only while the job's status is one of ``only_if``; commits.

    One conditional ``UPDATE ... WHERE status IN (...)``: of a worker
    finishing the job and the reaper failing it at the same moment, exactly
    one wins and records ``job.finish``. Returns the finished job, or None if
    it no longer exists or its status was not in ``only_if`` (nothing changed).
    """
    if status not in FINISHED_STATUSES:
        raise ValueError("finish_job_if needs a final status")
    values: dict[str, Any] = {
        "status": status,
        "finished_at": clock.utcnow(),
        "error": error[:ERROR_MAX_LENGTH] if error else None,
    }
    if stats is not None:
        values["stats"] = dict(stats)
    job = (
        await db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(list(only_if)))
            .values(**values)
            .returning(Job)
            .execution_options(populate_existing=True, synchronize_session=False)
        )
    ).scalar_one_or_none()
    if job is None:
        await db.rollback()
        return None
    await _record_finish(db, job, actor=actor, reason=reason, denied=denied)
    await db.commit()
    return job


async def _record_finish(
    db: AsyncSession, job: Job, *, actor: Actor, reason: str | None, denied: bool
) -> None:
    """The ``job.finish`` event: counts and reason codes, never keys or notes."""
    audited = {k: v for k, v in (job.stats or {}).items() if k not in _UNAUDITED_STATS}
    extra: dict[str, Any] = {"status": job.status.value, "stats": audited}
    if reason:
        extra["reason"] = reason
    await audit.record(
        db,
        action=AuditAction.JOB_FINISH,
        outcome=AuditOutcome.DENIED if denied else _OUTCOMES[job.status],
        actor=actor,
        target_type=TargetType.JOB,
        target_id=job.id,
        case_id=job.case_id,
        details=job_details(job, **extra),
    )


async def mark_running(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """Move a queued, not cancelled job to ``running``; commits. False if it was not queued."""
    result = await db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.QUEUED, Job.cancel_requested.is_(False))
        .values(status=JobStatus.RUNNING, started_at=clock.utcnow())
        .returning(Job.id)
    )
    started = result.first() is not None
    await db.commit()
    return started


async def mark_from_cache(db: AsyncSession, job_id: uuid.UUID, fetched_at: datetime) -> None:
    """Record that a running job is answered from the result cache. The caller commits."""
    await db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
        .values(from_cache=True, cached_fetched_at=fetched_at)
    )


async def cancel_requested(db: AsyncSession, job_id: uuid.UUID) -> bool:
    value = await db.scalar(select(Job.cancel_requested).where(Job.id == job_id))
    return bool(value)


async def lock_case_for_job(db: AsyncSession, case_id: uuid.UUID) -> Case | None:
    """The case, share-locked until the transaction ends.

    ``FOR SHARE`` conflicts with the ``FOR NO KEY UPDATE`` lock every case
    change takes, so the case cannot be closed, changed or deleted while the
    worker writes a finding, while several workers can still write at once.
    """
    return (
        await db.execute(
            select(Case)
            .where(Case.id == case_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


def case_is_open(case: Case | None) -> bool:
    return case is not None and case.status == CaseStatus.OPEN


__all__ = [
    "ERROR_ACTIVE",
    "ERROR_CASE_NOT_OPEN",
    "ERROR_ENTITY_GONE",
    "ERROR_NOT_ALLOWED",
    "ERROR_NOT_INSTALLED",
    "ERROR_PERSON",
    "ERROR_QUEUE",
    "EntityNotFoundError",
    "GuardrailError",
    "JobError",
    "JobFinishedError",
    "JobNotFoundError",
    "TooManyJobsError",
    "UnknownShuttleError",
    "cancel_job",
    "cancel_requested",
    "case_is_open",
    "check_shuttle",
    "count_active_jobs",
    "create_job",
    "finish_job",
    "finish_job_if",
    "get_job",
    "get_shuttle_registry",
    "job_details",
    "list_jobs",
    "lock_case_for_job",
    "mark_running",
    "requester_may_run",
]
