"""Asking the RIPEstat Data API, through the Shuttle's guarded HTTP client only.

:class:`RipeStatClient` wraps the ``httpx.AsyncClient`` the Shuttle gets as
``ctx.http`` (SSRF guard, per-host rate limit, 5 MB response cap) and adds
what RIPEstat needs:

- Every request is ``https://stat.ripe.net/data/<endpoint>/data.json`` with
  ``resource=<r>`` and ``sourceapp=nightloom``. RIPEstat asks every client
  to name itself with ``sourceapp`` so it can contact heavy users instead of
  blocking them.
- The answer is an envelope (``status``, ``status_code``, ``messages``,
  ``data``), checked with :class:`~nightloom_shuttle_ripestat.models.Envelope`:
  anything but ``status: "ok"`` with a ``data`` object is
  :class:`InvalidResponseError`.
- **429** (rate limited): waits for ``Retry-After`` (seconds or an HTTP
  date; :data:`DEFAULT_RETRY_AFTER` without one), at most
  :data:`MAX_RATE_LIMIT_WAITS` times and never longer than
  :data:`MAX_RETRY_AFTER_SECONDS`; a longer wait gives up at once.
- **5xx and network errors**: retried :data:`RETRIES` times after
  :data:`BACKOFF_SECONDS`, then :class:`SourceUnavailableError`.
- Any other status (RIPEstat answers ``400`` with ``status: "error"`` for a
  resource it does not understand) is :class:`InvalidResponseError`. Errors
  never quote a response.
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
from pydantic import ValidationError

from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError
from nightloom_shuttle_ripestat.models import Envelope

BASE_URL = "https://stat.ripe.net/data/"
SOURCE_APP = "nightloom"
RETRIES = 2
# Seconds to wait before the 1st and 2nd retry.
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_RATE_LIMIT_WAITS = 2
MAX_RETRY_AFTER_SECONDS = 30.0
DEFAULT_RETRY_AFTER = 5.0
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# announced-prefixes for a large network (thousands of prefixes) takes RIPEstat
# well over 20 seconds to compute.
SLOW_REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class RipeStatError(Exception):
    """Base class of the client's errors. Messages never quote a response."""


class SourceUnavailableError(RipeStatError):
    """RIPEstat did not answer usefully after every try (5xx, 429, timeouts, network)."""


class InvalidResponseError(RipeStatError):
    """RIPEstat answered, but not with usable data (a 4xx, not JSON, not ``ok``, too large)."""


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


def data_url(endpoint: str) -> str:
    """The URL of a RIPEstat data call (``network-info`` → ``.../network-info/data.json``)."""
    return f"{BASE_URL}{endpoint}/data.json"


class RipeStatClient:
    """RIPEstat data calls with retries. See the module docstring."""

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

    async def get(
        self, endpoint: str, resource: str, *, read_timeout: httpx.Timeout = REQUEST_TIMEOUT
    ) -> Mapping[str, Any]:
        """The ``data`` object of the data call ``endpoint`` for ``resource``."""
        url = data_url(endpoint)
        params = {"resource": resource, "sourceapp": SOURCE_APP}
        failure = "no try was made"
        retries = waits = 0
        while True:
            if self._before_request is not None:
                self._before_request()
            if self._past_deadline():
                raise SourceUnavailableError(f"out of time after: {failure}")
            self.requests += 1
            try:
                response = await self._http.get(url, params=params, timeout=read_timeout)
            except ResponseTooLargeError as error:
                raise InvalidResponseError("the response exceeded the size limit") from error
            except DestinationNotAllowedError as error:
                # The SSRF guard refused RIPEstat's address: retrying cannot help.
                raise SourceUnavailableError("RIPEstat resolved to a refused address") from error
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
            return self._data(response)

    @staticmethod
    def _data(response: httpx.Response) -> Mapping[str, Any]:
        if response.status_code != httpx.codes.OK:
            raise InvalidResponseError(f"HTTP {response.status_code}")
        try:
            body = json.loads(response.content)
        except ValueError as error:
            raise InvalidResponseError("the answer is not JSON") from error
        try:
            envelope = Envelope.model_validate(body)
        except ValidationError as error:
            raise InvalidResponseError("the answer is not a RIPEstat envelope") from error
        if envelope.status != "ok":
            raise InvalidResponseError("RIPEstat reported a status other than ok")
        if envelope.data is None:
            raise InvalidResponseError("the answer has no data")
        return envelope.data


__all__ = [
    "BACKOFF_SECONDS",
    "BASE_URL",
    "DEFAULT_RETRY_AFTER",
    "MAX_RATE_LIMIT_WAITS",
    "MAX_RETRY_AFTER_SECONDS",
    "RETRIES",
    "RETRY_STATUSES",
    "SLOW_REQUEST_TIMEOUT",
    "SOURCE_APP",
    "InvalidResponseError",
    "RipeStatClient",
    "RipeStatError",
    "SourceUnavailableError",
    "data_url",
    "retry_after_seconds",
]
