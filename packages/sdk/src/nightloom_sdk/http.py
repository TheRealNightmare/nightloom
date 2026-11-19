"""The HTTP client a Shuttle gets as ``ctx.http``.

It is an ordinary :class:`httpx.AsyncClient`, with these guards built in:

- **SSRF guard.** Every connection resolves the host name once, refuses it
  unless *every* address is public (not private, loopback, link-local,
  multicast, reserved, unspecified, shared or otherwise special, including
  IPv4 addresses embedded in IPv4-mapped, 6to4, Teredo and NAT64 IPv6
  addresses), and then connects to the address it checked, so a second DNS
  answer (DNS rebinding) can never redirect the connection. Unix sockets are
  refused. Proxies from the environment are ignored.
- **Schemes.** Only ``http`` and ``https``, for the first request and every
  redirect (at most :data:`MAX_REDIRECTS`).
- **Rate limit.** Each request, redirects included, takes a token from the
  per-host bucket of the Shuttle's ``rate_limit``.
- **Response size.** At most :data:`MAX_RESPONSE_BYTES` (5 MB), enforced
  while streaming: on the bytes received and, for a body that is read
  (``stream=False``, the default), on the decoded bytes too, so a compressed
  response cannot expand past the limit. ``Accept-Encoding: identity`` is
  sent by default.
- **Identity and timeouts.** ``User-Agent: Nightloom/<version>
  (+https://github.com/TheRealNightmare/nightloom)`` and bounded timeouts.
"""

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpcore
import httpx

from nightloom_sdk.meta import ShuttleMeta
from nightloom_sdk.ratelimit import HostRateLimiter

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
PROJECT_URL = "https://github.com/TheRealNightmare/nightloom"
ALLOWED_SCHEMES = frozenset({"http", "https"})
# Seconds for connecting (DNS included) and for each read/write.
CONNECT_TIMEOUT_SECONDS = 10.0
# Seconds for one DNS lookup, whoever asks. The lookup runs in a thread
# (getaddrinfo), which keeps waiting after this; the run does not.
DNS_TIMEOUT_SECONDS = 10.0
IO_TIMEOUT_SECONDS = 30.0

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

_NAT64_PREFIXES = (
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("64:ff9b:1::/48"),
)


def nightloom_version() -> str:
    try:
        return version("nightloom-sdk")
    except PackageNotFoundError:  # pragma: no cover - only when run from a source tree
        return "0.0.0"


def user_agent() -> str:
    return f"Nightloom/{nightloom_version()} (+{PROJECT_URL})"


# --- errors ---------------------------------------------------------------------------


class DestinationNotAllowedError(httpx.TransportError):
    """The host resolves to an address a Shuttle may not reach (SSRF guard)."""


class SchemeNotAllowedError(httpx.UnsupportedProtocol):
    """A request or redirect to a scheme other than http or https."""


class ResponseTooLargeError(httpx.TransportError):
    """The response body exceeded the size limit."""


class NetworkNotDeclaredError(RuntimeError):
    """The Shuttle declared ``network=False`` but tried to use ``ctx.http``."""


# --- SSRF guard ---------------------------------------------------------------------------


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]  # the client's address
    for prefix in _NAT64_PREFIXES:
        if address in prefix:
            return ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF)
    return None


def is_public_address(address: IPAddress | str) -> bool:
    """Whether a Shuttle may connect to ``address``.

    Only globally reachable unicast addresses pass. An IPv6 address that
    embeds an IPv4 address (IPv4-mapped, 6to4, Teredo, NAT64) passes only if
    the embedded address does too.
    """
    if isinstance(address, str):
        address = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id:
            return False
        embedded = _embedded_ipv4(address)
        if embedded is not None and not is_public_address(embedded):
            return False
    return not (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
    )


