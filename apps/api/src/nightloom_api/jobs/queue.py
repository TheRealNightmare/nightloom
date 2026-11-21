"""Handing jobs to the worker through Celery on Valkey.

The API only *sends* tasks; the worker (``apps/worker``) builds its Celery app
with the same :func:`create_celery` and defines the task. Tasks carry nothing
but the job's ID: everything else is read from PostgreSQL when the job runs,
so a message on the broker never holds case data or secrets.

Only JSON is accepted (never pickle), tasks are acknowledged after they ran
(``acks_late``) and a worker takes one task at a time.
"""

import uuid
from functools import partial
from typing import Any, Protocol

import anyio.to_thread
from celery import Celery
from fastapi import Request

from nightloom_api.config import Settings
from nightloom_api.jobs.deadlines import (
    HARD_LIMIT_MARGIN_SECONDS,
    SOFT_LIMIT_MARGIN_SECONDS,
    time_limits,
)
from nightloom_sdk.meta import MAX_TIMEOUT_SECONDS

TASK_NAME = "nightloom.run_job"
# Looks for an ownership verification's proof (see nightloom_api.ownership).
CHECK_TASK_NAME = "nightloom.check_ownership"
# Seconds a check may take, at most (DNS and one HTTPS request, with retries).
CHECK_SOFT_LIMIT_SECONDS = 60
CHECK_HARD_LIMIT_SECONDS = 90
QUEUE_NAME = "shuttles"
# The broker re-delivers a task not acknowledged within this time. It must be
# longer than any task can run, or a long job would run twice.
VISIBILITY_TIMEOUT_SECONDS = 3600


def celery_config(settings: Settings) -> dict[str, Any]:
    url = settings.valkey_url.get_secret_value()
    default_soft, default_hard = time_limits(MAX_TIMEOUT_SECONDS)
    return {
        "broker_url": url,
        "result_backend": url,
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "result_accept_content": ["json"],
        "task_acks_late": True,
        "task_reject_on_worker_lost": True,
        "worker_prefetch_multiplier": 1,
        "task_default_queue": QUEUE_NAME,
        # Job state lives in PostgreSQL; nothing reads Celery results.
        "task_ignore_result": True,
        "task_soft_time_limit": default_soft,
        "task_time_limit": default_hard,
        "broker_connection_retry_on_startup": True,
        "broker_transport_options": {"visibility_timeout": VISIBILITY_TIMEOUT_SECONDS},
        "worker_hijack_root_logger": False,
        "worker_send_task_events": False,
        "enable_utc": True,
    }


def create_celery(settings: Settings, name: str = "nightloom") -> Celery:
    """A Celery app configured for Nightloom. Connects lazily, on first use."""
    app = Celery(name, set_as_current=False)
    app.conf.update(celery_config(settings))
    return app


class JobQueue(Protocol):
    async def enqueue(self, job_id: uuid.UUID, *, timeout_seconds: int) -> None:
        """Hand the (committed) job to the worker."""

    async def enqueue_check(self, verification_id: uuid.UUID) -> None:
        """Ask the worker to look for an ownership verification's proof."""

    def close(self) -> None:
        """Release connections."""


class CeleryJobQueue:
    """Sends ``run_job`` tasks. The blocking broker call runs in a worker thread."""

    def __init__(self, settings: Settings) -> None:
        self._app = create_celery(settings, name="nightloom-api")

    async def enqueue(self, job_id: uuid.UUID, *, timeout_seconds: int) -> None:
        soft, hard = time_limits(timeout_seconds)
        send = partial(
            self._app.send_task,
            TASK_NAME,
            args=[str(job_id)],
            task_id=str(job_id),
            queue=QUEUE_NAME,
            soft_time_limit=soft,
            time_limit=hard,
            retry=True,
            retry_policy={"max_retries": 2, "interval_start": 0.2, "interval_step": 0.5},
        )
        await anyio.to_thread.run_sync(send)

    async def enqueue_check(self, verification_id: uuid.UUID) -> None:
        send = partial(
            self._app.send_task,
            CHECK_TASK_NAME,
            args=[str(verification_id)],
            queue=QUEUE_NAME,
            soft_time_limit=CHECK_SOFT_LIMIT_SECONDS,
            time_limit=CHECK_HARD_LIMIT_SECONDS,
            retry=True,
            retry_policy={"max_retries": 2, "interval_start": 0.2, "interval_step": 0.5},
        )
        await anyio.to_thread.run_sync(send)

    def close(self) -> None:
        self._app.close()


def get_job_queue(request: Request) -> JobQueue:
    """FastAPI dependency: the process-wide job queue (overridden in tests)."""
    queue: JobQueue = request.app.state.job_queue
    return queue


__all__ = [
    "CHECK_HARD_LIMIT_SECONDS",
    "CHECK_SOFT_LIMIT_SECONDS",
    "CHECK_TASK_NAME",
    "HARD_LIMIT_MARGIN_SECONDS",
    "QUEUE_NAME",
    "SOFT_LIMIT_MARGIN_SECONDS",
    "TASK_NAME",
    "CeleryJobQueue",
    "JobQueue",
    "celery_config",
    "create_celery",
    "get_job_queue",
    "time_limits",
]
