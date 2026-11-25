"""Asking Have I Been Pwned (API v3), through the Shuttle's guarded HTTP client only.

:class:`HibpClient` wraps the ``httpx.AsyncClient`` the Shuttle gets as
``ctx.http`` (SSRF guard, per-host rate limit, 5 MB response cap and
Nightloom's ``User-Agent``, which HIBP requires) and adds what the API needs:

- Every request goes to ``https://haveibeenpwned.com/api/v3/``: the only
  host either Shuttle contacts. A domain is a query parameter; an email
  address is one percent-encoded path segment.
- :meth:`HibpClient.site_breaches` asks ``breaches?domain=`` (free, no key).
  :meth:`HibpClient.account_breaches` asks ``breachedaccount/<address>``
  with the operator's key in the ``hibp-api-key`` header (a paid
  subscription); the key is never logged or put in an error.
- **404** from ``breachedaccount`` means "in no breach": an empty list.
- **401** (key refused) is :class:`KeyRefusedError`; **403** (HIBP refused
  the client) and any other 4xx, or an answer that is not a JSON list, is
  :class:`InvalidResponseError`.
- **429**: waits for ``Retry-After`` (HIBP sends seconds;
  :data:`DEFAULT_RETRY_AFTER` without one), at most
  :data:`MAX_RATE_LIMIT_WAITS` times and never longer than
  :data:`MAX_RETRY_AFTER_SECONDS`; a longer wait gives up at once.
- **5xx and network errors**: retried :data:`RETRIES` times after
  :data:`BACKOFF_SECONDS`, then :class:`SourceUnavailableError`.
- No new try starts after ``deadline`` (a monotonic time). Errors never
  quote a response.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError

HOST = "haveibeenpwned.com"
BASE_URL = f"https://{HOST}/api/v3/"
KEY_HEADER = "hibp-api-key"
RETRIES = 2
# Seconds to wait before the 1st and 2nd retry.
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_RATE_LIMIT_WAITS = 2
MAX_RETRY_AFTER_SECONDS = 30.0
DEFAULT_RETRY_AFTER = 6.0
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class HibpError(Exception):
    """Base class of the client's errors. Messages never quote a response or the key."""


class SourceUnavailableError(HibpError):
    """HIBP did not answer usefully after every try (5xx, 429, timeouts, network)."""


class InvalidResponseError(HibpError):
    """HIBP answered, but not with usable data (a 4xx, not JSON, too large)."""


class KeyRefusedError(HibpError):
    """HIBP refused the API key (HTTP 401)."""


def retry_after_seconds(value: str | None) -> float | None:
    """``Retry-After`` as seconds (HIBP sends whole seconds); ``None`` if unusable."""
    if value is None:
        return None
    text = value.strip()
    return float(text) if text.isdigit() else None


class HibpClient:
    """HIBP lookups with retries. See the module docstring."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        deadline: float | None = None,
        before_request: Callable[[], None] | None = None,
        backoff: Sequence[float] = BACKOFF_SECONDS,
    ) -> None:
        """``before_request`` runs before every try (the Shuttle's cancellation check)."""
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self._deadline = deadline
        self._before_request = before_request
        self._backoff = tuple(backoff)
        self.requests = 0
        self.retries = 0
        self.rate_limited = 0

    async def site_breaches(self, domain: str) -> list[Any]:
        """The breaches of the site at ``domain`` (HIBP's public breach list)."""
        body = await self._get("breaches", params={"domain": domain})
        return [] if body is None else _list(body)

    async def account_breaches(self, address: str, *, key: str) -> list[Any]:
        """The breaches ``address`` is in, in full (not truncated); ``[]`` if none."""
        body = await self._get(
            f"breachedaccount/{quote(address, safe='')}",
            params={"truncateResponse": "false"},
            headers={KEY_HEADER: key},
        )
        return [] if body is None else _list(body)

    def _past_deadline(self) -> bool:
        return self._deadline is not None and self._clock() >= self._deadline

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> Any | None:
        """The JSON body of ``BASE_URL + path``; ``None`` for a 404."""
        url = f"{BASE_URL}{path}"
        failure = "no try was made"
        retries = waits = 0
        while True:
            if self._before_request is not None:
                self._before_request()
            if self._past_deadline():
                raise SourceUnavailableError(f"out of time after: {failure}")
            self.requests += 1
            try:
                response = await self._http.get(
                    url,
                    params=dict(params),
                    headers={"Accept": "application/json", **(headers or {})},
                    timeout=REQUEST_TIMEOUT,
                )
            except ResponseTooLargeError as error:
                raise InvalidResponseError("the response exceeded the size limit") from error
            except DestinationNotAllowedError as error:
                raise SourceUnavailableError("HIBP resolved to a refused address") from error
            except httpx.HTTPError as error:
                failure = type(error).__name__
                response = None
            if response is not None and response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                failure = "HTTP 429"
                wait = retry_after_seconds(response.headers.get("Retry-After"))
                wait = DEFAULT_RETRY_AFTER if wait is None else wait
                if waits >= MAX_RATE_LIMIT_WAITS or wait > MAX_RETRY_AFTER_SECONDS:
                    raise SourceUnavailableError(failure)
                waits += 1
                self.rate_limited += 1
                await self._sleep(wait)
                continue
            if response is None or response.status_code in RETRY_STATUSES:
                if response is not None:
                    failure = f"HTTP {response.status_code}"
                if retries >= RETRIES:
                    raise SourceUnavailableError(failure)
                await self._sleep(self._backoff[min(retries, len(self._backoff) - 1)])
                retries += 1
                self.retries += 1
                continue
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            if response.status_code == httpx.codes.UNAUTHORIZED:
                raise KeyRefusedError("HTTP 401")
            if response.status_code != httpx.codes.OK:
                raise InvalidResponseError(f"HTTP {response.status_code}")
            try:
                return json.loads(response.content)
            except ValueError as error:
                raise InvalidResponseError("the answer is not JSON") from error


def _list(body: Any) -> list[Any]:
    if not isinstance(body, list):
        raise InvalidResponseError("the answer is not a list of breaches")
    return body


__all__ = [
    "BACKOFF_SECONDS",
    "BASE_URL",
    "DEFAULT_RETRY_AFTER",
    "HOST",
    "KEY_HEADER",
    "MAX_RATE_LIMIT_WAITS",
    "MAX_RETRY_AFTER_SECONDS",
    "RETRIES",
    "HibpClient",
    "HibpError",
    "InvalidResponseError",
    "KeyRefusedError",
    "SourceUnavailableError",
    "retry_after_seconds",
]
