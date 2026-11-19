"""A job's time limits, all derived from its Shuttle's ``timeout_seconds``.

Every running job has a **deadline**: ``started_at + timeout``. Four layers
make sure it ends, each one catching what the previous one missed:

1. **Timeout** (the runner): the Shuttle, its graph writes and its clean-up
   are bounded by the deadline; the outcome is recorded within
   ``FINISH_GRACE_SECONDS`` after it (see ``nightloom_worker.runner``).
2. **Soft limit** (Celery, ``deadline + SOFT_LIMIT_MARGIN_SECONDS``): the
   worker cancels the run and records it as failed.
3. **Hard limit** (Celery, ``soft + HARD_LIMIT_MARGIN_SECONDS``): the pool
   process is killed; the worker's main process records the job as failed.
4. **Reaper** (every minute, in a worker's main process): a job still
   ``running`` ``REAPER_MARGIN_SECONDS`` after its hard limit (the worker
   died with it) is failed.

The API reports a job as *overdue* once its hard limit has passed.
"""

from datetime import datetime, timedelta

from nightloom_sdk.meta import MAX_TIMEOUT_SECONDS
from nightloom_sdk.registry import ShuttleRegistry

# Seconds a task may run beyond its Shuttle's timeout: loading the job,
# checking guardrails and recording the outcome. The runner enforces the
# deadline itself; these limits only catch a stuck run.
SOFT_LIMIT_MARGIN_SECONDS = 60
HARD_LIMIT_MARGIN_SECONDS = 30
# The reaper fails a running job this long after its hard limit.
REAPER_MARGIN_SECONDS = 60
# The runner records the outcome within this long after the deadline.
FINISH_GRACE_SECONDS = 30


def time_limits(timeout_seconds: int) -> tuple[int, int]:
    """The (soft, hard) Celery time limits for a Shuttle with ``timeout_seconds``."""
    soft = timeout_seconds + SOFT_LIMIT_MARGIN_SECONDS
    return soft, soft + HARD_LIMIT_MARGIN_SECONDS


def shuttle_timeout(registry: ShuttleRegistry, shuttle_name: str) -> int:
    """The installed Shuttle's timeout; the largest allowed one if it is not installed."""
    shuttle = registry.get(shuttle_name)
    return shuttle.meta.timeout_seconds if shuttle is not None else MAX_TIMEOUT_SECONDS


def deadline(started_at: datetime, timeout_seconds: int) -> datetime:
    """When the run must end."""
    return started_at + timedelta(seconds=timeout_seconds)


def overdue_at(started_at: datetime, timeout_seconds: int) -> datetime:
    """When the worker's hard limit has passed: a job still running then has lost its worker."""
    return started_at + timedelta(seconds=time_limits(timeout_seconds)[1])


def stale_at(started_at: datetime, timeout_seconds: int) -> datetime:
    """When the reaper fails a job that is still running."""
    return overdue_at(started_at, timeout_seconds) + timedelta(seconds=REAPER_MARGIN_SECONDS)


__all__ = [
    "FINISH_GRACE_SECONDS",
    "HARD_LIMIT_MARGIN_SECONDS",
    "REAPER_MARGIN_SECONDS",
    "SOFT_LIMIT_MARGIN_SECONDS",
    "deadline",
    "overdue_at",
    "shuttle_timeout",
    "stale_at",
    "time_limits",
]
