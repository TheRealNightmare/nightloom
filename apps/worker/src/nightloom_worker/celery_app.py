"""The worker's Celery app and its one task, ``run_job``.

Start it with::

    celery -A nightloom_worker.celery_app worker --queues shuttles --concurrency 4

Configuration comes from the same ``NIGHTLOOM_*`` settings as the API and
the same :func:`~nightloom_api.jobs.queue.create_celery`: Valkey as broker,
JSON only (no pickle), late acknowledgement, one task per worker process at
a time. Each task runs the job in a fresh event loop with its own database
and Neo4j clients, closed when it ends.
"""

import asyncio
import logging
import uuid
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import setup_logging

from nightloom_api.config import Settings, get_settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra import db as db_infra
from nightloom_api.infra import graph as graph_infra
from nightloom_api.jobs.models import JobStatus
from nightloom_api.jobs.queue import TASK_NAME, create_celery
from nightloom_api.logging import configure_logging
from nightloom_sdk.registry import default_registry
from nightloom_worker.runner import (
    ERROR_WORKER_LOST,
    Outcome,
    RunnerConfig,
    finish_abandoned,
    reap_stale_jobs,
    run_job,
)

logger = logging.getLogger(__name__)

app = create_celery(get_settings(), name="nightloom-worker")


@setup_logging.connect
def _configure_logging(**_: Any) -> None:
    """Use Nightloom's log format instead of Celery's (and never print secrets)."""
    configure_logging(get_settings().log_level)


async def _with_config(settings: Settings, job_id: uuid.UUID, *, abandoned: bool) -> str | None:
    engine = db_infra.create_engine(settings)
    driver = graph_infra.create_driver(settings)
    try:
        config = RunnerConfig(
            sessionmaker=db_infra.create_sessionmaker(engine),
            graph=GraphRepository(driver),
            registry=default_registry(),
        )
        if abandoned:
            await finish_abandoned(
                config,
                job_id,
                Outcome(JobStatus.FAILED, error=ERROR_WORKER_LOST, reason="worker_time_limit"),
            )
            return JobStatus.FAILED.value
        reaped = await reap_stale_jobs(config)
        if reaped:
            logger.warning("Failed %d abandoned job(s).", reaped)
        status = await run_job(job_id, config)
        return status.value if status is not None else None
    finally:
        await driver.close()
        await engine.dispose()


@app.task(name=TASK_NAME, acks_late=True)
def run_job_task(job_id: str) -> str | None:
    """Run one job. The argument is the job's ID; everything else is read from PostgreSQL."""
    try:
        parsed = uuid.UUID(job_id)
    except (TypeError, ValueError):
        logger.error("Ignoring a task with an invalid job ID.")
        return None
    settings = get_settings()
    try:
        return asyncio.run(_with_config(settings, parsed, abandoned=False))
    except SoftTimeLimitExceeded:
        logger.error("Job %s hit the worker's time limit.", parsed)
        return asyncio.run(_with_config(settings, parsed, abandoned=True))
