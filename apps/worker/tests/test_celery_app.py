"""The Celery task wrapper; one test runs the real task against the test databases."""

import asyncio
import logging
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from billiard.exceptions import WorkerLostError
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from celery.signals import task_failure
from pydantic import SecretStr
from redis.asyncio import Redis

from nightloom_api.config import Settings, get_settings
from nightloom_api.jobs import result_cache
from nightloom_api.jobs.queue import TASK_NAME
from nightloom_worker import celery_app
from support import TEST_DATABASE_URL, TEST_VALKEY_URL, graph_test_settings, run_in_thread


def test_task_is_registered_under_the_shared_name() -> None:
    assert TASK_NAME in celery_app.app.tasks
    assert celery_app.app.conf.accept_content == ["json"]
    assert celery_app.app.conf.task_acks_late is True


def test_invalid_job_ids_are_ignored() -> None:
    assert celery_app.run_job_task.run("not-a-uuid") is None


def test_task_runs_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[uuid.UUID, bool]] = []

    async def fake(settings: Settings, job_id: uuid.UUID, *, abandoned: bool) -> str | None:
        calls.append((job_id, abandoned))
        return "succeeded"

    monkeypatch.setattr(celery_app, "_with_config", fake)
    job_id = uuid.uuid4()
    assert celery_app.run_job_task.run(str(job_id)) == "succeeded"
    assert calls == [(job_id, False)]


def test_soft_time_limit_fails_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake(settings: Settings, job_id: uuid.UUID, *, abandoned: bool) -> str | None:
        calls.append(abandoned)
        if not abandoned:
            raise SoftTimeLimitExceeded()
        return "failed"

    monkeypatch.setattr(celery_app, "_with_config", fake)
    assert celery_app.run_job_task.run(str(uuid.uuid4())) == "failed"
    assert calls == [False, True]


def test_logging_is_configured_by_nightloom(monkeypatch: pytest.MonkeyPatch) -> None:
    levels: list[str] = []
    monkeypatch.setattr(celery_app, "configure_logging", levels.append)
    celery_app._configure_logging()
    assert levels == [get_settings().log_level]


