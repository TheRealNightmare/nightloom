"""Asking the Wayback Machine's CDX server, through the Shuttle's guarded HTTP client only.

:class:`CdxClient` wraps the ``httpx.AsyncClient`` the Shuttle gets as
``ctx.http`` (SSRF guard, per-host rate limit, 5 MB response cap) and adds
what the CDX server (https://web.archive.org/cdx/search/cdx) needs. It is a
free service run by a non-profit, and it is slow and often briefly
unavailable:

- Every request is a ``GET`` to :data:`CDX_URL` with ``output=json`` and an
  explicit field list (:data:`~nightloom_shuttle_wayback.filtering.FIELDS`).
  The answer is a JSON list of rows whose first row names the fields; an
  empty body or ``[]`` means no captures.
- **Per request** the read timeout is 60 seconds (a domain-wide listing can
  take most of that).
- **5xx, timeouts and network errors**: retried :data:`RETRIES` times after
  :data:`BACKOFF_SECONDS`, then :class:`SourceUnavailableError`.
- **429** (rate limited): waits for ``Retry-After`` (seconds or an HTTP
  date; :data:`DEFAULT_RETRY_AFTER` without one), at most
  :data:`MAX_RATE_LIMIT_WAITS` times and never longer than
  :data:`MAX_RETRY_AFTER_SECONDS`; a longer wait gives up at once.
- **Too large** (over the 5 MB cap): :class:`ResultTooLargeError` at once;
  the Shuttle asks again for fewer rows.
- Any other status, or an answer that is not a JSON list, is
  :class:`InvalidResponseError`. Errors never quote a response.
- No new try starts after ``deadline`` (a monotonic time).
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError
from nightloom_shuttle_wayback.filtering import FIELDS

HOST = "web.archive.org"
CDX_URL = f"https://{HOST}/cdx/search/cdx"
ARCHIVE_URL = f"https://{HOST}/web/"
RETRIES = 2
# Seconds to wait before the 1st and 2nd retry.
BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0)
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_RATE_LIMIT_WAITS = 2
MAX_RETRY_AFTER_SECONDS = 60.0
DEFAULT_RETRY_AFTER = 10.0
REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class CdxError(Exception):
    """Base class of the client's errors. Messages never quote a response."""


class SourceUnavailableError(CdxError):
    """The CDX server did not answer usefully after every try (5xx, 429, timeouts, network)."""


class ResultTooLargeError(CdxError):
    """The answer was larger than the Shuttle's response cap."""


class InvalidResponseError(CdxError):
    """The CDX server answered, but not with a list of rows (a 4xx, not JSON)."""


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


def listing_params(domain: str, limit: int) -> dict[str, str]:
    """Every archived address under ``domain`` (subdomains included), one row per address.

    ``collapse=urlkey`` keeps the first row of each archived address; the
    index is ordered by address, then time, so that row is its first capture.
    """
    return {
        "url": domain,
        "matchType": "domain",
        "output": "json",
        "fl": ",".join(FIELDS),
        "collapse": "urlkey",
        "limit": str(limit),
    }


def first_capture_params(domain: str) -> dict[str, str]:
    """The first capture of the domain's home page (an exact match, oldest first)."""
    return {"url": f"{domain}/", "output": "json", "fl": ",".join(FIELDS), "limit": "1"}


def last_capture_params(domain: str) -> dict[str, str]:
    """The latest capture of the domain's home page.

    ``limit=-1`` returns the last row; the CDX documentation's ``fastLatest``
    option finds it without reading every earlier capture.
    """
    return {
        "url": f"{domain}/",
        "output": "json",
        "fl": ",".join(FIELDS),
        "fastLatest": "true",
        "limit": "-1",
    }


class CdxClient:
    """CDX queries with retries. See the module docstring."""

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

    async def query(self, params: dict[str, str]) -> list[Any]:
        """The rows the CDX server returns for ``params`` (the field-name row included)."""
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
                    CDX_URL,
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT,
                )
            except ResponseTooLargeError as error:
                raise ResultTooLargeError("the response exceeded the size limit") from error
            except DestinationNotAllowedError as error:
                # The SSRF guard refused the archive's address: retrying cannot help.
                raise SourceUnavailableError("the archive resolved to a refused address") from error
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
            return self._rows(response)

    @staticmethod
    def _rows(response: httpx.Response) -> list[Any]:
        if response.status_code != httpx.codes.OK:
            raise InvalidResponseError(f"HTTP {response.status_code}")
        if not response.content.strip():
            return []
        try:
            rows = json.loads(response.content)
        except ValueError as error:
            raise InvalidResponseError("the answer is not JSON") from error
        if not isinstance(rows, list):
            raise InvalidResponseError("the answer is not a JSON list")
        return rows


__all__ = [
    "ARCHIVE_URL",
    "BACKOFF_SECONDS",
    "CDX_URL",
    "DEFAULT_RETRY_AFTER",
    "HOST",
    "MAX_RATE_LIMIT_WAITS",
    "MAX_RETRY_AFTER_SECONDS",
    "REQUEST_TIMEOUT",
    "RETRIES",
    "RETRY_STATUSES",
    "CdxClient",
    "CdxError",
    "InvalidResponseError",
    "ResultTooLargeError",
    "SourceUnavailableError",
    "first_capture_params",
    "last_capture_params",
    "listing_params",
    "retry_after_seconds",
]
