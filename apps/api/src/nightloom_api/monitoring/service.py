"""Certificate Transparency monitoring: watched domains, hourly crt.sh checks, alerts.

A case **owner** watches a Domain that is in the case's graph (at most
:data:`MAX_PER_CASE`). Every :data:`CHECK_EVERY` the worker's sweep queues an
ordinary crt.sh job for it: requested by the owner who added it (it counts
against their job limit, and runs only while they may run Shuttles in the
case and the case is open), audited as ``job.create`` by
``system:ct-monitor``, and run **without the result cache**, so crt.sh is
asked again. Everything a job does is checked as for any job.

When the job finishes, the sweep reads the keys of the entities it
**created** (``stats.entities_new``): new Certificates and new names (Domains
other than the watched one) become a :class:`CtAlert`, shown to the case's
members until an editor or owner marks it seen. The first successful check
only records the **baseline** (everything would be new). Entities that were
already in the graph, for example from a manual crt.sh run, are not new.

crt.sh is often busy: a failed check is simply tried again at the next hour
(``check_failed``); nothing is lost, since the next check asks for all
certificates again.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.service import Actor
from nightloom_api.auth.models import User
from nightloom_api.jobs import service as jobs
from nightloom_api.jobs.models import FINISHED_STATUSES, Job, JobStatus
from nightloom_api.jobs.queue import JobQueue
from nightloom_api.monitoring.models import CtAlert, CtWatch
from nightloom_core.entities import EntityType
from nightloom_core.normalize import (
    NormalizationError,
    is_public_suffix,
    normalize_domain,
    parse_key,
)
from nightloom_sdk.registry import ShuttleRegistry

logger = logging.getLogger(__name__)

SHUTTLE = "crtsh"
CHECK_EVERY = timedelta(hours=1)
# When the owner's job limit is full, try again this soon.
RETRY_SOON = timedelta(minutes=5)
# An owner's "Check now" may not be repeated within this.
CHECK_COOLDOWN = timedelta(minutes=1)
MAX_PER_CASE = 20
# What an alert keeps: the first new names (for the text), the first new keys
# (for "show in graph").
MAX_ALERT_NAMES = 20
MAX_ALERT_KEYS = 200
# The newest alerts a case's list returns.
MAX_ALERTS_LISTED = 50
# Watches the sweep queues at once.
SWEEP_LIMIT = 20

SYSTEM_ACTOR = Actor.system("ct-monitor")

# Why the last check did not run or did not succeed (shown in the web UI).
PROBLEMS: dict[str, str] = {
    "case_closed": "The case is closed; checks resume when it is reopened.",
    "owner_cannot_run": (
        "The owner who added this domain can no longer run Shuttles in this case. "
        "Remove it and add it again."
    ),
    "shuttle_missing": "The crt.sh Shuttle is not installed.",
    "job_limit": "Waiting: the owner who added it has too many jobs queued or running.",
    "entity_gone": "The domain is no longer in the graph. Add it again, or remove this watch.",
    "check_failed": "The last check failed (crt.sh is often busy); it is tried again next hour.",
    "check_cancelled": "The last check was cancelled; it is tried again next hour.",
}


class MonitoringError(ValueError):
    """Refused: the message is for the user; ``status_code`` for the API."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def clean_domain(value: str) -> str:
    """The canonical domain, refused if it is a wildcard or a public suffix."""
    try:
        domain = normalize_domain(value)
    except NormalizationError as error:
        raise MonitoringError(str(error)) from error
    if domain.startswith("*."):
        raise MonitoringError("Watch the domain itself, without a wildcard: it covers subdomains.")
    if is_public_suffix(domain):
        raise MonitoringError("This is a public suffix (like co.uk); watch a domain under it.")
    return domain


def domain_key(domain: str) -> str:
    """The graph key of a (normalized) domain."""
    return f"{EntityType.DOMAIN.value}:{domain}"


