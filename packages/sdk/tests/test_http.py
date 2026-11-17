"""The guarded HTTP client: SSRF guard, schemes and redirects, size cap, rate limit.

Nothing here reaches the internet: host names resolve through fake resolvers,
and the only real connections go to a throwaway server on 127.0.0.1 that a
test explicitly allows.
"""

import asyncio
import gzip
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpcore
import httpx
import pytest

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_sdk.http import (
    MAX_RESPONSE_BYTES,
    DestinationNotAllowedError,
    GuardedHTTPTransport,
    GuardedNetworkBackend,
    ResponseTooLargeError,
    SchemeNotAllowedError,
    create_http_client,
    is_public_address,
    system_resolver,
    user_agent,
)
from nightloom_sdk.meta import RateLimit, ShuttleMeta
from nightloom_sdk.ratelimit import HostRateLimiter

META = ShuttleMeta(
    name="http-test",
    version="1.0.0",
    title="HTTP test",
    description="A network Shuttle used by the HTTP tests.",
    input_types=frozenset({EntityType.DOMAIN}),
    output_entity_types=frozenset({EntityType.IP_ADDRESS}),
    sensitivity=Sensitivity.INFRASTRUCTURE,
    network=True,
    rate_limit=RateLimit(requests=100, period_seconds=1),
)

# --- which addresses are public -------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",  # private
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",  # loopback
        "127.255.255.254",
        "169.254.169.254",  # link-local (cloud metadata)
        "0.0.0.0",  # noqa: S104 - unspecified, the point of the test
        "100.64.0.1",  # shared address space (CGNAT)
        "192.0.2.1",  # documentation
        "198.18.0.1",  # benchmarking
        "224.0.0.1",  # multicast
        "239.255.255.250",
        "240.0.0.1",  # reserved
        "255.255.255.255",
        "::1",  # loopback
        "::",  # unspecified
        "fe80::1",  # link-local
        "fe80::1%2",  # link-local with a zone
        "fc00::1",  # unique local (ULA)
        "fd12:3456:789a::1",
        "fec0::1",  # site-local (deprecated)
        "ff02::1",  # multicast
        "2001:db8::1",  # documentation
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "::ffff:169.254.169.254",
        "::127.0.0.1",  # IPv4-compatible (deprecated)
        "2002:7f00:1::1",  # 6to4 of 127.0.0.1
        "2002:a00:1::1",  # 6to4 of 10.0.0.1
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "64:ff9b:1::a00:1",  # local-use NAT64 prefix
        "2001:0:4136:e378:8000:63bf:f5ff:fffe",  # Teredo, client 10.0.0.1
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert not is_public_address(address)


