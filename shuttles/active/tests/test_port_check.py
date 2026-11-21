"""The TCP port check with a fake connector, plus the real connector on localhost."""

import asyncio
import ipaddress
from datetime import UTC, datetime

import pytest

from nightloom_core.entities import EntityType, build_entity
from nightloom_sdk import EntityFinding, Mode, RelationshipFinding
from nightloom_sdk.http import is_public_address
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_active import PortCheckShuttle
from nightloom_shuttle_active.ports import (
    COMMON_PORTS,
    CONCURRENCY,
    NOTE_NOT_PUBLIC,
    NOTE_SUMMARY,
    PortState,
    tcp_connect,
)

IP = EntityType.IP_ADDRESS
META = PortCheckShuttle.meta
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


class FakeConnector:
    def __init__(self, states: dict[int, PortState]) -> None:
        self.states = states
        self.calls: list[tuple[str, int, float]] = []
        self.running = 0
        self.most = 0

    async def __call__(self, address: str, port: int, timeout: float) -> PortState:  # noqa: ASYNC109
        self.calls.append((address, port, timeout))
        self.running += 1
        self.most = max(self.most, self.running)
        await asyncio.sleep(0)
        self.running -= 1
        return self.states.get(port, PortState.CLOSED)


def test_meta() -> None:
    assert META.mode == Mode.ACTIVE
    assert META.cache_ttl_seconds == 0
    assert len(COMMON_PORTS) == 20
    assert len(set(COMMON_PORTS)) == 20


async def test_open_ports_become_services() -> None:
    fake = FakeConnector({443: PortState.OPEN, 22: PortState.OPEN, 3389: PortState.SILENT})
    shuttle = PortCheckShuttle(connect=fake, address_filter=lambda _: True, now=lambda: NOW)
    ctx = fake_context(META)
    entity = entity_input(IP, "93.184.215.14")
    findings = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, findings, entity)
    assert {call[1] for call in fake.calls} == set(COMMON_PORTS)
    assert all(call[0] == "93.184.215.14" and call[2] == 3.0 for call in fake.calls)
    assert fake.most <= CONCURRENCY
    [address, *rest] = findings
    assert isinstance(address, EntityFinding)
    assert address.properties == {
        "tcp_checked_ports": sorted(COMMON_PORTS),
        "tcp_open_ports": [22, 443],
        "tcp_checked_at": NOW,
    }
    build_entity(IP, address.value, address.properties)
    services = [f.value for f in rest if isinstance(f, EntityFinding)]
    assert services == ["93.184.215.14:22/tcp", "93.184.215.14:443/tcp"]
    assert all(
        f.target.value == "93.184.215.14" for f in rest if isinstance(f, RelationshipFinding)
    )
    assert ctx.notes == (NOTE_SUMMARY.format(checked=20, open=2, closed=17, silent=1),)


async def test_ipv6_services_use_brackets() -> None:
    fake = FakeConnector({80: PortState.OPEN})
    shuttle = PortCheckShuttle(connect=fake, ports=[80, 81], address_filter=lambda _: True)
    findings = await collect(shuttle, entity_input(IP, "2001:4860::1"))
    assert [f.value for f in findings if isinstance(f, EntityFinding)][1:] == [
        "[2001:4860::1]:80/tcp"
    ]


async def test_private_addresses_are_never_contacted() -> None:
    fake = FakeConnector({})
    shuttle = PortCheckShuttle(connect=fake)
    ctx = fake_context(META)
    assert await collect(shuttle, entity_input(IP, "10.0.0.5"), ctx=ctx) == []
    assert fake.calls == []
    assert ctx.notes == (NOTE_NOT_PUBLIC,)
    assert not is_public_address(ipaddress.ip_address("192.0.2.1"))


async def test_the_real_connector_on_localhost() -> None:
    async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Nothing is sent by the connector: the server reads EOF at once.
        assert await reader.read() == b""
        writer.close()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        assert await tcp_connect("127.0.0.1", port, 2.0) == PortState.OPEN
    # The server is gone now: the port refuses.
    assert await tcp_connect("127.0.0.1", port, 2.0) == PortState.CLOSED


async def test_the_real_connector_reports_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    for error in (TimeoutError(), OSError("network unreachable")):

        async def fail(*_: object, error: BaseException = error) -> None:
            raise error

        monkeypatch.setattr(asyncio, "open_connection", fail)
        assert await tcp_connect("93.184.215.14", 9, 0.2) == PortState.SILENT