# --- watches -------------------------------------------------------------------------------


async def list_watches(db: AsyncSession, case_id: uuid.UUID) -> list[CtWatch]:
    return list(
        (
            await db.execute(
                select(CtWatch).where(CtWatch.case_id == case_id).order_by(CtWatch.domain)
            )
        ).scalars()
    )


async def get_watch(
    db: AsyncSession, case_id: uuid.UUID, watch_id: uuid.UUID, *, lock: bool = False
) -> CtWatch | None:
    statement = select(CtWatch).where(CtWatch.case_id == case_id, CtWatch.id == watch_id)
    if lock:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


async def create_watch(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    domain: str,
    user_id: uuid.UUID,
    actor: Actor,
    now: datetime,
) -> CtWatch:
    """Watch a domain (first check at once) and record ``ct_watch.create`` (no commit)."""
    count = await db.scalar(
        select(func.count()).select_from(CtWatch).where(CtWatch.case_id == case_id)
    )
    if (count or 0) >= MAX_PER_CASE:
        raise MonitoringError(f"A case can watch at most {MAX_PER_CASE} domains. Remove one first.")
    taken = await db.scalar(
        select(CtWatch.id).where(CtWatch.case_id == case_id, CtWatch.domain == domain)
    )
    if taken is not None:
        raise MonitoringError("This domain is already watched.", status_code=409)
    watch = CtWatch(case_id=case_id, domain=domain, created_by=user_id, next_check_at=now)
    db.add(watch)
    await db.flush()
    await audit.record(
        db,
        action=AuditAction.CT_WATCH_CREATE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.CT_WATCH,
        target_id=str(watch.id),
        case_id=case_id,
        details={"domain": domain},
    )
    return watch


async def delete_watch(db: AsyncSession, watch: CtWatch, *, actor: Actor) -> None:
    """Stop watching and record ``ct_watch.delete`` (no commit). Its alerts stay."""
    await audit.record(
        db,
        action=AuditAction.CT_WATCH_DELETE,
        outcome=AuditOutcome.SUCCESS,
        actor=actor,
        target_type=TargetType.CT_WATCH,
        target_id=str(watch.id),
        case_id=watch.case_id,
        details={"domain": watch.domain},
    )
    await db.delete(watch)


def request_check(watch: CtWatch, now: datetime) -> bool:
    """Move the next check to now (an owner's *Check now*). False if one is under way or soon."""
    if watch.pending_job_id is not None:
        return False
    if watch.checked_at is not None and now - watch.checked_at < CHECK_COOLDOWN:
        return False
    watch.next_check_at = now
    return True


# --- alerts --------------------------------------------------------------------------------


async def list_alerts(db: AsyncSession, case_id: uuid.UUID, *, unseen_only: bool) -> list[CtAlert]:
    """The case's newest alerts (at most :data:`MAX_ALERTS_LISTED`), newest first."""
    statement = select(CtAlert).where(CtAlert.case_id == case_id)
    if unseen_only:
        statement = statement.where(CtAlert.acknowledged_at.is_(None))
    statement = statement.order_by(CtAlert.created_at.desc(), CtAlert.id.desc()).limit(
        MAX_ALERTS_LISTED
    )
    return list((await db.execute(statement)).scalars())


async def count_unseen(db: AsyncSession, case_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(CtAlert)
        .where(CtAlert.case_id == case_id, CtAlert.acknowledged_at.is_(None))
    )
    return int(count or 0)


