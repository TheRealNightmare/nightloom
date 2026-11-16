"""Readiness endpoint: can the API reach every backing service?"""

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from nightloom_api.config import Settings, get_settings
from nightloom_api.infra import Check, DependencyName, Infra
from nightloom_api.infra.errors import describe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class CheckResult(BaseModel):
    """Outcome of one dependency check."""

    ok: bool
    latency_ms: float
    error: str | None = None


class ReadyResponse(BaseModel):
    """Per-dependency readiness. ``status`` is ``ready`` only when every check is ok."""

    status: Literal["ready", "not_ready"]
    checks: dict[DependencyName, CheckResult]


def get_readiness_checks(request: Request) -> Mapping[DependencyName, Check]:
    """FastAPI dependency: the checks to run (overridden in tests)."""
    infra: Infra = request.app.state.infra
    return infra.checks()


async def run_check(check: Check, limit_seconds: float, secrets: list[str]) -> CheckResult:
    """Run one check with a time limit and turn any failure into a redacted result."""
    started = time.perf_counter()
    error: str | None = None
    try:
        async with asyncio.timeout(limit_seconds):
            await check()
    except TimeoutError:
        error = f"timed out after {limit_seconds:g}s"
    except Exception as exc:
        error = describe_error(exc, secrets)
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return CheckResult(ok=error is None, latency_ms=latency_ms, error=error)


_NOT_READY_RESPONSE: dict[int | str, dict[str, object]] = {
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": ReadyResponse,
        "description": "At least one dependency is unreachable",
    }
}


@router.get("/ready", response_model=ReadyResponse, responses=_NOT_READY_RESPONSE)
async def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    checks: Annotated[Mapping[DependencyName, Check], Depends(get_readiness_checks)],
) -> ReadyResponse:
    """Ping PostgreSQL, Neo4j, Valkey and object storage concurrently.

    Returns 200 when all of them answer within ``dependency_timeout_seconds``,
    503 otherwise. Error messages never contain credentials or URLs.
    """
    secrets = settings.secret_values()
    names = list(checks)
    results = await asyncio.gather(
        *(run_check(checks[name], settings.dependency_timeout_seconds, secrets) for name in names)
    )
    by_name = dict(zip(names, results, strict=True))

    for name, result in by_name.items():
        if not result.ok:
            logger.warning("Readiness check %s failed: %s", name, result.error)

    all_ok = all(result.ok for result in results)
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if all_ok else "not_ready", checks=by_name)
