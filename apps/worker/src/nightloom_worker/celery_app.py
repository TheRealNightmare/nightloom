"""The worker's Celery app and its one task, ``run_job``.

Start it with::

    celery -A nightloom_worker.celery_app worker --queues shuttles --concurrency 4

Configuration comes from the same ``NIGHTLOOM_*`` settings as the API and
the same :func:`~nightloom_api.jobs.queue.create_celery`: Valkey as broker,
JSON only (no pickle), late acknowledgement, one task per worker process at
a time. Each task runs the job in a fresh event loop (:func:`run_bounded`)
with its own database and Neo4j clients, closed when it ends.

Every job reaches a final status (see :mod:`nightloom_api.jobs.deadlines`):

- the runner ends the run at its deadline and records the outcome;
- at Celery's **soft limit** the run is cancelled and the job recorded as
  failed (``worker_time_limit``);
- at the **hard limit** Celery kills the pool process; the worker's *main*
  process gets ``task_failure`` and records the job as failed. A pool
  process that dies otherwise (``WorkerLostError``) has its task re-delivered,
  and the next run of it finds the job ``running`` and fails it;
- the **reaper** runs in each worker's main process when it is ready and
  every :data:`REAP_INTERVAL_SECONDS` after (one worker at a time, under a
  Valkey lock), and fails jobs whose worker died with them;
- a running job is noted in Valkey under the worker's node name
  (:data:`RUNNING_KEY`); a worker that starts again under the same name
  (``docker compose kill`` / ``start``) fails the jobs its previous run left,
  at once, before taking new ones.

After each job of a **scan run** (and from the reaper's thread every
interval), the run's next planned jobs are queued while the requester has
free job slots (see :mod:`nightloom_api.scans.service`).

The reaper's thread also runs the **ownership sweep** every interval
(verifications past their 30 days expire; verified domains not checked for
a day, and owners' checks that never reported, are queued for checking by
the ``check_ownership`` task; see :mod:`nightloom_worker.ownership`).

The reaper's thread also purges the **result cache** every
:data:`PURGE_INTERVAL_SECONDS` (expired entries, then least recently used
ones beyond ``NIGHTLOOM_RESULT_CACHE_MAX_BYTES``), under its own lock.
"""

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from billiard.exceptions import WorkerLostError
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from celery.signals import setup_logging, task_failure, worker_init, worker_ready, worker_shutdown
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from redis.exceptions import LockError, RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.auth import clock
from nightloom_api.config import Settings, get_settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra import db as db_infra
from nightloom_api.infra import graph as graph_infra
from nightloom_api.jobs import deadlines, result_cache
from nightloom_api.jobs.models import Job, JobStatus
from nightloom_api.jobs.queue import (
    CHECK_TASK_NAME,
    TASK_NAME,
    CeleryJobQueue,
    create_celery,
)
from nightloom_api.logging import configure_logging
from nightloom_api.ownership import service as ownership
from nightloom_api.scans import service as scans
from nightloom_sdk.meta import MAX_TIMEOUT_SECONDS
from nightloom_sdk.registry import RegistryError, default_registry
from nightloom_worker.loop import run_bounded
from nightloom_worker.ownership import check_verification, default_prover
from nightloom_worker.runner import (
    ERROR_TIME_LIMIT,
    ERROR_WORKER_LOST,
    Outcome,
    RunnerConfig,
    bounded,
    finish_abandoned,
    reap_stale_jobs,
    run_job,
)

logger = logging.getLogger(__name__)