async def acknowledge(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    alert_ids: Sequence[uuid.UUID] | None,
    user: User,
    actor: Actor,
    now: datetime,
) -> int:
    """Mark the case's unseen alerts seen (all, or the given ones); returns how many (no commit).

    One ``ct_alert.acknowledge`` event with the count, if any changed.
    """
    statement = (
        update(CtAlert)
        .where(CtAlert.case_id == case_id, CtAlert.acknowledged_at.is_(None))
        .values(acknowledged_at=now, acknowledged_by=user.id)
        .returning(CtAlert.id)
        .execution_options(synchronize_session=False)
    )
    if alert_ids is not None:
        statement = statement.where(CtAlert.id.in_(list(alert_ids)))
    changed = list((await db.execute(statement)).scalars())
    if changed:
        await audit.record(
            db,
            action=AuditAction.CT_ALERT_ACKNOWLEDGE,
            outcome=AuditOutcome.SUCCESS,
            actor=actor,
            target_type=TargetType.CASE,
            target_id=str(case_id),
            case_id=case_id,
            details={"alerts": len(changed)},
        )
    return len(changed)


@dataclass(frozen=True)
class News:
    """What a check added: new certificates and new names (Domains other than the watched one)."""

    certificates: int
    names: list[str]
    keys: list[str]

    @property
    def empty(self) -> bool:
        return self.certificates == 0 and not self.names


def news_of(job: Job, domain: str) -> News:
    """The certificates and names ``job`` created (from ``stats.entities_new``)."""
    raw = (job.stats or {}).get("entities_new") or []
    certificates = 0
    names: list[str] = []
    keys: list[str] = []
    for key in raw if isinstance(raw, list) else []:
        if not isinstance(key, str):
            continue
        try:
            entity_type, value = parse_key(key)
        except NormalizationError:
            continue
        if entity_type == EntityType.CERTIFICATE:
            certificates += 1
        elif entity_type == EntityType.DOMAIN and value != domain:
            names.append(value)
        else:
            continue
        keys.append(key)
    return News(certificates=certificates, names=names, keys=keys[:MAX_ALERT_KEYS])


async def record_alert(db: AsyncSession, watch: CtWatch, job: Job, news: News) -> CtAlert:
    """Add an alert for ``news`` and record ``ct_alert.create`` (no commit)."""
    alert = CtAlert(
        case_id=watch.case_id,
        watch_id=watch.id,
        job_id=job.id,
        domain=watch.domain,
        certificates=news.certificates,
        names=news.names[:MAX_ALERT_NAMES],
        names_total=len(news.names),
        keys=news.keys,
    )
    db.add(alert)
    await db.flush()
    await audit.record(
        db,
        action=AuditAction.CT_ALERT_CREATE,
        outcome=AuditOutcome.SUCCESS,
        actor=SYSTEM_ACTOR,
        target_type=TargetType.CT_ALERT,
        target_id=str(alert.id),
        case_id=watch.case_id,
        details={
            "domain": watch.domain,
            "certificates": news.certificates,
            "names": len(news.names),
            "job_id": str(job.id),
        },
    )
    return alert


# --- the sweep (worker) --------------------------------------------------------------------


async def collect(db: AsyncSession, now: datetime) -> int:
    """Read the checks that finished; record alerts and the baseline (commits). Returns alerts."""
    rows = (
        await db.execute(
            select(CtWatch, Job)
            .join(Job, Job.id == CtWatch.pending_job_id)
            .where(Job.status.in_(list(FINISHED_STATUSES)))
            .with_for_update(of=CtWatch, skip_locked=True)
            .limit(100)
        )
    ).all()
    alerts = 0
    for watch, job in rows:
        watch.pending_job_id = None
        watch.last_job_id = job.id
        watch.checked_at = job.finished_at or now
        if job.status == JobStatus.SUCCEEDED:
            watch.last_problem = None
        elif job.status == JobStatus.CANCELLED:
            watch.last_problem = "check_cancelled"
        elif job.error == jobs.ERROR_ENTITY_GONE:
            watch.last_problem = "entity_gone"
        else:
            watch.last_problem = "check_failed"
        news = news_of(job, watch.domain)
        if watch.baseline_at is None:
            # The first check: what exists now is the baseline, not news.
            if job.status == JobStatus.SUCCEEDED:
                watch.baseline_at = watch.checked_at
            continue
        if not news.empty:
            await record_alert(db, watch, job, news)
            alerts += 1
    await db.commit()
    return alerts


