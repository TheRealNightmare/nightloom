"""Asking RDAP servers, through the Shuttle's guarded HTTP client only.

:class:`RdapClient` wraps the ``httpx.AsyncClient`` the Shuttle gets as
``ctx.http`` (SSRF guard, per-host rate limit, 5 MB response cap) and adds
what RDAP servers need:

- ``Accept: application/rdap+json`` (RFC 7480).
- **404** means the server has no such object: :meth:`RdapClient.get`
  returns ``None`` and the Shuttle says so in a job note.
- **429** (rate limited): waits for ``Retry-After`` (seconds or an HTTP
  date; :data:`DEFAULT_RETRY_AFTER` without one), at most
  :data:`MAX_RATE_LIMIT_WAITS` times and never longer than
  :data:`MAX_RETRY_AFTER_SECONDS`; a longer wait gives up at once.
- **5xx and network errors**: retried :data:`RETRIES` times after
  :data:`BACKOFF_SECONDS`, then :class:`SourceUnavailableError`.
- Any other status, or an answer that is not a JSON object, is
  :class:`InvalidResponseError`. Errors never quote a response.
- No new try starts after ``deadline`` (a monotonic time).
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError

ACCEPT = "application/rdap+json, application/json;q=0.8"
RETRIES = 2
# Seconds to wait before the 1st and 2nd retry.
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_RATE_LIMIT_WAITS = 2
MAX_RETRY_AFTER_SECONDS = 30.0
DEFAULT_RETRY_AFTER = 5.0
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class RdapError(Exception):
    """Base class of the client's errors. Messages never quote a response."""


class SourceUnavailableError(RdapError):
    """The server did not answer usefully after every try (5xx, 429, timeouts, network)."""


class InvalidResponseError(RdapError):
    """The server answered, but not with an RDAP object (a 4xx, not JSON, too large)."""


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    """``Retry-After`` as seconds from now: delta-seconds or an HTTP date; ``None`` if unusable."""
    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


class RdapClient:
    """RDAP GETs with retries. See the module docstring."""

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

    def _past_deadline(self) -> bool:
        return self._deadline is not None and self._clock() >= self._deadline

    async def get(self, url: str) -> Mapping[str, Any] | None:
        """The JSON object at ``url``; ``None`` when the server has no such object (404)."""
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
                    url, headers={"Accept": ACCEPT}, timeout=REQUEST_TIMEOUT
                )
            except ResponseTooLargeError as error:
                raise InvalidResponseError("the response exceeded the size limit") from error
            except DestinationNotAllowedError as error:
                # The SSRF guard refused the server's address: retrying cannot help.
                raise SourceUnavailableError("the server resolved to a refused address") from error
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
            return self._json(response)

    @staticmethod
    def _json(response: httpx.Response) -> Mapping[str, Any] | None:
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code != httpx.codes.OK:
            raise InvalidResponseError(f"HTTP {response.status_code}")
        try:
            data = json.loads(response.content)
        except ValueError as error:
            raise InvalidResponseError("the answer is not JSON") from error
        if not isinstance(data, dict):
            raise InvalidResponseError("the answer is not a JSON object")
        return data


__all__ = [
    "ACCEPT",
    "BACKOFF_SECONDS",
    "DEFAULT_RETRY_AFTER",
    "MAX_RATE_LIMIT_WAITS",
    "MAX_RETRY_AFTER_SECONDS",
    "RETRIES",
    "RETRY_STATUSES",
    "InvalidResponseError",
    "RdapClient",
    "RdapError",
    "SourceUnavailableError",
    "retry_after_seconds",
]