# Seconds for closing a run's database engine and Neo4j driver.
CLOSE_SECONDS = 5.0
# Recording a job from the main process (time limit, lost worker): each SQL
# statement and lock wait, and the whole recording, are bounded.
RECORD_STATEMENT_SECONDS = 5.0
RECORD_SECONDS = 20.0
# The reaper: how often, and the Valkey lock that lets one worker reap at a time.
REAP_INTERVAL_SECONDS = 60.0
REAPER_LOCK = "nightloom:worker:reaper"
REAPER_LOCK_SECONDS = 50
# Purging the result cache (expired entries, then the size cap): how often,
# from the reaper's thread, and the lock that lets one worker purge at a time.
PURGE_INTERVAL_SECONDS = 600.0
PURGE_LOCK = "nightloom:worker:result-cache-purge"
PURGE_LOCK_SECONDS = 50
# The ownership sweep, from the reaper's thread every interval, one worker at a time.
OWNERSHIP_LOCK = "nightloom:worker:ownership-sweep"
OWNERSHIP_LOCK_SECONDS = 50
# Releasing a scan run's next jobs after a job, and from the reaper's thread.
RELEASE_SECONDS = 20.0
# Which worker runs a job: the node name, until the job ends or the longest
# possible run is over.
RUNNING_KEY = "nightloom:worker:running:{job_id}"
RUNNING_KEY_SECONDS = (
    deadlines.time_limits(MAX_TIMEOUT_SECONDS)[1] + deadlines.REAPER_MARGIN_SECONDS
)
VALKEY_SECONDS = 5.0
# This run of the worker: set at import in the main process and inherited by
# the pool processes it forks, so a restarted worker under the same node name
# can tell its previous run's jobs from its own.
BOOT_ID = uuid.uuid4().hex

TIME_LIMIT_OUTCOME = Outcome(JobStatus.FAILED, error=ERROR_TIME_LIMIT, reason="worker_time_limit")
WORKER_LOST_OUTCOME = Outcome(JobStatus.FAILED, error=ERROR_WORKER_LOST, reason="worker_lost")

app = create_celery(get_settings(), name="nightloom-worker")


@setup_logging.connect
def _configure_logging(**_: Any) -> None:
    """Use Nightloom's log format instead of Celery's (and never print secrets)."""
    configure_logging(get_settings().log_level)


@worker_init.connect
def _check_shuttles(**_: Any) -> None:
    """Refuse to start when a Shuttle cannot load or is misconfigured.

    For example a private resolver in NIGHTLOOM_DNS_RESOLVERS: better one
    clear error at startup than every job failing. Celery logs and ignores
    ordinary exceptions from signal handlers, so this exits instead.
    """
    try:
        registry = default_registry()
        registry.check_configuration()
    except RegistryError as error:
        logger.critical("Refusing to start: %s", error)
        raise SystemExit(1) from error
    logger.info("%d Shuttle(s) installed; configuration OK.", len(registry))


# --- running a job (pool process) -----------------------------------------------------------


async def release_after(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], job_id: uuid.UUID
) -> int:
    """Queue the next planned jobs of the job's scan run, if it belongs to one. Returns how many."""
    async with sessionmaker() as db:
        row = (
            await db.execute(select(Job.run_id, Job.requested_by).where(Job.id == job_id))
        ).first()
    if row is None or row.run_id is None or row.requested_by is None:
        return 0
    queue = CeleryJobQueue(settings)
    try:
        return await scans.release_and_enqueue(
            sessionmaker,
            queue,
            user_id=row.requested_by,
            limit=settings.max_active_jobs_per_user,
            registry=default_registry(),
        )
    finally:
        queue.close()


async def _with_config(settings: Settings, job_id: uuid.UUID, *, abandoned: bool) -> str | None:
    engine = db_infra.create_engine(settings)
    driver = graph_infra.create_driver(settings)
    sessionmaker = db_infra.create_sessionmaker(engine)
    try:
        if abandoned:
            await finish_abandoned(sessionmaker, job_id, TIME_LIMIT_OUTCOME)
            await bounded(
                release_after(settings, sessionmaker, job_id),
                RELEASE_SECONDS,
                f"Job {job_id}: releasing its scan run's next jobs",
            )
            return JobStatus.FAILED.value
        config = RunnerConfig(
            sessionmaker=sessionmaker,
            graph=GraphRepository(driver),
            registry=default_registry(),
            cache_scope=result_cache.CacheScope(settings.result_cache_scope),
            cache_max_entry_bytes=settings.result_cache_max_entry_bytes,
            prover=default_prover(),
        )
        status = await run_job(job_id, config)
        # A finished job frees a job slot: its scan run (if any) may queue the next.
        await bounded(
            release_after(settings, sessionmaker, job_id),
            RELEASE_SECONDS,
            f"Job {job_id}: releasing its scan run's next jobs",
        )
        return status.value if status is not None else None
    finally:
        await bounded(driver.close(), CLOSE_SECONDS, "Closing the Neo4j driver")
        await bounded(engine.dispose(), CLOSE_SECONDS, "Closing the database engine")