@dataclass(frozen=True)
class Queued:
    """A job the sweep created, to hand to the worker once committed."""

    job_id: uuid.UUID
    timeout_seconds: int


async def queue_due(
    db: AsyncSession,
    now: datetime,
    *,
    registry: ShuttleRegistry,
    limit: int,
    sweep_limit: int = SWEEP_LIMIT,
) -> list[Queued]:
    """Create the checks that are due (commits each). ``limit`` is the per-user job limit."""
    due = list(
        (
            await db.execute(
                select(CtWatch.id)
                .where(CtWatch.next_check_at <= now, CtWatch.pending_job_id.is_(None))
                .order_by(CtWatch.next_check_at)
                .limit(sweep_limit)
            )
        ).scalars()
    )
    queued: list[Queued] = []
    for watch_id in due:
        item = await _queue_one(db, watch_id, now, registry=registry, limit=limit)
        if item is not None:
            queued.append(item)
    return queued


async def _queue_one(
    db: AsyncSession,
    watch_id: uuid.UUID,
    now: datetime,
    *,
    registry: ShuttleRegistry,
    limit: int,
) -> Queued | None:
    watch = (
        await db.execute(
            select(CtWatch)
            .where(
                CtWatch.id == watch_id,
                CtWatch.next_check_at <= now,
                CtWatch.pending_job_id.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if watch is None:
        await db.rollback()
        return None
    # Whatever happens, the next attempt is an hour away unless said otherwise.
    watch.next_check_at = now + CHECK_EVERY
    case = await jobs.lock_case_for_job(db, watch.case_id)
    shuttle = registry.get(SHUTTLE)
    user = await db.get(User, watch.created_by) if watch.created_by is not None else None
    if case is None:
        await db.rollback()
        return None
    if not jobs.case_is_open(case):
        watch.last_problem = "case_closed"
    elif user is None or not await jobs.requester_may_run(db, case.id, user.id):
        watch.last_problem = "owner_cannot_run"
    elif shuttle is None:
        watch.last_problem = "shuttle_missing"
    else:
        try:
            job = await jobs.create_job(
                db,
                case=case,
                meta=shuttle.meta,
                input_key=domain_key(watch.domain),
                user=user,
                actor=SYSTEM_ACTOR,
                limit=limit,
                use_cache=False,
                commit=False,
            )
        except jobs.TooManyJobsError:
            watch.last_problem = "job_limit"
            watch.next_check_at = now + RETRY_SOON
        else:
            watch.pending_job_id = job.id
            await db.commit()
            return Queued(job.id, shuttle.meta.timeout_seconds)
    await db.commit()
    return None


async def sweep(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    now: datetime,
    *,
    registry: ShuttleRegistry,
    limit: int,
) -> tuple[int, int]:
    """Collect finished checks, then queue the due ones. Returns (alerts, checks queued)."""
    async with sessionmaker() as db:
        alerts = await collect(db, now)
    async with sessionmaker() as db:
        queued = await queue_due(db, now, registry=registry, limit=limit)
    for item in queued:
        try:
            await queue.enqueue(item.job_id, timeout_seconds=item.timeout_seconds)
        except Exception as error:
            # The job stays queued; the reaper fails it later, and the next hour tries again.
            logger.error("Could not queue CT check %s: %s", item.job_id, type(error).__name__)
    return alerts, len(queued)


__all__ = [
    "CHECK_EVERY",
    "MAX_ALERT_NAMES",
    "MAX_PER_CASE",
    "PROBLEMS",
    "SYSTEM_ACTOR",
    "MonitoringError",
    "News",
    "acknowledge",
    "clean_domain",
    "collect",
    "count_unseen",
    "create_watch",
    "delete_watch",
    "list_alerts",
    "list_watches",
    "news_of",
    "queue_due",
    "request_check",
    "sweep",
]
