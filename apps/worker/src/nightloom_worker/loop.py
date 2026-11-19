"""Running a job's coroutine on a fresh event loop that always ends.

:func:`asyncio.run` has two waits that a stuck job turns into a hang after
the job itself is over:

- it waits for every leftover task to finish cancelling, and a task that
  ignores cancellation never does;
- it waits for the default thread pool, where ``loop.getaddrinfo`` runs, so
  a DNS lookup that never returns keeps the Celery task alive (Python waits
  up to five minutes, longer than the soft limit leaves).

:func:`run_bounded` gives each of those a time limit and leaves stuck tasks
and threads behind (the pool process is replaced eventually; a thread stuck
in ``getaddrinfo`` cannot be stopped from Python anyway).

**The soft time limit.** Celery (billiard) tells the pool process with
``SIGUSR1``; its handler raises ``SoftTimeLimitExceeded`` wherever the main
thread happens to be. Inside an event loop that is often a loop callback,
where asyncio logs the exception and carries on, so the job is never told.
With ``catch_soft_limit=True`` (main thread only) the signal instead sets a
flag and cancels the main task through the loop's thread-safe wake-up, and
:func:`run_bounded` then raises ``SoftTimeLimitExceeded`` itself, from a
known place, so the task can record the job as failed.
"""

import asyncio
import contextlib
import logging
import signal
import threading
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from types import FrameType
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)

# Seconds to wait, at the end, for leftover tasks to cancel and async
# generators to close.
SHUTDOWN_SECONDS = 5.0
SOFT_LIMIT_SIGNAL = getattr(signal, "SIGUSR1", None)


def run_bounded[T](
    main: Coroutine[Any, Any, T],
    *,
    shutdown_seconds: float = SHUTDOWN_SECONDS,
    catch_soft_limit: bool = False,
) -> T:
    """Run ``main`` on a new event loop and close it within ``shutdown_seconds`` after.

    With ``catch_soft_limit``, Celery's soft-limit signal cancels ``main``
    and this raises ``SoftTimeLimitExceeded`` (see the module docstring).
    """
    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(thread_name_prefix="nightloom-job")
    loop.set_default_executor(executor)
    task = loop.create_task(main)
    soft_limit = threading.Event()

    def on_soft_limit(signum: int, frame: FrameType | None) -> None:
        soft_limit.set()
        # Never raise here: this may run in the middle of any loop callback.
        with contextlib.suppress(RuntimeError):  # the loop is already closed
            loop.call_soon_threadsafe(task.cancel)

    previous: Any = None
    install = (
        catch_soft_limit
        and SOFT_LIMIT_SIGNAL is not None
        and threading.current_thread() is threading.main_thread()
    )
    if install:
        previous = signal.signal(SOFT_LIMIT_SIGNAL, on_soft_limit)  # type: ignore[arg-type]
    try:
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(task)
        except asyncio.CancelledError:
            if soft_limit.is_set():
                raise SoftTimeLimitExceeded() from None
            raise
    finally:
        if install:
            signal.signal(SOFT_LIMIT_SIGNAL, previous)  # type: ignore[arg-type]
        try:
            _cancel_leftovers(loop, shutdown_seconds)
            _close_async_generators(loop, shutdown_seconds)
        finally:
            # Never waits: a thread stuck in getaddrinfo is left behind.
            executor.shutdown(wait=False, cancel_futures=True)
            asyncio.set_event_loop(None)
            loop.close()


def _cancel_leftovers(loop: asyncio.AbstractEventLoop, seconds: float) -> None:
    tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    loop.run_until_complete(asyncio.wait(tasks, timeout=seconds))
    stuck = [task for task in tasks if not task.done()]
    if stuck:
        logger.warning(
            "%d task(s) ignored cancellation for %.1f s; leaving them behind.", len(stuck), seconds
        )
    for task in tasks:
        if task.done() and not task.cancelled() and task.exception() is not None:
            # Only the type: the text may quote a response or a value.
            logger.warning("A leftover task failed: %s.", type(task.exception()).__name__)


def _close_async_generators(loop: asyncio.AbstractEventLoop, seconds: float) -> None:
    try:
        loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), seconds))
    except TimeoutError:
        logger.warning("Closing async generators took over %.1f s; leaving them.", seconds)


__all__ = ["SHUTDOWN_SECONDS", "SOFT_LIMIT_SIGNAL", "run_bounded"]