def _valkey(settings: Settings) -> SyncRedis:
    return SyncRedis.from_url(
        settings.valkey_url.get_secret_value(),
        socket_connect_timeout=VALKEY_SECONDS,
        socket_timeout=VALKEY_SECONDS,
    )


def _note_running(settings: Settings, job_id: uuid.UUID, hostname: str | None) -> bool:
    """Note that this worker runs the job; best effort (the reaper still covers it)."""
    if not hostname:
        return False
    try:
        with _valkey(settings) as client:
            client.set(
                RUNNING_KEY.format(job_id=job_id), f"{hostname} {BOOT_ID}", ex=RUNNING_KEY_SECONDS
            )
    except (RedisError, OSError) as error:
        logger.warning("Job %s: could not note its worker: %s.", job_id, type(error).__name__)
        return False
    return True


def _forget_running(settings: Settings, job_id: uuid.UUID) -> None:
    try:
        with _valkey(settings) as client:
            client.delete(RUNNING_KEY.format(job_id=job_id))
    except (RedisError, OSError) as error:
        logger.warning("Job %s: could not clear its worker: %s.", job_id, type(error).__name__)


@app.task(name=TASK_NAME, acks_late=True)
def run_job_task(job_id: str) -> str | None:
    """Run one job. The argument is the job's ID; everything else is read from PostgreSQL."""
    try:
        parsed = uuid.UUID(job_id)
    except (TypeError, ValueError):
        logger.error("Ignoring a task with an invalid job ID.")
        return None
    settings = get_settings()
    noted = _note_running(settings, parsed, run_job_task.request.hostname)
    try:
        try:
            return run_bounded(
                _with_config(settings, parsed, abandoned=False), catch_soft_limit=True
            )
        except SoftTimeLimitExceeded:
            logger.error("Job %s hit the worker's soft time limit; recording it as failed.", parsed)
            return run_bounded(_with_config(settings, parsed, abandoned=True))
    finally:
        if noted:
            _forget_running(settings, parsed)


async def _check_ownership(settings: Settings, verification_id: uuid.UUID) -> str | None:
    engine = db_infra.create_engine(settings)
    try:
        return await check_verification(
            db_infra.create_sessionmaker(engine), verification_id, default_prover()
        )
    finally:
        await bounded(engine.dispose(), CLOSE_SECONDS, "Closing the database engine")


@app.task(name=CHECK_TASK_NAME, acks_late=True)
def check_ownership_task(verification_id: str) -> str | None:
    """Look for one ownership verification's proof. The argument is its ID."""
    try:
        parsed = uuid.UUID(verification_id)
    except (TypeError, ValueError):
        logger.error("Ignoring a check with an invalid verification ID.")
        return None
    return run_bounded(_check_ownership(get_settings(), parsed))


# --- the main process: time limits and lost workers -----------------------------------------


