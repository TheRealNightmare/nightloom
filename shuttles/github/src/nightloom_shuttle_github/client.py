"""Asking the GitHub REST API, through the Shuttle's guarded HTTP client only.

:class:`GithubClient` wraps the ``httpx.AsyncClient`` the Shuttle gets as
``ctx.http`` (SSRF guard, per-host rate limit, 5 MB response cap and
Nightloom's ``User-Agent``, which GitHub requires) and adds what the API
needs:

- Every request goes to ``https://api.github.com/`` with
  ``Accept: application/vnd.github+json`` and the API version header. An
  optional token (the operator's ``NIGHTLOOM_GITHUB_TOKEN``) goes in
  ``Authorization: Bearer``; it is never logged or put in an error.
- **404** means "no such account or repository": ``None``.
- **Rate limits.** GitHub allows 60 requests an hour without a token. A
  403 or 429 with ``x-ratelimit-remaining: 0`` (the primary limit) waits
  for ``x-ratelimit-reset`` if that is at most
  :data:`MAX_RETRY_AFTER_SECONDS` away, and is otherwise
  :class:`RateLimitedError` at once. A 403 or 429 with ``Retry-After`` (a
  secondary limit) waits for it the same way, at most
  :data:`MAX_RATE_LIMIT_WAITS` times.
- **401** (a token GitHub refuses) is :class:`TokenRefusedError`; any other
  4xx, or an answer that is not JSON, is :class:`InvalidResponseError`.
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

import httpx

from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError

HOST = "api.github.com"
BASE_URL = f"https://{HOST}/"
ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"
RETRIES = 2
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_RATE_LIMIT_WAITS = 2
MAX_RETRY_AFTER_SECONDS = 30.0
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]
WallClock = Callable[[], float]


class GithubError(Exception):
    """Base class of the client's errors. Messages never quote a response or the token."""


class SourceUnavailableError(GithubError):
    """GitHub did not answer usefully after every try (5xx, timeouts, network)."""


class RateLimitedError(SourceUnavailableError):
    """GitHub's rate limit is used up for longer than the Shuttle may wait."""


class InvalidResponseError(GithubError):
    """GitHub answered, but not with usable data (a 4xx, not JSON, too large)."""


class TokenRefusedError(GithubError):
    """GitHub refused the token (HTTP 401)."""


def _seconds(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    return float(text) if text.isdigit() else None


class GithubClient:
    """GitHub lookups with retries. See the module docstring."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        token: str | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        wall_clock: WallClock = time.time,
        deadline: float | None = None,
        before_request: Callable[[], None] | None = None,
        backoff: Sequence[float] = BACKOFF_SECONDS,
    ) -> None:
        """``wall_clock`` is the Unix time (``x-ratelimit-reset`` is one)."""
        self._http = http
        self._token = token
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock
        self._deadline = deadline
        self._before_request = before_request
        self._backoff = tuple(backoff)
        self.requests = 0
        self.retries = 0
        self.rate_limited = 0

    def _past_deadline(self) -> bool:
        return self._deadline is not None and self._clock() >= self._deadline

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": ACCEPT, "X-GitHub-Api-Version": API_VERSION}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _rate_limit_wait(self, response: httpx.Response) -> float | None:
        """Seconds to wait if ``response`` is a rate limit; ``None`` if it is not one."""
        if response.status_code not in (403, 429):
            return None
        retry_after = _seconds(response.headers.get("Retry-After"))
        if retry_after is not None:
            return retry_after
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = _seconds(response.headers.get("x-ratelimit-reset"))
            return float("inf") if reset is None else max(0.0, reset - self._wall_clock())
        return 60.0 if response.status_code == 429 else None

    async def get(self, path: str, params: Mapping[str, str] | None = None) -> Any | None:
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
                    url, params=dict(params or {}), headers=self._headers(), timeout=REQUEST_TIMEOUT
                )
            except ResponseTooLargeError as error:
                raise InvalidResponseError("the response exceeded the size limit") from error
            except DestinationNotAllowedError as error:
                raise SourceUnavailableError("GitHub resolved to a refused address") from error
            except httpx.HTTPError as error:
                failure = type(error).__name__
                response = None
            if response is not None:
                wait = self._rate_limit_wait(response)
                if wait is not None:
                    if waits >= MAX_RATE_LIMIT_WAITS or wait > MAX_RETRY_AFTER_SECONDS:
                        raise RateLimitedError(f"HTTP {response.status_code}: rate limited")
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
                raise TokenRefusedError("HTTP 401")
            if response.status_code != httpx.codes.OK:
                raise InvalidResponseError(f"HTTP {response.status_code}")
            try:
                return json.loads(response.content)
            except ValueError as error:
                raise InvalidResponseError("the answer is not JSON") from error


__all__ = [
    "ACCEPT",
    "API_VERSION",
    "BASE_URL",
    "HOST",
    "MAX_RETRY_AFTER_SECONDS",
    "GithubClient",
    "GithubError",
    "InvalidResponseError",
    "RateLimitedError",
    "SourceUnavailableError",
    "TokenRefusedError",
]
