"""run_bounded: a job's event loop always ends, and the soft limit cancels it."""

import asyncio
import logging
import os
import signal
import threading
import time
from collections.abc import AsyncIterator

import pytest
from billiard.pool import soft_timeout_sighandler
from celery.exceptions import SoftTimeLimitExceeded

from nightloom_worker.loop import SOFT_LIMIT_SIGNAL, run_bounded

SHUTDOWN = 0.3


def test_returns_the_result_and_restores_no_loop() -> None:
    async def main() -> int:
        return 42

    assert run_bounded(main()) == 42
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()


def test_errors_propagate() -> None:
    async def main() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        run_bounded(main())


def test_a_stuck_executor_thread_is_left_behind() -> None:
    stuck = threading.Event()

    async def main() -> str:
        loop = asyncio.get_running_loop()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await loop.run_in_executor(None, stuck.wait)  # e.g. getaddrinfo
        return "done"

    began = time.monotonic()
    try:
        assert run_bounded(main(), shutdown_seconds=SHUTDOWN) == "done"
        assert time.monotonic() - began < 2
    finally:
        stuck.set()


def test_leftover_tasks_that_ignore_cancellation_are_left_behind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                continue

    async def failing() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise KeyError("secret value") from None

    async def main() -> None:
        asyncio.get_running_loop().create_task(stubborn())
        asyncio.get_running_loop().create_task(failing())
        await asyncio.sleep(0)

    began = time.monotonic()
    run_bounded(main(), shutdown_seconds=SHUTDOWN)
    assert time.monotonic() - began < 2
    assert "1 task(s) ignored cancellation" in caplog.text
    assert "A leftover task failed: KeyError." in caplog.text
    assert "secret value" not in caplog.text


def test_async_generators_that_hang_on_close_are_left_behind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def generator() -> AsyncIterator[int]:
        try:
            yield 1
            yield 2
        finally:
            await asyncio.sleep(10)

    kept: list[AsyncIterator[int]] = []

    async def main() -> None:
        kept.append(generator())
        await anext(kept[0])  # left open: closed at shutdown

    began = time.monotonic()
    run_bounded(main(), shutdown_seconds=SHUTDOWN)
    assert time.monotonic() - began < 2
    assert "Closing async generators took over" in caplog.text


def test_cancellation_without_a_soft_limit_propagates() -> None:
    async def main() -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await asyncio.sleep(1)

    with pytest.raises(asyncio.CancelledError):
        run_bounded(main())


@pytest.mark.skipif(SOFT_LIMIT_SIGNAL is None, reason="no SIGUSR1 on this platform")
def test_soft_limit_signal_cancels_even_inside_a_loop_callback() -> None:
    """billiard's own handler would raise inside the callback, where asyncio swallows it."""
    assert threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGUSR1, soft_timeout_sighandler)
    reached: list[str] = []

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.call_soon(time.sleep, 0.5)  # the loop is blocked when the signal arrives
        try:
            await asyncio.sleep(30)
        finally:
            reached.append("cleanup")

    timer = threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGUSR1))
    timer.start()
    began = time.monotonic()
    try:
        with pytest.raises(SoftTimeLimitExceeded):
            run_bounded(main(), catch_soft_limit=True)
    finally:
        timer.cancel()
        restored = signal.getsignal(signal.SIGUSR1)
        signal.signal(signal.SIGUSR1, previous)
    assert time.monotonic() - began < 3
    assert reached == ["cleanup"]
    assert restored is soft_timeout_sighandler  # put back afterwards


def test_the_soft_limit_is_only_caught_in_the_main_thread() -> None:
    results: list[object] = []

    async def main() -> str:
        return "ok"

    def in_thread() -> None:
        results.append(run_bounded(main(), catch_soft_limit=True))

    thread = threading.Thread(target=in_thread)
    thread.start()
    thread.join(5)
    assert results == ["ok"]


def test_a_soft_limit_after_the_run_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)

    async def main() -> str:
        return "finished"

    assert run_bounded(main(), catch_soft_limit=True) == "finished"
