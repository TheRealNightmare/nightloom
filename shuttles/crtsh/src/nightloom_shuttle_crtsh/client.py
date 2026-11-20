"""Talking to crt.sh, through the Shuttle's guarded HTTP client only.

:class:`CrtShClient` wraps the ``httpx.AsyncClient`` the Shuttle gets as
``ctx.http`` (SSRF guard, per-host rate limit, 5 MB response cap). It adds
what crt.sh needs:

- **Retries.** crt.sh is a free, heavily used service that often answers
  502/503/504 or times out. A request that fails that way (or with 429, 500,
  or a network error) is retried up to :data:`RETRIES` times, waiting
  :data:`BACKOFF_SECONDS` between tries (on top of the rate limit), then
  raises :class:`SourceUnavailableError`.
- **Too large.** A search whose answer exceeds the response cap raises
  :class:`ResultTooLargeError` at once (retrying would not help); the
  Shuttle then narrows the query.
- **A deadline.** No new try starts after ``deadline`` (a monotonic time), so
  a struggling crt.sh cannot use up the job's whole timeout.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError

CRTSH_URL = "https://crt.sh/"
RETRIES = 3
# Seconds to wait before the 1st, 2nd and 3rd retry.
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0, 10.0)
DOWNLOAD_RETRIES = 1
# Statuses that mean "busy or broken right now": worth another try.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# crt.sh can take a long time to answer a search for a large domain.
SEARCH_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class CrtShError(Exception):
    """Base class of the client's errors. Messages never quote a response."""


class SourceUnavailableError(CrtShError):
    """crt.sh did not answer usefully after every try (5xx, 429, timeouts, network)."""


class ResultTooLargeError(CrtShError):
    """The answer was larger than the Shuttle's response cap."""


class InvalidResponseError(CrtShError):
    """crt.sh answered, but not with what was asked for (a 4xx, or JSON that is not a list)."""


class CrtShClient:
    """crt.sh searches and certificate downloads with retries. See the module docstring."""

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
        # Whether a try was skipped because the deadline had passed.
        self.out_of_time = False

    def extend_deadline(self, deadline: float) -> None:
        """Allow tries until ``deadline`` (a later phase of the run has its own budget)."""
        self._deadline = deadline

    def _past_deadline(self) -> bool:
        return self._deadline is not None and self._clock() >= self._deadline

    async def _get(
        self, params: dict[str, str], *, request_timeout: httpx.Timeout, retries: int
    ) -> httpx.Response:
        failure = "no try was made"
        for attempt in range(retries + 1):
            if attempt:
                self.retries += 1
                await self._sleep(self._backoff[min(attempt - 1, len(self._backoff) - 1)])
            if self._before_request is not None:
                self._before_request()
            if self._past_deadline():
                self.out_of_time = True
                raise SourceUnavailableError(f"out of time after: {failure}")
            self.requests += 1
            try:
                response = await self._http.get(CRTSH_URL, params=params, timeout=request_timeout)
            except ResponseTooLargeError as error:
                raise ResultTooLargeError("the response exceeded the size limit") from error
            except DestinationNotAllowedError as error:
                # The SSRF guard refused crt.sh's address: retrying cannot help.
                raise SourceUnavailableError("crt.sh resolved to a refused address") from error
            except httpx.HTTPError as error:
                failure = type(error).__name__
                continue
            if response.status_code in RETRY_STATUSES:
                failure = f"HTTP {response.status_code}"
                continue
            if response.status_code != httpx.codes.OK:
                raise InvalidResponseError(f"HTTP {response.status_code}")
            return response
        raise SourceUnavailableError(failure)

    async def search(self, query: str, *, exclude_expired: bool = False) -> list[Any]:
        """The JSON rows crt.sh returns for ``query`` (``example.org`` or ``%.example.org``)."""
        params = {"q": query, "output": "json"}
        if exclude_expired:
            params["exclude"] = "expired"
        response = await self._get(params, request_timeout=SEARCH_TIMEOUT, retries=RETRIES)
        if not response.content.strip():
            return []  # crt.sh sometimes answers an empty body for no results
        try:
            rows = json.loads(response.content)
        except ValueError as error:
            raise InvalidResponseError("the answer is not JSON") from error
        if not isinstance(rows, list):
            raise InvalidResponseError("the answer is not a JSON list")
        return rows

    async def download(self, crtsh_id: int) -> bytes:
        """The certificate with crt.sh ID ``crtsh_id`` (PEM)."""
        response = await self._get(
            {"d": str(crtsh_id)}, request_timeout=DOWNLOAD_TIMEOUT, retries=DOWNLOAD_RETRIES
        )
        return response.content


__all__ = [
    "BACKOFF_SECONDS",
    "CRTSH_URL",
    "DOWNLOAD_RETRIES",
    "RETRIES",
    "RETRY_STATUSES",
    "CrtShClient",
    "CrtShError",
    "InvalidResponseError",
    "ResultTooLargeError",
    "SourceUnavailableError",
]
