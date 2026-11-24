"""Asking the GLEIF API, through the Shuttle's guarded HTTP client only.

:class:`GleifClient` wraps the ``httpx.AsyncClient`` the Shuttle gets as
``ctx.http`` (SSRF guard, per-host rate limit, 5 MB response cap) and adds
what the GLEIF API (a JSON:API service, no key) needs:

- Every request goes to ``https://api.gleif.org/api/v1/``: the only host the
  Shuttle ever contacts. Names and LEIs are query parameters or path
  segments (an LEI is checked to be 20 letters and digits first), never a
  destination.
- **404** means "no such record" (an unknown LEI, or no reported parent):
  the methods return ``None`` or an empty page.
- **429** (rate limited; GLEIF allows 60 requests a minute): waits for
  ``Retry-After`` (seconds or an HTTP date; :data:`DEFAULT_RETRY_AFTER`
  without one), at most :data:`MAX_RATE_LIMIT_WAITS` times and never longer
  than :data:`MAX_RETRY_AFTER_SECONDS`; a longer wait gives up at once.
- **5xx and network errors**: retried :data:`RETRIES` times after
  :data:`BACKOFF_SECONDS`, then :class:`SourceUnavailableError`.
- Any other status, an answer that is not JSON or not JSON:API data is
  :class:`InvalidResponseError`. Errors never quote a response.
- No new try starts after ``deadline`` (a monotonic time).
"""

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError

HOST = "api.gleif.org"
BASE_URL = f"https://{HOST}/api/v1/"
ACCEPT = "application/vnd.api+json"
RETRIES = 2
# Seconds to wait before the 1st and 2nd retry.
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_RATE_LIMIT_WAITS = 2
MAX_RETRY_AFTER_SECONDS = 30.0
DEFAULT_RETRY_AFTER = 5.0
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
# The largest page GLEIF serves.
MAX_PAGE_SIZE = 200

LEI_SHAPE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class GleifError(Exception):
    """Base class of the client's errors. Messages never quote a response."""


class SourceUnavailableError(GleifError):
    """GLEIF did not answer usefully after every try (5xx, 429, timeouts, network)."""


class InvalidResponseError(GleifError):
    """GLEIF answered, but not with usable data (a 4xx, not JSON, too large)."""


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


def is_valid_lei(value: str) -> bool:
    """Whether ``value`` is a well-formed LEI with correct check digits (ISO 17442, ISO 7064
    MOD 97-10: letters count as 10 to 35, and the whole number modulo 97 is 1)."""
    if not LEI_SHAPE.fullmatch(value):
        return False
    digits = "".join(str(int(character, 36)) for character in value)
    return int(digits) % 97 == 1


@dataclass(frozen=True)
class Page:
    """A page of LEI records: the raw JSON:API resources and how many there are in all."""

    records: list[Any]
    total: int


class GleifClient:
    """GLEIF lookups with retries. See the module docstring."""

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

    # --- the lookups ---

    async def search(self, legal_name: str, *, size: int) -> Page:
        """LEI records whose legal name is ``legal_name`` (GLEIF's own matching)."""
        body = await self._get(
            "lei-records",
            {"filter[entity.legalName]": legal_name, "page[size]": str(_page_size(size))},
        )
        return Page([], 0) if body is None else _page(body)

    async def record(self, lei: str) -> Any | None:
        """The LEI record of ``lei``; ``None`` if GLEIF has none."""
        body = await self._get(f"lei-records/{_checked(lei)}")
        return None if body is None else _resource(body)

    async def parent(self, lei: str, *, ultimate: bool) -> Any | None:
        """The direct (or ultimate) parent's LEI record; ``None`` if none is reported."""
        kind = "ultimate-parent" if ultimate else "direct-parent"
        body = await self._get(f"lei-records/{_checked(lei)}/{kind}")
        return None if body is None else _resource(body)

    async def children(self, lei: str, *, size: int) -> Page:
        """The first page of the direct children's LEI records."""
        body = await self._get(
            f"lei-records/{_checked(lei)}/direct-children", {"page[size]": str(_page_size(size))}
        )
        return Page([], 0) if body is None else _page(body)

    # --- one GET with retries ---

    def _past_deadline(self) -> bool:
        return self._deadline is not None and self._clock() >= self._deadline

    async def _get(self, path: str, params: Mapping[str, str] | None = None) -> Any | None:
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
                    params=dict(params or {}),
                    headers={"Accept": ACCEPT},
                    timeout=REQUEST_TIMEOUT,
                )
            except ResponseTooLargeError as error:
                raise InvalidResponseError("the response exceeded the size limit") from error
            except DestinationNotAllowedError as error:
                # The SSRF guard refused GLEIF's address: retrying cannot help.
                raise SourceUnavailableError("GLEIF resolved to a refused address") from error
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
            if response.status_code != httpx.codes.OK:
                raise InvalidResponseError(f"HTTP {response.status_code}")
            try:
                return json.loads(response.content)
            except ValueError as error:
                raise InvalidResponseError("the answer is not JSON") from error


def _page_size(size: int) -> int:
    return max(1, min(size, MAX_PAGE_SIZE))


def _checked(lei: str) -> str:
    """``lei`` if it is safe to put in a path (20 letters and digits); else a ValueError."""
    if not LEI_SHAPE.fullmatch(lei):
        raise ValueError("not an LEI")
    return lei


def _resource(body: Any) -> Any:
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        raise InvalidResponseError("the answer is not a JSON:API resource")
    return body["data"]


def _page(body: Any) -> Page:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise InvalidResponseError("the answer is not a JSON:API list")
    records: list[Any] = body["data"]
    total = len(records)
    meta = body.get("meta")
    pagination = meta.get("pagination") if isinstance(meta, dict) else None
    if isinstance(pagination, dict):
        reported = pagination.get("total")
        if isinstance(reported, int) and not isinstance(reported, bool) and reported >= total:
            total = reported
    return Page(records, total)


__all__ = [
    "ACCEPT",
    "BACKOFF_SECONDS",
    "BASE_URL",
    "DEFAULT_RETRY_AFTER",
    "HOST",
    "MAX_PAGE_SIZE",
    "MAX_RATE_LIMIT_WAITS",
    "MAX_RETRY_AFTER_SECONDS",
    "RETRIES",
    "RETRY_STATUSES",
    "GleifClient",
    "GleifError",
    "InvalidResponseError",
    "Page",
    "SourceUnavailableError",
    "is_valid_lei",
    "retry_after_seconds",
]
