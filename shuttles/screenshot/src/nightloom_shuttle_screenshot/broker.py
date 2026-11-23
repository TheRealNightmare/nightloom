"""The page's network: every request the browser makes is answered from here.

The browser service has no network of its own (it sits on an internal-only
Compose network). Each request the page makes is intercepted and handed to
:meth:`RequestBroker.answer`, which fetches it through the Shuttle's guarded
HTTP client (public addresses only, rate-limited per host, size-capped) or
refuses it. So everything the page loads went through the same checks as
any Shuttle request, and nothing else can be reached.

Refused: methods other than GET and HEAD, schemes other than http(s),
resource kinds a still picture does not need (video and audio, WebSockets,
event streams, pings), and anything past :data:`MAX_REQUESTS` requests or
:data:`MAX_TOTAL_BYTES` bytes. Redirects are followed here (at most
:data:`MAX_REDIRECTS`), each hop checked like the first: Chromium cannot be
handed a redirect for an intercepted request. The page's own address is
resolved first (:meth:`RequestBroker.open`), so the browser opens the final
page. Cookies are never sent, and ``Set-Cookie`` is never passed on.
"""

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from nightloom_sdk.http import DestinationNotAllowedError

MAX_REQUESTS = 150
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
ALLOWED_METHODS = frozenset({"GET", "HEAD"})
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# What a page needs to be drawn (Playwright's resource types).
ALLOWED_RESOURCES = frozenset(
    {"document", "stylesheet", "image", "font", "script", "xhr", "fetch", "other"}
)
# Request headers passed on from the browser; everything else is dropped.
FORWARDED_REQUEST_HEADERS = frozenset({"accept", "accept-language", "range"})
# Response headers passed back to the browser.
FORWARDED_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-language",
        "location",
        "content-security-policy",
        "x-frame-options",
        "access-control-allow-origin",
        "access-control-allow-headers",
        "access-control-allow-methods",
        "timing-allow-origin",
        "last-modified",
        "etag",
    }
)


@dataclass(frozen=True)
class Reply:
    """What the browser gets for one request."""

    status: int
    headers: dict[str, str]
    body: bytes


@dataclass
class RequestBroker:
    """Answers one page's requests through ``client``, within the limits above."""

    client: httpx.AsyncClient
    max_requests: int = MAX_REQUESTS
    max_total_bytes: int = MAX_TOTAL_BYTES
    requests: int = 0
    total_bytes: int = 0
    refused: int = 0
    failed: int = 0
    hosts: set[str] = field(default_factory=set)
    # Pages fetched by open() for the browser's coming request.
    _kept: dict[str, Reply] = field(default_factory=dict)

    async def open(self, url: str) -> str | None:
        """Follow ``url``'s redirects and keep the final page for the browser.

        Returns the final address, so the browser navigates straight to it
        (relative links then resolve against the right page), or ``None`` if
        it cannot be fetched. The kept reply answers the browser's own request
        for that address, so the page is fetched once.
        """
        found = await self._follow(url, method="GET", headers={})
        if found is None:
            return None
        final, reply = found
        self._kept[final] = reply
        return final

    async def answer(
        self, url: str, *, method: str, resource_type: str, headers: dict[str, str]
    ) -> Reply | None:
        """The reply to one request, or ``None`` to refuse it (the browser sees a failure).

        Redirects are followed here (each hop checked like the first): the
        browser cannot be handed a redirect for an intercepted request.
        """
        parts = urlsplit(url)
        if (
            method.upper() not in ALLOWED_METHODS
            or parts.scheme not in ("http", "https")
            or resource_type not in ALLOWED_RESOURCES
        ):
            self.refused += 1
            return None
        kept = self._kept.pop(url, None) if method.upper() == "GET" else None
        if kept is not None:
            return kept
        found = await self._follow(url, method=method.upper(), headers=headers)
        return None if found is None else found[1]

    async def _follow(
        self, url: str, *, method: str, headers: dict[str, str]
    ) -> tuple[str, Reply] | None:
        for _ in range(MAX_REDIRECTS + 1):
            reply = await self._fetch(url, method=method, headers=headers)
            if reply is None:
                return None
            location = reply.headers.get("location")
            if reply.status not in REDIRECT_STATUSES or not location:
                return url, reply
            url = urljoin(url, location)
            if urlsplit(url).scheme not in ("http", "https"):
                self.refused += 1
                return None
            if reply.status == 303:
                method = "GET"
        self.refused += 1
        return None

    async def _fetch(self, url: str, *, method: str, headers: dict[str, str]) -> Reply | None:
        if self.requests >= self.max_requests or self.total_bytes >= self.max_total_bytes:
            self.refused += 1
            return None
        self.requests += 1
        sent = {
            name: value
            for name, value in headers.items()
            if name.lower() in FORWARDED_REQUEST_HEADERS
        }
        try:
            async with self.client.stream(
                method, url, headers=sent, follow_redirects=False
            ) as response:
                body = bytearray()
                limit = min(MAX_RESPONSE_BYTES, self.max_total_bytes - self.total_bytes)
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > limit:
                        self.refused += 1
                        return None
        except DestinationNotAllowedError:
            self.refused += 1
            return None
        except httpx.HTTPError:
            self.failed += 1
            return None
        self.total_bytes += len(body)
        self.hosts.add((urlsplit(url).hostname or "").lower())
        return Reply(
            status=response.status_code,
            headers={
                name.lower(): value
                for name, value in response.headers.items()
                if name.lower() in FORWARDED_RESPONSE_HEADERS
            },
            body=bytes(body),
        )


__all__ = ["MAX_REQUESTS", "MAX_TOTAL_BYTES", "Reply", "RequestBroker"]
