"""Runs one job through the real Celery task in this process's main thread (tests only).

``python limits_child.py <job id> <mode>``. The process installs billiard's
soft-time-limit signal handler, as a prefork pool child does, so the test can
send it a real ``SIGUSR1``. Settings come from the environment (``NIGHTLOOM_*``).

Modes (the Shuttle is named ``limits`` and has a one-second timeout):

- ``block``: blocks the event loop for three seconds inside a loop callback,
  where an exception raised by a signal handler is caught and logged by
  asyncio instead of propagating.
"""

import asyncio
import signal
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

from billiard.pool import soft_timeout_sighandler

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import EntityInput, Shuttle, ShuttleContext, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_worker import celery_app

BLOCK_SECONDS = 3.0


class Limits(Shuttle):
    meta = ShuttleMeta(
        name="limits",
        version="1.0.0",
        title="Limits",
        description="A fake Shuttle that misbehaves for the time limit tests.",
        input_types=frozenset({EntityType.DOMAIN}),
        output_entity_types=frozenset({EntityType.DOMAIN}),
        output_relationship_types=frozenset({RelationType.HAS_SUBDOMAIN}),
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=False,
        timeout_seconds=1,
        default_admiralty="C3",
        default_confidence=0.6,
    )

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Any]:
        loop = asyncio.get_running_loop()
        loop.call_soon(time.sleep, BLOCK_SECONDS)
        await asyncio.sleep(60)
        yield None  # pragma: no cover - never reached


def main() -> None:
    job_id = sys.argv[1]
    setattr(celery_app, "default_registry", lambda: ShuttleRegistry([Limits]))  # noqa: B010
    signal.signal(signal.SIGUSR1, soft_timeout_sighandler)
    print("ready", flush=True)
    result = celery_app.run_job_task.run(job_id)
    print(f"result {result}", flush=True)


if __name__ == "__main__":
    main()