@pytest.mark.parametrize("address", ["93.184.215.14", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_address(address)


# --- the network backend ----------------------------------------------------------------------


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Records what it was asked to connect to; fails for addresses in ``fail``."""

    def __init__(self, fail: frozenset[str] = frozenset()) -> None:
        self.connected: list[tuple[str, int]] = []
        self.fail = fail

    async def connect_tcp(self, host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        self.connected.append((host, port))
        if host in self.fail:
            raise httpcore.ConnectError("refused")
        return httpcore.AsyncMockStream([])

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("never")

    async def sleep(self, seconds: float) -> None:
        self.slept = seconds


def resolver(table: dict[str, list[str]]) -> Callable[[str, int], Any]:
    async def resolve(host: str, port: int) -> list[str]:
        return table[host]

    return resolve


async def test_backend_refuses_a_name_resolving_to_a_private_address() -> None:
    inner = RecordingBackend()
    backend = GuardedNetworkBackend(resolver=resolver({"internal.test": ["10.0.0.5"]}), inner=inner)
    with pytest.raises(DestinationNotAllowedError, match=r"internal\.test"):
        await backend.connect_tcp("internal.test", 443)
    assert inner.connected == []


async def test_backend_refuses_when_any_address_is_private() -> None:
    inner = RecordingBackend()
    backend = GuardedNetworkBackend(
        resolver=resolver({"mixed.test": ["93.184.215.14", "127.0.0.1"]}), inner=inner
    )
    with pytest.raises(DestinationNotAllowedError):
        await backend.connect_tcp("mixed.test", 80)
    assert inner.connected == []


async def test_backend_connects_to_the_checked_address_not_the_name() -> None:
    inner = RecordingBackend()
    backend = GuardedNetworkBackend(
        resolver=resolver({"public.test": ["93.184.215.14"]}), inner=inner
    )
    await backend.connect_tcp("public.test", 443)
    assert inner.connected == [("93.184.215.14", 443)]


async def test_dns_rebinding_to_a_private_address_is_refused() -> None:
    answers = iter([["93.184.215.14"], ["127.0.0.1"]])

    async def rebinding(host: str, port: int) -> list[str]:
        return next(answers)

    inner = RecordingBackend()
    backend = GuardedNetworkBackend(resolver=rebinding, inner=inner)
    await backend.connect_tcp("rebind.test", 80)
    with pytest.raises(DestinationNotAllowedError):
        await backend.connect_tcp("rebind.test", 80)
    # Only the first, checked, public address was ever connected to.
    assert inner.connected == [("93.184.215.14", 80)]


async def test_backend_tries_the_next_address_when_one_fails() -> None:
    inner = RecordingBackend(fail=frozenset({"93.184.215.14"}))
    backend = GuardedNetworkBackend(
        resolver=resolver({"two.test": ["93.184.215.14", "1.1.1.1"]}), inner=inner
    )
    await backend.connect_tcp("two.test", 80)
    assert [host for host, _ in inner.connected] == ["93.184.215.14", "1.1.1.1"]
    inner = RecordingBackend(fail=frozenset({"1.1.1.1"}))
    backend = GuardedNetworkBackend(resolver=resolver({"one.test": ["1.1.1.1"]}), inner=inner)
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("one.test", 80)


async def test_resolution_failures() -> None:
    async def fails(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    async def empty(host: str, port: int) -> list[str]:
        return []

    async def slow(host: str, port: int) -> list[str]:
        await asyncio.sleep(10)
        return []

    async def garbage(host: str, port: int) -> list[str]:
        return ["not-an-address"]

    for broken, error in (
        (fails, httpcore.ConnectError),
        (empty, httpcore.ConnectError),
        (slow, httpcore.ConnectTimeout),
        (garbage, DestinationNotAllowedError),
    ):
        backend = GuardedNetworkBackend(resolver=broken, inner=RecordingBackend())
        with pytest.raises(error):
            await backend.connect_tcp("broken.test", 80, timeout=0.01)


async def test_unix_sockets_are_refused() -> None:
    backend = GuardedNetworkBackend(inner=RecordingBackend())
    with pytest.raises(DestinationNotAllowedError):
        await backend.connect_unix_socket("/var/run/docker.sock")
    await backend.sleep(0)


async def test_system_resolver_handles_literal_addresses() -> None:
    assert await system_resolver("127.0.0.1", 80) == ["127.0.0.1"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
    ],
)
async def test_client_refuses_literal_private_destinations(url: str) -> None:
    async with create_http_client(META) as client:
        with pytest.raises(DestinationNotAllowedError):
            await client.get(url)


async def test_client_refuses_names_resolving_to_private_addresses() -> None:
    backend = GuardedNetworkBackend(resolver=resolver({"localhost.test": ["127.0.0.1"]}))
    async with create_http_client(META, transport=GuardedHTTPTransport(backend)) as client:
        with pytest.raises(DestinationNotAllowedError):
            await client.get("https://localhost.test/")


# --- a real local server, allowed explicitly --------------------------------------------------

Handler = Callable[[bytes], bytes]


@pytest.fixture
async def local_server() -> AsyncIterator[tuple[int, list[Handler]]]:
    """A tiny HTTP/1.1 server on 127.0.0.1; ``handlers`` answer requests in order."""
    handlers: list[Handler] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        writer.write(handlers.pop(0)(request))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    async with server:
        yield port, handlers


def response(status: str, body: bytes = b"", headers: str = "") -> Handler:
    def answer(_request: bytes) -> bytes:
        head = f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n"
        return head.encode() + headers.encode() + b"\r\n" + body

    return answer


def loopback_client(port: int, **kwargs: Any) -> httpx.AsyncClient:
    """A client whose backend treats 127.0.0.1 as public (and nothing else private)."""
    backend = GuardedNetworkBackend(
        resolver=resolver({"public.test": ["127.0.0.1"], "internal.test": ["10.0.0.1"]}),
        is_allowed=lambda address: address == "127.0.0.1" or is_public_address(address),
    )
    return create_http_client(META, transport=GuardedHTTPTransport(backend), **kwargs)


async def test_real_request_through_the_guarded_transport(
    local_server: tuple[int, list[Handler]],
) -> None:
    port, handlers = local_server
    seen: list[bytes] = []

    def answer(request: bytes) -> bytes:
        seen.append(request)
        return response("200 OK", b"hello")(request)

    handlers.append(answer)
    async with loopback_client(port) as client:
        reply = await client.get(f"http://public.test:{port}/path")
    assert (reply.status_code, reply.text) == (200, "hello")
    assert b"User-Agent: Nightloom/" in seen[0]
    assert b"Accept-Encoding: identity" in seen[0]


async def test_redirect_to_a_private_destination_is_refused(
    local_server: tuple[int, list[Handler]],
) -> None:
    port, handlers = local_server
    handlers.append(response("302 Found", headers="Location: http://internal.test/admin\r\n"))
    async with loopback_client(port) as client:
        with pytest.raises(DestinationNotAllowedError):
            await client.get(f"http://public.test:{port}/")


async def test_size_cap_on_a_real_connection(local_server: tuple[int, list[Handler]]) -> None:
    port, handlers = local_server
    handlers.append(response("200 OK", b"x" * 2_000))
    async with loopback_client(port, max_response_bytes=1_000) as client:
        with pytest.raises(ResponseTooLargeError):
            await client.get(f"http://public.test:{port}/")


async def test_connection_errors_become_httpx_errors(
    local_server: tuple[int, list[Handler]],
) -> None:
    port, handlers = local_server

    def hang_up(_request: bytes) -> bytes:
        return b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort"

    handlers.append(hang_up)
    async with loopback_client(port) as client:
        with pytest.raises(httpx.TransportError):
            await client.get(f"http://public.test:{port}/")


# --- schemes and redirects (mock transport) ----------------------------------------------


def mock_client(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> Any:
    return create_http_client(META, transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize("url", ["ftp://example.org/file", "file:///etc/passwd"])
async def test_non_http_schemes_are_refused(url: str) -> None:
    async with mock_client(lambda request: httpx.Response(200)) as client:
        with pytest.raises(SchemeNotAllowedError):
            await client.get(url)


@pytest.mark.parametrize(
    ("location", "error"),
    [
        ("file:///etc/passwd", SchemeNotAllowedError),
        ("ftp://example.org/", SchemeNotAllowedError),
        ("gopher://example.org/", SchemeNotAllowedError),
        # httpx refuses this one as an invalid URL before it is sent.
        ("javascript:alert(1)", httpx.InvalidURL),
    ],
)
async def test_redirects_to_non_http_schemes_are_refused(
    location: str, error: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": location})

    async with mock_client(handler) as client:
        with pytest.raises(error):
            await client.get("https://example.org/")


async def test_http_redirects_are_followed_up_to_the_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/final":
            return httpx.Response(200, text="done")
        return httpx.Response(301, headers={"Location": "https://example.net/final"})

    async with mock_client(handler) as client:
        reply = await client.get("http://example.org/start")
    assert reply.text == "done"
    assert [r.status_code for r in reply.history] == [301]

    def loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"})

    async with mock_client(loop) as client:
        with pytest.raises(httpx.TooManyRedirects):
            await client.get("https://example.org/")


# --- response size -------------------------------------------------------------------------


async def test_response_at_the_limit_is_read_and_over_it_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        size = int(request.url.params["size"])
        return httpx.Response(200, content=b"a" * size)

    async with mock_client(handler, max_response_bytes=1_000) as client:
        assert len((await client.get("https://example.org/?size=1000")).content) == 1_000
        with pytest.raises(ResponseTooLargeError):
            await client.get("https://example.org/?size=1001")


async def test_size_cap_is_enforced_while_streaming() -> None:
    chunks_sent: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        for _ in range(100):
            chunks_sent.append(1)
            yield b"b" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    async with (
        mock_client(handler, max_response_bytes=1_000) as client,
        client.stream("GET", "https://example.org/") as reply,
    ):
        with pytest.raises(ResponseTooLargeError):
            async for _ in reply.aiter_bytes():
                pass
    # Stopped right after the limit, not after reading everything.
    assert len(chunks_sent) == 11


async def test_compressed_response_cannot_expand_past_the_limit() -> None:
    bomb = gzip.compress(b"\0" * 100_000)
    assert len(bomb) < 1_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bomb, headers={"Content-Encoding": "gzip"})

    async with mock_client(handler, max_response_bytes=1_000) as client:
        with pytest.raises(ResponseTooLargeError):
            await client.get("https://example.org/")


def test_default_limit_is_five_megabytes() -> None:
    assert MAX_RESPONSE_BYTES == 5 * 1024 * 1024


# --- rate limit and identity ---------------------------------------------------------------


async def test_every_request_and_redirect_takes_a_token() -> None:
    hosts: list[str] = []

    class Spy(HostRateLimiter):
        async def acquire(self, host: str) -> float:
            hosts.append(host)
            return 0.0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.org":
            return httpx.Response(302, headers={"Location": "https://cdn.example.net/x"})
        return httpx.Response(200)

    limiter = Spy(RateLimit(requests=1, period_seconds=1))
    async with mock_client(handler, limiter=limiter) as client:
        await client.get("https://example.org/")
        await client.get("https://example.org/")
    assert hosts == ["example.org", "cdn.example.net"] * 2


async def test_rate_limit_delays_requests_beyond_the_burst() -> None:
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = HostRateLimiter(
        RateLimit(requests=2, period_seconds=10), clock=lambda: now[0], sleep=sleep
    )
    async with mock_client(lambda request: httpx.Response(200), limiter=limiter) as client:
        for _ in range(3):
            await client.get("https://example.org/")
    assert slept == [pytest.approx(5)]


def test_user_agent_names_the_project() -> None:
    agent = user_agent()
    assert agent.startswith("Nightloom/")
    assert agent.endswith("(+https://github.com/TheRealNightmare/nightloom)")


async def test_environment_proxies_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    async with create_http_client(META) as client:
        assert client.trust_env is False