async def system_resolver(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to (A and AAAA), in the resolver's order.

    Raises :class:`TimeoutError` after :data:`DNS_TIMEOUT_SECONDS`.
    """
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(DNS_TIMEOUT_SECONDS):
        infos = await loop.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolves, checks every address, then connects to a checked address.

    ``resolver`` and ``is_allowed`` are injectable for tests.
    """

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        is_allowed: Callable[[str], bool] = is_public_address,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._resolver = resolver
        self._is_allowed = is_allowed
        self._inner = inner or httpcore.AnyIOBackend()

    async def resolve(self, host: str, port: int, limit: float | None) -> list[str]:
        try:
            async with asyncio.timeout(limit):
                addresses = await self._resolver(host, port)
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(f"Resolving {host} timed out.") from exc
        except OSError as exc:
            raise httpcore.ConnectError(f"Could not resolve {host}.") from exc
        if not addresses:
            raise httpcore.ConnectError(f"Could not resolve {host}.")
        refused = [address for address in addresses if not self._safe(address)]
        if refused:
            raise DestinationNotAllowedError(
                f"{host} resolves to an address Shuttles may not reach "
                "(private, loopback, link-local, multicast or reserved)."
            )
        return addresses

    def _safe(self, address: str) -> bool:
        try:
            return self._is_allowed(address)
        except ValueError:
            return False

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await self.resolve(host, port, timeout)
        error: Exception | None = None
        for address in addresses:
            try:
                # The checked address, never the name: no second DNS lookup.
                return await self._inner.connect_tcp(
                    address, port, timeout, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        assert error is not None  # noqa: S101 - addresses is never empty here
        raise error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise DestinationNotAllowedError("Shuttles may not connect to Unix sockets.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


# --- transports -------------------------------------------------------------------------

_HTTPCORE_ERRORS = (
    httpcore.TimeoutException,
    httpcore.NetworkError,
    httpcore.ProtocolError,
    httpcore.ProxyError,
    httpcore.UnsupportedProtocol,
)


def _httpx_error(exc: Exception, request: httpx.Request) -> httpx.TransportError:
    """The httpx exception of the same name (httpx mirrors httpcore's hierarchy)."""
    kind = getattr(httpx, type(exc).__name__, httpx.TransportError)
    if not (isinstance(kind, type) and issubclass(kind, httpx.TransportError)):
        kind = httpx.TransportError
    return kind(str(exc), request=request)


class _CappedStream(httpx.AsyncByteStream):
    """Passes the body through and raises once it exceeds ``limit`` bytes."""

    def __init__(self, inner: Any, limit: int, request: httpx.Request) -> None:
        self._inner = inner
        self._limit = limit
        self._request = request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        received = 0
        try:
            async for chunk in self._inner:
                received += len(chunk)
                if received > self._limit:
                    raise ResponseTooLargeError(
                        f"The response is larger than {self._limit} bytes.", request=self._request
                    )
                yield chunk
        except _HTTPCORE_ERRORS as exc:
            raise _httpx_error(exc, self._request) from exc

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()


class GuardedHTTPTransport(httpx.AsyncBaseTransport):
    """An HTTP transport whose every connection goes through :class:`GuardedNetworkBackend`."""

    def __init__(self, backend: GuardedNetworkBackend | None = None) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=20,
            max_keepalive_connections=5,
            network_backend=backend or GuardedNetworkBackend(),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await self._pool.handle_async_request(core_request)
        except _HTTPCORE_ERRORS as exc:
            raise _httpx_error(exc, request) from exc
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CappedStream(response.stream, MAX_RESPONSE_BYTES, request),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class GuardTransport(httpx.AsyncBaseTransport):
    """Scheme check, per-host rate limit and response size cap around ``inner``.

    httpx sends every redirect through the transport again, so each hop is
    checked and rate limited like the first request.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        limiter: HostRateLimiter | None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._max = max_response_bytes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme not in ALLOWED_SCHEMES:
            raise SchemeNotAllowedError(
                "Shuttles may only make http and https requests.", request=request
            )
        if self._limiter is not None:
            await self._limiter.acquire(request.url.host)
        response = await self._inner.handle_async_request(request)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_CappedStream(response.stream, self._max, request),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


# --- client --------------------------------------------------------------------------------


class ShuttleHttpClient(httpx.AsyncClient):
    """An :class:`httpx.AsyncClient` that also caps decoded bodies it reads for the caller."""

    def __init__(self, *, max_response_bytes: int = MAX_RESPONSE_BYTES, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._max_response_bytes = max_response_bytes

    async def send(
        self, request: httpx.Request, *, stream: bool = False, **kwargs: Any
    ) -> httpx.Response:
        response = await super().send(request, stream=True, **kwargs)
        if stream:
            return response
        try:
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise ResponseTooLargeError(
                        f"The response is larger than {self._max_response_bytes} bytes.",
                        request=request,
                    )
                chunks.append(chunk)
            # What Response.aread() stores; reading again returns it.
            response._content = b"".join(chunks)
        finally:
            await response.aclose()
        return response


def create_http_client(
    meta: ShuttleMeta,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    limiter: HostRateLimiter | None = None,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> ShuttleHttpClient:
    """The guarded client for one Shuttle run.

    ``transport`` replaces the real network (tests); the scheme check, rate
    limit and size cap still apply on top of it.
    """
    if limiter is None and meta.rate_limit is not None:
        limiter = HostRateLimiter(meta.rate_limit)
    timeout = min(IO_TIMEOUT_SECONDS, float(meta.timeout_seconds))
    return ShuttleHttpClient(
        transport=GuardTransport(
            transport or GuardedHTTPTransport(),
            limiter=limiter,
            max_response_bytes=max_response_bytes,
        ),
        headers={"User-Agent": user_agent(), "Accept-Encoding": "identity"},
        timeout=httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT_SECONDS, timeout)),
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        trust_env=False,
        max_response_bytes=max_response_bytes,
    )


__all__ = [
    "DNS_TIMEOUT_SECONDS",
    "MAX_REDIRECTS",
    "MAX_RESPONSE_BYTES",
    "DestinationNotAllowedError",
    "GuardTransport",
    "GuardedHTTPTransport",
    "GuardedNetworkBackend",
    "NetworkNotDeclaredError",
    "ResponseTooLargeError",
    "SchemeNotAllowedError",
    "ShuttleHttpClient",
    "create_http_client",
    "is_public_address",
    "system_resolver",
    "user_agent",
]
