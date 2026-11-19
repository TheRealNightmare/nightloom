"""Asking Shodan's InternetDB, through the Shuttle's guarded HTTP client only.

:class:`InternetDBClient` wraps the ``httpx.AsyncClient`` the Shuttle gets as
``ctx.http`` (SSRF guard, per-host rate limit, 5 MB response cap) and adds
what InternetDB needs:

- Every request is ``https://internetdb.shodan.io/<ipv4>``: the only host the
  Shuttle ever contacts. The address looked up is part of the path, never a
  destination.
- **404** is InternetDB's answer for an address it has no data on
  (``{"detail": "No information available"}``): :meth:`InternetDBClient.get`
  returns ``None``.
- **429** (rate limited): waits for ``Retry-After`` (seconds or an HTTP
  date; :data:`DEFAULT_RETRY_AFTER` without one), at most
  :data:`MAX_RATE_LIMIT_WAITS` times and never longer than
  :data:`MAX_RETRY_AFTER_SECONDS`; a longer wait gives up at once.
- **5xx and network errors**: retried :data:`RETRIES` times after
  :data:`BACKOFF_SECONDS`, then :class:`SourceUnavailableError`.
- Any other status, an answer that is not JSON or not a record is
  :class:`InvalidResponseError`. Errors never quote a response.
- No new try starts after ``deadline`` (a monotonic time).
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError
from nightloom_shuttle_internetdb.models import Record

HOST = "internetdb.shodan.io"
BASE_URL = f"https://{HOST}/"
RETRIES = 2
# Seconds to wait before the 1st and 2nd retry.
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_RATE_LIMIT_WAITS = 2
MAX_RETRY_AFTER_SECONDS = 30.0
DEFAULT_RETRY_AFTER = 5.0
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class InternetDBError(Exception):
    """Base class of the client's errors. Messages never quote a response."""


class SourceUnavailableError(InternetDBError):
    """InternetDB did not answer usefully after every try (5xx, 429, timeouts, network)."""


class InvalidResponseError(InternetDBError):
    """InternetDB answered, but not with a usable record (a 4xx, not JSON, too large)."""


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


def record_url(address: str) -> str:
    """The URL of InternetDB's record for ``address`` (always on :data:`HOST`)."""
    return f"{BASE_URL}{address}"


class InternetDBClient:
    """InternetDB lookups with retries. See the module docstring."""

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

    async def get(self, address: str) -> Record | None:
        """InternetDB's record for ``address``; ``None`` when it has none (404)."""
        url = record_url(address)
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
                    url, headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT
                )
            except ResponseTooLargeError as error:
                raise InvalidResponseError("the response exceeded the size limit") from error
            except DestinationNotAllowedError as error:
                # The SSRF guard refused InternetDB's address: retrying cannot help.
                raise SourceUnavailableError("InternetDB resolved to a refused address") from error
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
            return self._record(response)

    @staticmethod
    def _record(response: httpx.Response) -> Record:
        if response.status_code != httpx.codes.OK:
            raise InvalidResponseError(f"HTTP {response.status_code}")
        try:
            body = json.loads(response.content)
        except ValueError as error:
            raise InvalidResponseError("the answer is not JSON") from error
        try:
            return Record.model_validate(body)
        except ValidationError as error:
            raise InvalidResponseError("the answer is not an InternetDB record") from error


__all__ = [
    "BACKOFF_SECONDS",
    "BASE_URL",
    "DEFAULT_RETRY_AFTER",
    "HOST",
    "MAX_RATE_LIMIT_WAITS",
    "MAX_RETRY_AFTER_SECONDS",
    "RETRIES",
    "RETRY_STATUSES",
    "InternetDBClient",
    "InternetDBError",
    "InvalidResponseError",
    "SourceUnavailableError",
    "record_url",
    "retry_after_seconds",
]
