"""Helpers for unit-testing a Shuttle without the worker, databases or network.

::

    from nightloom_sdk.testing import collect, entity_input

    async def test_finds_parent() -> None:
        findings = await collect(MyShuttle(), entity_input(EntityType.DOMAIN, "www.example.org"))
        assert findings == [...]

For a Shuttle that uses the network, pass an :class:`httpx.MockTransport` as
``transport``: requests then go to your handler instead of the internet, while
the scheme check, rate limit and response size cap still apply.
"""

import asyncio
import logging
from collections.abc import Iterable, Mapping

import httpx

from nightloom_core.entities import EntityType
from nightloom_core.normalize import normalize
from nightloom_sdk.context import ShuttleContext
from nightloom_sdk.evidence import EvidenceStore, MemoryEvidenceStore
from nightloom_sdk.findings import EntityFinding, Finding, RelationshipFinding
from nightloom_sdk.http import create_http_client
from nightloom_sdk.meta import ShuttleMeta
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.shuttle import EntityInput, Shuttle


def entity_input(entity_type: EntityType, value: str) -> EntityInput:
    """The input a Shuttle would get for ``value``, normalized like the case stores it."""
    normalized = normalize(entity_type, value)
    return EntityInput(type=normalized.type, value=normalized.value, key=normalized.key)


def fake_context(
    meta: ShuttleMeta,
    *,
    keys: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    limiter: HostRateLimiter | None = None,
    cancelled: bool = False,
    logger: logging.Logger | None = None,
    evidence: EvidenceStore | None = None,
) -> ShuttleContext:
    """A context for ``meta``; with ``transport``, its HTTP client never touches the network.

    Without a transport, a network Shuttle's ``ctx.http`` refuses every
    request, so a test cannot reach the internet by accident. Evidence goes to
    ``evidence``, by default a new :class:`MemoryEvidenceStore`.
    """
    event = asyncio.Event()
    if cancelled:
        event.set()
    http = None
    if meta.network:
        http = create_http_client(
            meta, transport=transport or httpx.MockTransport(_no_network), limiter=limiter
        )
    return ShuttleContext(
        meta,
        keys=keys,
        http=http,
        cancel_event=event,
        logger=logger,
        evidence=evidence if evidence is not None else MemoryEvidenceStore(),
    )


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(
        f"Unexpected request to {request.url.host}: pass a MockTransport to fake_context()."
    )


async def collect(
    shuttle: Shuttle,
    entity: EntityInput,
    *,
    ctx: ShuttleContext | None = None,
    limit: int = 10_000,
) -> list[Finding]:
    """Run ``shuttle`` on ``entity`` and return every finding (at most ``limit``)."""
    context = ctx or fake_context(shuttle.meta)
    findings: list[Finding] = []
    try:
        async for finding in shuttle.run(context, entity):
            findings.append(finding)
            if len(findings) >= limit:
                break
    finally:
        await context.aclose()
    return findings


def assert_declared(meta: ShuttleMeta, findings: Iterable[Finding], entity: EntityInput) -> None:
    """Fail unless every finding uses only the types ``meta`` declares (as the runner checks)."""
    endpoints = meta.output_entity_types | {entity.type}
    for finding in findings:
        match finding:
            case EntityFinding():
                problems = [] if finding.type in meta.output_entity_types else [finding.type]
            case RelationshipFinding():
                if finding.type not in meta.output_relationship_types:
                    raise AssertionError(
                        f"{finding.type.value} is not in output_relationship_types"
                    )
                problems = [
                    e.type for e in (finding.source, finding.target) if e.type not in endpoints
                ]
            case _:
                raise AssertionError(f"{type(finding).__name__} is not a Finding")
        if problems:
            raise AssertionError(f"{problems[0].value} is not a declared output entity type")


__all__ = ["assert_declared", "collect", "entity_input", "fake_context"]