@pytest.mark.db
@pytest.mark.graph
def test_real_task_against_the_test_databases(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    """Runs the actual task code path: its own engine, driver, registry and event loop."""
    graph = graph_test_settings()
    monkeypatch.setenv("NIGHTLOOM_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("NIGHTLOOM_NEO4J_URI", graph.neo4j_uri)
    monkeypatch.setenv("NIGHTLOOM_NEO4J_USER", graph.neo4j_user)
    monkeypatch.setenv("NIGHTLOOM_NEO4J_PASSWORD", graph.neo4j_password.get_secret_value())
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.database_url == SecretStr(TEST_DATABASE_URL)

    async def missing_job() -> None:
        # A job that does not exist (e.g. its case was deleted): nothing to do.
        assert await celery_app._with_config(settings, uuid.uuid4(), abandoned=False) is None

    run_in_thread(missing_job)
    assert celery_app.run_job_task.run(str(uuid.uuid4())) is None
    # The abandoned path is a no-op for a job that does not exist.
    assert asyncio.run(celery_app._with_config(settings, uuid.uuid4(), abandoned=True)) == "failed"
    get_settings.cache_clear()


def test_startup_checks_every_shuttle_configuration(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("NIGHTLOOM_DNS_RESOLVERS", raising=False)
    celery_app._check_shuttles()  # the defaults are fine

    monkeypatch.setenv("NIGHTLOOM_DNS_RESOLVERS", "192.168.1.1")
    with pytest.raises(SystemExit) as stopped:
        celery_app._check_shuttles()
    assert stopped.value.code == 1
    assert "Refusing to start: Shuttle 'dns' is misconfigured" in caplog.text
    assert "not a public address" in caplog.text

    monkeypatch.setenv("NIGHTLOOM_DNS_RESOLVERS", "172.30.0.53")
    monkeypatch.setenv("NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS", "true")
    celery_app._check_shuttles()  # the test-only mode allows it...
    monkeypatch.setenv("NIGHTLOOM_ENV", "production")
    with pytest.raises(SystemExit):  # ...but never in production
        celery_app._check_shuttles()
    assert "test-only setting" in caplog.text


# --- the main process: time limits and lost workers -----------------------------------------


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, str | None]]:
    calls: list[tuple[uuid.UUID, str | None]] = []
    monkeypatch.setattr(
        celery_app,
        "record_abandoned",
        lambda job_id, outcome: calls.append((job_id, outcome.reason)),
    )
    return calls


def _fail(exception: BaseException, *, sender: Any = None, **extra: Any) -> None:
    task_failure.send(
        sender=sender or celery_app.run_job_task,
        exception=exception,
        traceback=None,
        einfo=None,
        kwargs={},
        **extra,
    )


def test_hard_limit_and_lost_workers_are_recorded(
    recorded: list[tuple[uuid.UUID, str | None]],
) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    _fail(TimeLimitExceeded(390), task_id=str(first), args=[str(first)])
    _fail(WorkerLostError("lost"), task_id=str(second), args=None)  # from the task ID
    assert recorded == [(first, "worker_time_limit"), (second, "worker_lost")]


def test_other_failures_and_tasks_are_left_alone(
    recorded: list[tuple[uuid.UUID, str | None]], caplog: pytest.LogCaptureFixture
) -> None:
    job_id = str(uuid.uuid4())
    _fail(ValueError("boom"), task_id=job_id, args=[job_id])  # recorded by the run itself
    _fail(SoftTimeLimitExceeded(), task_id=job_id, args=[job_id])  # likewise
    other = celery_app.app.tasks["celery.chord_unlock"]  # any other task
    _fail(TimeLimitExceeded(1), sender=other, task_id=job_id, args=[job_id])
    _fail(TimeLimitExceeded(1), task_id="not-a-uuid", args=["not-a-uuid"])
    assert recorded == []
    assert "A task with an invalid job ID failed: TimeLimitExceeded." in caplog.text


def test_recording_never_raises_or_hangs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fails(*_: Any) -> bool:
        raise OSError("connection refused, password=hunter2")

    monkeypatch.setattr(celery_app, "_record", fails)
    celery_app.record_abandoned(uuid.uuid4(), celery_app.TIME_LIMIT_OUTCOME)
    assert "could not be recorded: OSError." in caplog.text
    assert "hunter2" not in caplog.text

    async def hangs(*_: Any) -> bool:
        await asyncio.sleep(3600)
        return True  # pragma: no cover

    monkeypatch.setattr(celery_app, "_record", hangs)
    monkeypatch.setattr(celery_app, "RECORD_SECONDS", 0.2)
    began = time.monotonic()
    celery_app.record_abandoned(uuid.uuid4(), celery_app.TIME_LIMIT_OUTCOME)
    assert time.monotonic() - began < 2
    assert "did not finish within 0 s; left behind." in caplog.text


# --- the main process: the reaper -------------------------------------------------------------


class _FakeReap:
    def __init__(self, *results: int | Exception | None) -> None:
        self.results = list(results)
        self.calls = 0
        self.called = threading.Event()

    async def __call__(self, settings: Settings) -> int | None:
        self.calls += 1
        self.called.set()
        result = self.results.pop(0) if self.results else 0
        if isinstance(result, Exception):
            raise result
        return result


async def _no_purge(settings: Settings) -> None:
    return None


async def _no_release(settings: Settings) -> int:
    return 0


async def _no_sweep(settings: Settings) -> int | None:
    return 0


async def _no_monitor(settings: Settings) -> tuple[int, int] | None:
    return (0, 0)


def test_reaper_runs_now_and_then_every_interval(caplog: pytest.LogCaptureFixture) -> None:
    fake = _FakeReap(3, OSError("down"), None)
    reaper = celery_app.Reaper(
        Settings(env="test"),
        interval=0.05,
        reap=fake,
        purge=_no_purge,
        release=_no_release,
        sweep=_no_sweep,
        monitor=_no_monitor,
    )
    reaper.start()
    try:
        deadline = time.monotonic() + 5
        while fake.calls < 4 and time.monotonic() < deadline:
            fake.called.wait(0.1)
    finally:
        reaper.stop()
        reaper.join(5)
    assert fake.calls >= 4
    assert not reaper.is_alive()
    assert "Failed 3 abandoned job(s)." in caplog.text
    assert "Reaping stale jobs failed: OSError." in caplog.text


def test_reaper_starts_when_the_worker_is_ready_and_stops_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[Any] = []

    class FakeReaper:
        def __init__(self, settings: Settings, *, hostname: str | None) -> None:
            self.stopped = False
            self.hostname = hostname

        def start(self) -> None:
            started.append(self)

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(celery_app, "Reaper", FakeReaper)
    monkeypatch.setattr(celery_app, "_reaper", None)
    consumer = SimpleNamespace(hostname="worker@abc")
    celery_app._start_reaper(sender=consumer)
    celery_app._start_reaper(sender=consumer)  # once per process
    assert len(started) == 1
    assert started[0].hostname == "worker@abc"
    celery_app._stop_reaper()
    celery_app._stop_reaper()
    assert started[0].stopped is True


@pytest.mark.db
def test_only_one_worker_reaps_at_a_time(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    monkeypatch.setenv("NIGHTLOOM_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("NIGHTLOOM_VALKEY_URL", TEST_VALKEY_URL)
    get_settings.cache_clear()
    settings = get_settings()
    lock_name = f"{celery_app.REAPER_LOCK}:test:{uuid.uuid4()}"
    monkeypatch.setattr(celery_app, "REAPER_LOCK", lock_name)

    async def scenario() -> tuple[int | None, int | None, int | None]:
        other = Redis.from_url(TEST_VALKEY_URL)
        try:
            held = other.lock(lock_name, timeout=30, blocking=False)
            assert await held.acquire()
            while_held = await celery_app.reap_once(settings)  # another worker reaps
            await held.release()
            free = await celery_app.reap_once(settings)
            assert await other.get(lock_name) is None  # released after reaping
            # A lock that expired while reaping is not an error.
            await other.set(lock_name, "someone else", ex=30)
            stolen = await celery_app.reap_once(settings)
            return while_held, free, stolen
        finally:
            await other.delete(lock_name)
            await other.aclose()

    while_held, free, stolen = run_in_thread(scenario)
    get_settings.cache_clear()
    assert while_held is None
    assert isinstance(free, int)
    assert stolen is None


def test_reaper_purges_the_result_cache_every_purge_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=celery_app.__name__)
    now = [100.0]
    results: list[Any] = [
        result_cache.PurgeResult(expired=2, evicted=1),
        OSError("down"),
        result_cache.PurgeResult(expired=0, evicted=0),
    ]
    calls: list[Settings] = []

    async def purge(settings: Settings) -> result_cache.PurgeResult | None:
        calls.append(settings)
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[no-any-return]

    reaper = celery_app.Reaper(
        Settings(env="test"), purge=purge, purge_interval=600, monotonic=lambda: now[0]
    )
    assert reaper.purge() == result_cache.PurgeResult(expired=2, evicted=1)
    assert "removed 2 expired and 1 least recently used entries" in caplog.text
    now[0] += 599
    assert reaper.purge() is None  # not yet
    assert len(calls) == 1
    now[0] += 1
    assert reaper.purge() is None  # failed, logged by type only
    assert "Purging the result cache failed: OSError." in caplog.text
    now[0] += 600
    assert reaper.purge() == result_cache.PurgeResult(expired=0, evicted=0)
    assert len(calls) == 3


@pytest.mark.db
def test_only_one_worker_purges_the_cache_at_a_time(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    monkeypatch.setenv("NIGHTLOOM_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("NIGHTLOOM_VALKEY_URL", TEST_VALKEY_URL)
    get_settings.cache_clear()
    settings = get_settings()
    lock_name = f"{celery_app.PURGE_LOCK}:test:{uuid.uuid4()}"
    monkeypatch.setattr(celery_app, "PURGE_LOCK", lock_name)

    async def scenario() -> tuple[Any, Any, Any]:
        other = Redis.from_url(TEST_VALKEY_URL)
        try:
            held = other.lock(lock_name, timeout=30, blocking=False)
            assert await held.acquire()
            while_held = await celery_app.purge_cache_once(settings)
            await held.release()
            free = await celery_app.purge_cache_once(settings)
            assert await other.get(lock_name) is None
            await other.set(lock_name, "someone else", ex=30)
            stolen = await celery_app.purge_cache_once(settings)
            return while_held, free, stolen
        finally:
            await other.delete(lock_name)
            await other.aclose()

    while_held, free, stolen = run_in_thread(scenario)
    get_settings.cache_clear()
    assert while_held is None
    assert isinstance(free, result_cache.PurgeResult)
    assert stolen is None


def test_reaper_releases_scan_run_jobs_and_logs_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=celery_app.__name__)
    results: list[Any] = [3, OSError("down"), 0]

    async def release(settings: Settings) -> int:
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return int(result)

    reaper = celery_app.Reaper(Settings(env="test"), release=release)
    assert reaper.release() == 3
    assert "Released 3 scan run job(s)." in caplog.text
    assert reaper.release() is None
    assert "Releasing scan run jobs failed: OSError." in caplog.text
    assert reaper.release() == 0


def test_reaper_sweeps_ownership(caplog: pytest.LogCaptureFixture) -> None:
    results: list[int | Exception | None] = [2, OSError("down"), None]

    async def sweep(settings: Settings) -> int | None:
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    caplog.set_level(logging.INFO, logger="nightloom_worker.celery_app")
    reaper = celery_app.Reaper(Settings(env="test"), sweep=sweep)
    assert reaper.sweep() == 2
    assert "Queued 2 ownership check(s)." in caplog.text
    assert reaper.sweep() is None
    assert "The ownership sweep failed: OSError." in caplog.text
    assert reaper.sweep() is None  # another worker held the lock


def test_reaper_monitor_logs_and_survives_errors(caplog: pytest.LogCaptureFixture) -> None:
    results: list[tuple[int, int] | Exception | None] = [(1, 2), OSError("down"), None]

    async def monitor(settings: Settings) -> tuple[int, int] | None:
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    caplog.set_level(logging.INFO, logger="nightloom_worker.celery_app")
    reaper = celery_app.Reaper(Settings(env="test"), monitor=monitor)
    assert reaper.monitor() == (1, 2)
    assert "CT monitoring: 1 alert(s), 2 check(s) queued." in caplog.text
    assert reaper.monitor() is None
    assert "The CT monitoring sweep failed: OSError." in caplog.text
    assert reaper.monitor() is None  # another worker held the lock


class _SentQueue:
    def __init__(self, settings: Settings) -> None:
        self.sent: list[uuid.UUID] = []
        self.checks: list[uuid.UUID] = []
        _SentQueue.last = self

    last: "_SentQueue | None" = None

    async def enqueue(self, job_id: uuid.UUID, *, timeout_seconds: int) -> None:
        self.sent.append(job_id)

    async def enqueue_check(self, verification_id: uuid.UUID) -> None:
        self.checks.append(verification_id)

    def close(self) -> None:
        pass


@pytest.mark.db
def test_the_ownership_sweep_queues_due_checks(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    from sqlalchemy import text as sql

    from nightloom_api.infra import db as db_infra
    from nightloom_api.ownership.models import OwnershipVerification

    monkeypatch.setenv("NIGHTLOOM_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("NIGHTLOOM_VALKEY_URL", TEST_VALKEY_URL)
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(celery_app, "CeleryJobQueue", _SentQueue)

    async def scenario() -> tuple[int | None, list[uuid.UUID], uuid.UUID]:
        engine = db_infra.create_engine(settings)
        sessionmaker = db_infra.create_sessionmaker(engine)
        user_id, case_id = uuid.uuid4(), uuid.uuid4()
        try:
            async with sessionmaker() as db:
                await db.execute(
                    sql(
                        "INSERT INTO users (id, email, display_name, password_hash, role,"
                        " is_active) VALUES (:id, :email, 'Sweep tester', 'x', 'analyst', true)"
                    ),
                    {"id": user_id, "email": f"sweep-{user_id.hex[:8]}@example.org"},
                )
                await db.execute(
                    sql(
                        "INSERT INTO cases (id, title, purpose, legal_basis, ethics_ack_version,"
                        " ethics_ack_at, created_by) VALUES (:id, 'Sweep',"
                        " 'Check that verified domains are checked again every day.',"
                        " 'owned_assets', '1', now(), :user)"
                    ),
                    {"id": case_id, "user": user_id},
                )
                row = OwnershipVerification(
                    case_id=case_id,
                    domain=f"sweep-{case_id.hex[:8]}.example.org",
                    method="dns_txt",
                    token="t",
                    status="verified",
                    verified_at=sql("now() - interval '2 days'"),
                    expires_at=sql("now() + interval '28 days'"),
                    checked_at=sql("now() - interval '25 hours'"),
                )
                db.add(row)
                await db.commit()
                verification_id = row.id
            queued = await celery_app.sweep_ownership(settings)
            assert _SentQueue.last is not None
            return queued, _SentQueue.last.checks, verification_id
        finally:
            async with sessionmaker() as db:
                await db.execute(sql("DELETE FROM cases WHERE id = :id"), {"id": case_id})
                await db.execute(sql("DELETE FROM users WHERE id = :id"), {"id": user_id})
                await db.commit()
            await engine.dispose()

    try:
        queued, checks, verification_id = asyncio.run(scenario())
    finally:
        get_settings.cache_clear()
    assert queued is not None and queued >= 1
    assert verification_id in checks


@pytest.mark.db
def test_a_finished_run_job_releases_the_next_ones(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    """After a job of a scan run, the requester's next planned jobs are queued."""
    from sqlalchemy import text as sql

    from nightloom_api.infra import db as db_infra
    from nightloom_api.jobs.models import Job, JobStatus
    from nightloom_api.scans.models import ScanRun

    monkeypatch.setenv("NIGHTLOOM_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("NIGHTLOOM_VALKEY_URL", TEST_VALKEY_URL)
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(celery_app, "CeleryJobQueue", _SentQueue)

    async def scenario() -> tuple[int, int, int, list[str]]:
        engine = db_infra.create_engine(settings)
        sessionmaker = db_infra.create_sessionmaker(engine)
        user_id, case_id = uuid.uuid4(), uuid.uuid4()
        try:
            async with sessionmaker() as db:
                await db.execute(
                    sql(
                        "INSERT INTO users (id, email, display_name, password_hash, role,"
                        " is_active) VALUES (:id, :email, 'Run tester', 'x', 'analyst', true)"
                    ),
                    {"id": user_id, "email": f"runs-{user_id.hex[:8]}@example.org"},
                )
                await db.execute(
                    sql(
                        "INSERT INTO cases (id, title, purpose, legal_basis, ethics_ack_version,"
                        " ethics_ack_at, created_by) VALUES (:id, 'Runs',"
                        " 'Check that scan runs release their next planned jobs.',"
                        " 'owned_assets', '1', now(), :user)"
                    ),
                    {"id": case_id, "user": user_id},
                )
                await db.execute(
                    sql(
                        "INSERT INTO case_members (case_id, user_id, role) "
                        "VALUES (:case, :user, 'owner')"
                    ),
                    {"case": case_id, "user": user_id},
                )
                run = ScanRun(
                    case_id=case_id,
                    profile_id="quick-domain-recon",
                    profile_name="Quick domain recon",
                    requested_by=user_id,
                    plan=[
                        {"shuttle": "registrable-domain", "entity_key": f"domain:h{i}.example.org"}
                        for i in range(7)
                    ],
                    entities=7,
                )
                db.add(run)
                await db.commit()
                single = Job(
                    case_id=case_id,
                    shuttle_name="registrable-domain",
                    shuttle_version="1.0.0",
                    input_key="domain:example.org",
                    requested_by=user_id,
                    status=JobStatus.SUCCEEDED,
                    stats={},
                    started_at=sql("now()"),
                    finished_at=sql("now()"),
                )
                db.add(single)
                await db.commit()
            # A job outside any run releases nothing.
            assert await celery_app.release_after(settings, sessionmaker, single.id) == 0
            assert await celery_app.release_after(settings, sessionmaker, uuid.uuid4()) == 0
            first = await celery_app.release_all_runs(settings)
            queue = _SentQueue.last
            assert queue is not None
            sent = [str(job_id) for job_id in queue.sent]
            async with sessionmaker() as db:
                await db.execute(
                    sql(
                        "UPDATE jobs SET status = 'succeeded', started_at = now(), "
                        "finished_at = now() WHERE id = :id"
                    ),
                    {"id": queue.sent[0]},
                )
                await db.commit()
            after = await celery_app.release_after(settings, sessionmaker, queue.sent[0])
            async with sessionmaker() as db:
                released = await db.scalar(
                    sql("SELECT released FROM scan_runs WHERE id = :id"), {"id": run.id}
                )
            return first, after, int(released or 0), sent
        finally:
            async with sessionmaker() as db:
                await db.execute(sql("DELETE FROM jobs WHERE case_id = :id"), {"id": case_id})
                await db.execute(sql("DELETE FROM scan_runs WHERE case_id = :id"), {"id": case_id})
                await db.execute(
                    sql("DELETE FROM case_members WHERE case_id = :id"), {"id": case_id}
                )
                await db.execute(sql("DELETE FROM cases WHERE id = :id"), {"id": case_id})
                await db.execute(sql("DELETE FROM users WHERE id = :id"), {"id": user_id})
                await db.commit()
            await engine.dispose()

    first, after, released, sent = run_in_thread(scenario)
    get_settings.cache_clear()
    assert (first, after, released) == (5, 1, 6)
    assert len(sent) == 5


def test_reaper_first_fails_what_the_previous_run_left(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[str] = []

    async def own(settings: Settings, hostname: str) -> int:
        calls.append(hostname)
        return 1

    async def broken(settings: Settings, hostname: str) -> int:
        raise OSError("down")

    reaper = celery_app.Reaper(Settings(env="test"), hostname="worker@abc", reap_own=own)
    assert reaper.reap_previous_run() == 1
    assert calls == ["worker@abc"]
    assert celery_app.Reaper(Settings(env="test"), reap_own=own).reap_previous_run() is None
    failing = celery_app.Reaper(Settings(env="test"), hostname="w", reap_own=broken)
    assert failing.reap_previous_run() is None
    assert "Reaping this worker's previous jobs failed: OSError." in caplog.text


def test_noting_the_worker_is_best_effort(caplog: pytest.LogCaptureFixture) -> None:
    unreachable = Settings(env="test", valkey_url=SecretStr("redis://127.0.0.1:1/0"))
    job_id = uuid.uuid4()
    assert celery_app._note_running(unreachable, job_id, None) is False
    assert celery_app._note_running(unreachable, job_id, "worker@abc") is False
    celery_app._forget_running(unreachable, job_id)
    assert "could not note its worker: ConnectionError." in caplog.text
    assert "could not clear its worker: ConnectionError." in caplog.text


@pytest.mark.db
def test_a_restarted_worker_fails_the_jobs_its_previous_run_left(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    """docker compose kill + start: same node name, a new run; nothing of the old one runs."""
    monkeypatch.setenv("NIGHTLOOM_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("NIGHTLOOM_VALKEY_URL", TEST_VALKEY_URL)
    get_settings.cache_clear()
    settings = get_settings()
    hostname = f"worker@{uuid.uuid4().hex[:8]}"
    finished: list[uuid.UUID] = []

    async def fake_finish(sessionmaker: Any, job_id: uuid.UUID, outcome: Any) -> bool:
        assert outcome.reason == "worker_lost"
        finished.append(job_id)
        return True

    monkeypatch.setattr(celery_app, "finish_abandoned", fake_finish)
    previous, current, elsewhere = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = celery_app.RUNNING_KEY.format

    # A job of this run, noted the way the task does it.
    assert celery_app._note_running(settings, current, hostname)

    async def scenario() -> tuple[int, list[bool]]:
        client = Redis.from_url(TEST_VALKEY_URL)
        try:
            await client.set(key(job_id=previous), f"{hostname} an-earlier-boot", ex=60)
            await client.set(key(job_id=elsewhere), "worker@other an-earlier-boot", ex=60)
            await client.set(key(job_id="not-a-uuid"), f"{hostname} an-earlier-boot", ex=60)
            reaped = await celery_app.reap_own(settings, hostname)
            left = [
                bool(await client.exists(key(job_id=job_id)))
                for job_id in (previous, current, elsewhere)
            ]
            return reaped, left
        finally:
            for job_id in (previous, current, elsewhere, "not-a-uuid"):
                await client.delete(key(job_id=job_id))
            await client.aclose()

    reaped, left = run_in_thread(scenario)
    celery_app._forget_running(settings, current)
    get_settings.cache_clear()
    assert (reaped, finished) == (1, [previous])
    assert left == [False, True, True]