def _in_thread(name: str, func: Callable[[], Any], seconds: float) -> bool:
    """Run ``func`` in a daemon thread for at most ``seconds``; False if it did not finish."""
    thread = threading.Thread(target=func, name=name, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        logger.error("%s did not finish within %.0f s; left behind.", name, seconds)
        return False
    return True


async def _record(settings: Settings, job_id: uuid.UUID, outcome: Outcome) -> bool:
    engine = db_infra.create_engine(settings, statement_timeout_seconds=RECORD_STATEMENT_SECONDS)
    try:
        return await finish_abandoned(db_infra.create_sessionmaker(engine), job_id, outcome)
    finally:
        await bounded(engine.dispose(), CLOSE_SECONDS, "Closing the database engine")


def record_abandoned(job_id: uuid.UUID, outcome: Outcome) -> None:
    """Fail a running job from the main process; bounded, never raises."""
    settings = get_settings()

    def record() -> None:
        try:
            if run_bounded(_record(settings, job_id, outcome)):
                logger.error("Job %s recorded as failed (%s).", job_id, outcome.reason)
        except Exception as error:  # never let a signal handler fail
            logger.error("Job %s could not be recorded: %s.", job_id, type(error).__name__)

    _in_thread(f"Recording job {job_id}", record, RECORD_SECONDS)


@task_failure.connect
def _on_task_failure(
    sender: Any = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    args: Any = None,
    **_: Any,
) -> None:
    """A pool process was killed at the hard limit (or lost) while running a job: record it.

    Celery sends ``task_failure`` from the main process in those cases, where
    the pool process had no chance to record anything itself.
    """
    if getattr(sender, "name", None) != TASK_NAME:
        return
    if isinstance(exception, TimeLimitExceeded):
        outcome = TIME_LIMIT_OUTCOME
    elif isinstance(exception, WorkerLostError):
        outcome = WORKER_LOST_OUTCOME
    else:
        return  # the pool process ran the task and recorded the job itself
    raw = args[0] if args else task_id
    try:
        job_id = uuid.UUID(str(raw))
    except ValueError:
        logger.error("A task with an invalid job ID failed: %s.", type(exception).__name__)
        return
    logger.error("Job %s: the worker process was stopped (%s).", job_id, type(exception).__name__)
    record_abandoned(job_id, outcome)


# --- the main process: the reaper -------------------------------------------------------------


async def reap_once(settings: Settings) -> int | None:
    """Fail stale jobs unless another worker holds the reaper lock. None if it did."""
    client = Redis.from_url(
        settings.valkey_url.get_secret_value(),
        socket_connect_timeout=RECORD_STATEMENT_SECONDS,
        socket_timeout=RECORD_STATEMENT_SECONDS,
    )
    engine = db_infra.create_engine(settings, statement_timeout_seconds=RECORD_STATEMENT_SECONDS)
    try:
        lock = client.lock(REAPER_LOCK, timeout=REAPER_LOCK_SECONDS, blocking=False)
        if not await lock.acquire():
            return None
        try:
            return await reap_stale_jobs(db_infra.create_sessionmaker(engine), default_registry())
        finally:
            try:
                await lock.release()
            except LockError:  # expired meanwhile: another worker may hold it now
                logger.warning("The reaper lock expired before the reaper finished.")
    finally:
        await bounded(engine.dispose(), CLOSE_SECONDS, "Closing the database engine")
        await bounded(client.aclose(), CLOSE_SECONDS, "Closing the Valkey client")


async def purge_cache_once(settings: Settings) -> result_cache.PurgeResult | None:
    """Purge the result cache unless another worker holds the purge lock. None if it did."""
    client = Redis.from_url(
        settings.valkey_url.get_secret_value(),
        socket_connect_timeout=RECORD_STATEMENT_SECONDS,
        socket_timeout=RECORD_STATEMENT_SECONDS,
    )
    engine = db_infra.create_engine(settings, statement_timeout_seconds=PURGE_LOCK_SECONDS / 2)
    try:
        lock = client.lock(PURGE_LOCK, timeout=PURGE_LOCK_SECONDS, blocking=False)
        if not await lock.acquire():
            return None
        try:
            async with db_infra.create_sessionmaker(engine)() as db, db.begin():
                return await result_cache.purge(
                    db, now=clock.utcnow(), max_total_bytes=settings.result_cache_max_bytes
                )
        finally:
            try:
                await lock.release()
            except LockError:  # expired meanwhile: another worker may hold it now
                logger.warning("The result cache purge lock expired before the purge finished.")
    finally:
        await bounded(engine.dispose(), CLOSE_SECONDS, "Closing the database engine")
        await bounded(client.aclose(), CLOSE_SECONDS, "Closing the Valkey client")


async def release_all_runs(settings: Settings) -> int:
    """Queue what every running scan run may queue now; end runs nobody can release.

    The safety net for releases missed after a job (a worker that died, a
    queue that was down). Returns how many jobs were released.
    """
    engine = db_infra.create_engine(settings, statement_timeout_seconds=RECORD_STATEMENT_SECONDS)
    queue = CeleryJobQueue(settings)
    released = 0
    try:
        sessionmaker = db_infra.create_sessionmaker(engine)
        async with sessionmaker() as db, db.begin():
            await scans.stop_orphaned_runs(db)
        async with sessionmaker() as db:
            users = await scans.users_with_running_runs(db)
        for user_id in users:
            released += await scans.release_and_enqueue(
                sessionmaker,
                queue,
                user_id=user_id,
                limit=settings.max_active_jobs_per_user,
                registry=default_registry(),
            )
    finally:
        queue.close()
        await bounded(engine.dispose(), CLOSE_SECONDS, "Closing the database engine")
    return released


async def sweep_ownership(settings: Settings) -> int | None:
    """Expire verifications and queue due checks, unless another worker sweeps. Returns how many."""
    client = Redis.from_url(
        settings.valkey_url.get_secret_value(),
        socket_connect_timeout=RECORD_STATEMENT_SECONDS,
        socket_timeout=RECORD_STATEMENT_SECONDS,
    )
    engine = db_infra.create_engine(settings, statement_timeout_seconds=RECORD_STATEMENT_SECONDS)
    queue = CeleryJobQueue(settings)
    try:
        lock = client.lock(OWNERSHIP_LOCK, timeout=OWNERSHIP_LOCK_SECONDS, blocking=False)
        if not await lock.acquire():
            return None
        try:
            async with db_infra.create_sessionmaker(engine)() as db:
                due = await ownership.sweep(db, clock.utcnow())
            for verification_id in due:
                await queue.enqueue_check(verification_id)
            return len(due)
        finally:
            try:
                await lock.release()
            except LockError:  # expired meanwhile: another worker may hold it now
                logger.warning("The ownership sweep lock expired before the sweep finished.")
    finally:
        queue.close()
        await bounded(engine.dispose(), CLOSE_SECONDS, "Closing the database engine")
        await bounded(client.aclose(), CLOSE_SECONDS, "Closing the Valkey client")


async def reap_own(settings: Settings, hostname: str) -> int:
    """Fail the jobs this worker's previous run (same node name) left running. Returns how many.

    Nothing of a previous run can still be running once the worker has
    started again; jobs noted by this run (same :data:`BOOT_ID`) are left alone.
    """
    client = Redis.from_url(
        settings.valkey_url.get_secret_value(),
        socket_connect_timeout=VALKEY_SECONDS,
        socket_timeout=VALKEY_SECONDS,
    )
    engine = db_infra.create_engine(settings, statement_timeout_seconds=RECORD_STATEMENT_SECONDS)
    reaped = 0
    try:
        sessionmaker = db_infra.create_sessionmaker(engine)
        prefix = RUNNING_KEY.format(job_id="")
        async for raw in client.scan_iter(match=f"{prefix}*", count=100):
            key = raw.decode() if isinstance(raw, bytes) else str(raw)
            value = await client.get(key)
            text = value.decode() if isinstance(value, bytes) else str(value or "")
            owner, _, boot = text.partition(" ")
            if owner != hostname or boot == BOOT_ID:
                continue  # another worker's, or this run's own
            try:
                job_id = uuid.UUID(key.removeprefix(prefix))
            except ValueError:
                continue
            if await finish_abandoned(sessionmaker, job_id, WORKER_LOST_OUTCOME):
                logger.warning("Job %s: its worker stopped while running it; failed.", job_id)
                reaped += 1
            await client.delete(key)
    finally:
        await bounded(engine.dispose(), CLOSE_SECONDS, "Closing the database engine")
        await bounded(client.aclose(), CLOSE_SECONDS, "Closing the Valkey client")
    return reaped


class Reaper(threading.Thread):
    """Calls :func:`reap_once` now and every ``interval`` seconds until stopped.

    With a ``hostname``, it first fails what this worker's previous run left
    (:func:`reap_own`). Every ``purge_interval`` seconds (and once at start)
    it also purges the result cache (:func:`purge_cache_once`).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        hostname: str | None = None,
        interval: float = REAP_INTERVAL_SECONDS,
        reap: Callable[[Settings], Coroutine[Any, Any, int | None]] = reap_once,
        reap_own: Callable[[Settings, str], Coroutine[Any, Any, int]] = reap_own,
        purge_interval: float = PURGE_INTERVAL_SECONDS,
        purge: Callable[
            [Settings], Coroutine[Any, Any, result_cache.PurgeResult | None]
        ] = purge_cache_once,
        monotonic: Callable[[], float] = time.monotonic,
        release: Callable[[Settings], Coroutine[Any, Any, int]] = release_all_runs,
        sweep: Callable[[Settings], Coroutine[Any, Any, int | None]] = sweep_ownership,
    ) -> None:
        super().__init__(name="nightloom-reaper", daemon=True)
        self.settings = settings
        self.hostname = hostname
        self.interval = interval
        self._reap = reap
        self._reap_own = reap_own
        self.purge_interval = purge_interval
        self._purge = purge
        self._monotonic = monotonic
        self._last_purge: float | None = None
        self._release = release
        self._sweep = sweep
        self.stopped = threading.Event()

    def reap_previous_run(self) -> int | None:
        if not self.hostname:
            return None
        try:
            return run_bounded(
                asyncio.wait_for(self._reap_own(self.settings, self.hostname), RECORD_SECONDS)
            )
        except Exception as error:  # the reaper keeps going
            logger.warning("Reaping this worker's previous jobs failed: %s.", type(error).__name__)
            return None

    def reap(self) -> int | None:
        try:
            reaped = run_bounded(asyncio.wait_for(self._reap(self.settings), REAPER_LOCK_SECONDS))
        except Exception as error:  # the reaper keeps going
            logger.warning("Reaping stale jobs failed: %s.", type(error).__name__)
            return None
        if reaped:
            logger.warning("Failed %d abandoned job(s).", reaped)
        return reaped

    def purge(self) -> result_cache.PurgeResult | None:
        """Purge the result cache if ``purge_interval`` has passed since the last time."""
        now = self._monotonic()
        if self._last_purge is not None and now - self._last_purge < self.purge_interval:
            return None
        self._last_purge = now
        try:
            purged = run_bounded(asyncio.wait_for(self._purge(self.settings), PURGE_LOCK_SECONDS))
        except Exception as error:  # the reaper keeps going
            logger.warning("Purging the result cache failed: %s.", type(error).__name__)
            return None
        if purged is not None and (purged.expired or purged.evicted):
            logger.info(
                "Result cache: removed %d expired and %d least recently used entries.",
                purged.expired,
                purged.evicted,
            )
        return purged

    def release(self) -> int | None:
        """Queue what running scan runs may queue now (after the reaper freed job slots)."""
        try:
            released = run_bounded(asyncio.wait_for(self._release(self.settings), RELEASE_SECONDS))
        except Exception as error:  # the reaper keeps going
            logger.warning("Releasing scan run jobs failed: %s.", type(error).__name__)
            return None
        if released:
            logger.info("Released %d scan run job(s).", released)
        return released

    def sweep(self) -> int | None:
        """Expire ownership verifications and queue the checks that are due."""
        try:
            queued = run_bounded(
                asyncio.wait_for(self._sweep(self.settings), OWNERSHIP_LOCK_SECONDS)
            )
        except Exception as error:  # the reaper keeps going
            logger.warning("The ownership sweep failed: %s.", type(error).__name__)
            return None
        if queued:
            logger.info("Queued %d ownership check(s).", queued)
        return queued

    def run(self) -> None:
        self.reap_previous_run()
        while not self.stopped.is_set():
            self.reap()
            self.release()
            self.sweep()
            self.purge()
            self.stopped.wait(self.interval)

    def stop(self) -> None:
        self.stopped.set()


_reaper: Reaper | None = None


@worker_ready.connect
def _start_reaper(sender: Any = None, **_: Any) -> None:
    global _reaper  # one reaper per worker process
    if _reaper is None:
        _reaper = Reaper(get_settings(), hostname=getattr(sender, "hostname", None))
        _reaper.start()
        logger.info("Reaper started (every %.0f s).", REAP_INTERVAL_SECONDS)


@worker_shutdown.connect
def _stop_reaper(**_: Any) -> None:
    global _reaper
    if _reaper is not None:
        _reaper.stop()
        _reaper = None
