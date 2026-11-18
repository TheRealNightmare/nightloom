"""The Celery task wrapper; one test runs the real task against the test databases."""

import asyncio
import uuid

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from pydantic import SecretStr

from nightloom_api.config import Settings, get_settings
from nightloom_api.jobs.queue import TASK_NAME
from nightloom_worker import celery_app
from support import TEST_DATABASE_URL, graph_test_settings, run_in_thread


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
