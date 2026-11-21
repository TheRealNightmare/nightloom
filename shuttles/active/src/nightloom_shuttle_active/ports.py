"""The TCP port check: which common ports of a verified address accept a connection.

**Active**: it connects to the address itself, so it runs only on addresses
the case has verified (an address a verified domain resolves to; the worker
resolves the domain again just before the run). It is deliberately gentle:

- a plain TCP connect to each of :data:`COMMON_PORTS` (20 well-known ports),
  at most :data:`CONCURRENCY` at a time, each with a :data:`CONNECT_TIMEOUT`
  second timeout;
- nothing is sent after connecting: the connection is closed at once (no
  banners, no service probes);
- only public addresses are contacted (the SDK's ``is_public_address``);
  anything else is skipped with a note and no connection.

A port is **open** when the connection was accepted, **closed** when it was
refused, and **no answer** when it timed out or the network said it was
unreachable (often a firewall). It yields the input IPAddress with
``tcp_checked_ports``, ``tcp_open_ports`` and ``tcp_checked_at``, and each
open port as a Service ``<ip>:<port>/tcp`` that ``RUNS_ON`` the address.

Provenance **A2** (confidence 0.9): observed directly, now, from Nightloom's
own vantage point (a firewall may answer differently elsewhere).
"""

import asyncio
import contextlib
import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nightloom_core.entities import PORT_CHECK_MAX_PORTS, EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    Finding,
    Mode,
    RateLimit,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleMeta,
)
from nightloom_sdk.http import is_public_address

IP = EntityType.IP_ADDRESS
SERVICE = EntityType.SERVICE

# FTP, SSH, SMTP, DNS, HTTP, POP3, IMAP, HTTPS, SMTPS, submission, IMAPS,
# POP3S, MySQL, RDP, PostgreSQL, Redis, HTTP alt, HTTPS alt, Elasticsearch,
# MongoDB.
COMMON_PORTS: tuple[int, ...] = (
    21, 22, 25, 53, 80, 110, 143, 443, 465, 587,
    993, 995, 3306, 3389, 5432, 6379, 8080, 8443, 9200, 27017,
)  # fmt: skip
assert len(COMMON_PORTS) <= PORT_CHECK_MAX_PORTS  # noqa: S101 - a constant's bound
CONCURRENCY = 4
CONNECT_TIMEOUT = 3.0
CLOSE_TIMEOUT = 1.0
TIMEOUT_SECONDS = 60
ADMIRALTY, CONFIDENCE = "A2", 0.9

NOTE_NOT_PUBLIC = "This address is not publicly routable, so it was not contacted."
NOTE_SUMMARY = "Checked {checked} ports: {open} open, {closed} closed, {silent} did not answer."


class PortState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    # Timed out or unreachable: often a firewall dropping the connection.
    SILENT = "silent"


Connect = Callable[[str, int, float], Awaitable[PortState]]
AddressFilter = Callable[[ipaddress.IPv4Address | ipaddress.IPv6Address], bool]
Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def tcp_connect(address: str, port: int, timeout: float) -> PortState:  # noqa: ASYNC109
    """Open a TCP connection to the address literal and close it at once. Sends nothing."""
    try:
        async with asyncio.timeout(timeout):
            _reader, writer = await asyncio.open_connection(address, port)
    except TimeoutError:
        return PortState.SILENT
    except ConnectionRefusedError:
        return PortState.CLOSED
    except OSError:
        return PortState.SILENT
    writer.close()
    with contextlib.suppress(Exception):
        async with asyncio.timeout(CLOSE_TIMEOUT):
            await writer.wait_closed()
    return PortState.OPEN


class PortCheckShuttle(Shuttle):
    """Common open TCP ports of a verified address. See the module docstring."""

    meta = ShuttleMeta(
        name="port-check",
        version="1.0.0",
        title="Common open ports (TCP connect)",
        description=(
            "Tries a plain TCP connection to 20 common ports of the address (web, mail, SSH, "
            "DNS, databases, remote desktop), four at a time, and closes each at once without "
            "sending anything. Adds each port that accepted the connection as a Service. "
            "Active: it contacts the address, so it runs only on addresses this case has "
            "verified."
        ),
        input_types=frozenset({IP}),
        output_entity_types=frozenset({IP, SERVICE}),
        output_relationship_types=frozenset({RelationType.RUNS_ON}),
        mode=Mode.ACTIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        rate_limit=RateLimit(requests=CONCURRENCY, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
    )

    def __init__(
        self,
        *,
        connect: Connect = tcp_connect,
        ports: Sequence[int] = COMMON_PORTS,
        address_filter: AddressFilter = is_public_address,
        now: Now = _utcnow,
    ) -> None:
        """``connect``, ``address_filter`` and ``now`` replace the real ones in tests."""
        self._connect = connect
        self._ports = tuple(ports)
        self._address_filter = address_filter
        self._now = now

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        address = ipaddress.ip_address(entity.value)
        if not self._address_filter(address):
            ctx.note(NOTE_NOT_PUBLIC)
            return
        ctx.progress(f"Trying {len(self._ports)} ports, {CONCURRENCY} at a time.")
        states = await self._check(ctx, str(address))
        opened = sorted(port for port, state in states.items() if state == PortState.OPEN)
        closed = sum(1 for state in states.values() if state == PortState.CLOSED)
        ctx.note(
            NOTE_SUMMARY.format(
                checked=len(states),
                open=len(opened),
                closed=closed,
                silent=len(states) - len(opened) - closed,
            )
        )
        properties: dict[str, Any] = {
            "tcp_checked_ports": sorted(states),
            "tcp_open_ports": opened,
            "tcp_checked_at": self._now(),
        }
        yield EntityFinding(type=IP, value=entity.value, properties=properties)
        literal = f"[{address}]" if address.version == 6 else str(address)
        for port in opened:
            service = f"{literal}:{port}/tcp"
            yield EntityFinding(type=SERVICE, value=service)
            yield RelationshipFinding(
                type=RelationType.RUNS_ON,
                source=EntityRef(type=SERVICE, value=service),
                target=entity.ref,
            )

    async def _check(self, ctx: ShuttleContext, address: str) -> dict[int, PortState]:
        limit = asyncio.Semaphore(CONCURRENCY)
        states: dict[int, PortState] = {}

        async def one(port: int) -> None:
            async with limit:
                ctx.raise_if_cancelled()
                states[port] = await self._connect(address, port, CONNECT_TIMEOUT)

        async with asyncio.TaskGroup() as group:
            for port in dict.fromkeys(self._ports):
                group.create_task(one(port))
        return states


__all__ = [
    "COMMON_PORTS",
    "CONCURRENCY",
    "CONNECT_TIMEOUT",
    "NOTE_NOT_PUBLIC",
    "NOTE_SUMMARY",
    "PortCheckShuttle",
    "PortState",
    "tcp_connect",
]
