"""The Gravatar Shuttle: an email address's public Gravatar profile.

Gravatar (https://gravatar.com/) lets people publish a profile for an email
address. Its REST API (``GET https://api.gravatar.com/v3/profiles/<hash>``)
answers by the SHA-256 hash of the trimmed, lower-cased address, so the
address itself is never sent; no key is needed. It is the only host the
Shuttle contacts, through ``ctx.http``.

From a profile it yields:

- the profile as a SocialProfile named by its address (``profile_url``),
  with ``platform`` Gravatar, ``display_name``, ``job_title`` and
  ``company``, and ``HAS_PROFILE`` from the Email;
- each account its owner **verified** on Gravatar and did not hide (at most
  :data:`MAX_ACCOUNTS`) as a SocialProfile with ``platform`` (the service)
  and ``profile_url``, and ``LINKED_ACCOUNT`` from the Gravatar profile.

**People.** An email address and its profile are a person's: the Shuttle
has the ``person`` sensitivity, so it runs only in cases whose owner
allowed person-related entities, never in an auto-pivot. Never kept: the
photo (avatar), location, description, pronouns, pronunciation, or the
unverified links a profile lists.

Provenance: Gravatar ties a profile to an address its owner confirmed:
**A2** (0.9); a verified account is **B2** (0.8).
"""

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, normalize_text
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    FailureReason,
    Finding,
    Mode,
    RateLimit,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleFailedError,
    ShuttleMeta,
)
from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError

EMAIL = EntityType.EMAIL
PROFILE = EntityType.SOCIAL_PROFILE
HAS_PROFILE = RelationType.HAS_PROFILE
LINKED_ACCOUNT = RelationType.LINKED_ACCOUNT

HOST = "api.gravatar.com"
BASE_URL = f"https://{HOST}/v3/profiles/"
PLATFORM = "Gravatar"
MAX_ACCOUNTS = 20
RETRIES = 2
BACKOFF_SECONDS = (2.0, 5.0)
MAX_RETRY_AFTER_SECONDS = 30.0
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
VERIFIED = ("B2", 0.8)

USAGE_NOTE = (
    "An email address's Gravatar profile is a person's: this Shuttle runs only in cases that "
    "allow person-related entities, sends only the address's SHA-256 hash, and never keeps "
    "the photo, location or description."
)
NOTE_NONE = "Gravatar has no public profile for this address."
NOTE_SKIPPED = "Ignored {count:,} hidden, unverified or malformed account(s) on the profile."
NOTE_CAPPED = "The profile lists {count:,} verified accounts; added {limit:,}."

Sleep = Callable[[float], Any]


class SourceUnavailableError(Exception):
    """Gravatar did not answer usefully after every try."""


class InvalidResponseError(Exception):
    """Gravatar answered, but not with a usable profile."""


def profile_hash(address: str) -> str:
    """Gravatar's key for an address: SHA-256 of the trimmed, lower-cased address."""
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()


def _text(value: Any, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        text = normalize_text(value)
    except NormalizationError:
        return None
    return text if len(text) <= limit else None


def _url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return value.strip()


class GravatarShuttle(Shuttle):
    """An email address's public Gravatar profile. See the module docstring."""

    meta = ShuttleMeta(
        name="gravatar",
        version="1.0.0",
        title="Public profile of an email address (Gravatar)",
        description=(
            "Looks up an email address's public Gravatar profile (sending only the address's "
            "SHA-256 hash; no key): its display name, job title and company as a SocialProfile "
            "(HAS_PROFILE), and the accounts its owner verified on Gravatar (LINKED_ACCOUNT). "
            "Never keeps the photo, location or description. Person-related: runs only in "
            "cases that allow it."
        ),
        input_types=frozenset({EMAIL}),
        output_entity_types=frozenset({PROFILE}),
        output_relationship_types=frozenset({HAS_PROFILE, LINKED_ACCOUNT}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.PERSON,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=45,
        cache_ttl_seconds=24 * 3600,
        pivots=False,
        homepage="https://docs.gravatar.com/api/profiles/rest-api/",
        terms_url="https://wordpress.com/tos/",
        usage_note=USAGE_NOTE,
        default_admiralty="A2",
        default_confidence=0.9,
    )

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``http``, ``sleep`` and ``clock`` replace the real ones in tests."""
        self._http = http
        self._sleep = sleep
        self._clock = clock

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        try:
            ctx.progress("Asking Gravatar for the address's profile.")
            body = await self._get(ctx, profile_hash(entity.value))
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except SourceUnavailableError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        if body is None:
            ctx.note(NOTE_NONE)
            return
        url = _url(body.get("profile_url"))
        if url is None:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE)
        values = {
            "platform": PLATFORM,
            "profile_url": url,
            "display_name": _text(body.get("display_name")),
            "job_title": _text(body.get("job_title")),
            "company": _text(body.get("company")),
        }
        profile = EntityRef(type=PROFILE, value=url)
        yield EntityFinding(
            type=PROFILE,
            value=url,
            properties={key: value for key, value in values.items() if value is not None},
        )
        yield RelationshipFinding(type=HAS_PROFILE, source=entity.ref, target=profile)

        accounts: list[tuple[str, str]] = []
        skipped = 0
        items = body.get("verified_accounts")
        for item in items if isinstance(items, list) else []:
            data = item if isinstance(item, dict) else {}
            account_url = _url(data.get("url"))
            service = _text(data.get("service_label") or data.get("service_type"), 60)
            if data.get("is_hidden") is True or account_url is None or service is None:
                skipped += 1
                continue
            if account_url not in (known for known, _ in accounts) and account_url != url:
                accounts.append((account_url, service))
        if skipped:
            ctx.note(NOTE_SKIPPED.format(count=skipped))
        if len(accounts) > MAX_ACCOUNTS:
            ctx.note(NOTE_CAPPED.format(count=len(accounts), limit=MAX_ACCOUNTS))
        for account_url, service in accounts[:MAX_ACCOUNTS]:
            yield EntityFinding(
                type=PROFILE,
                value=account_url,
                properties={"platform": service, "profile_url": account_url},
                admiralty=VERIFIED[0],
                confidence=VERIFIED[1],
            )
            yield RelationshipFinding(
                type=LINKED_ACCOUNT,
                source=profile,
                target=EntityRef(type=PROFILE, value=account_url),
                admiralty=VERIFIED[0],
                confidence=VERIFIED[1],
                note="verified on Gravatar",
            )

    async def _get(self, ctx: ShuttleContext, key: str) -> dict[str, Any] | None:
        http = self._http or ctx.http
        deadline = self._clock() + 35
        retries = waits = 0
        while True:
            ctx.raise_if_cancelled()
            if self._clock() >= deadline:
                raise SourceUnavailableError("out of time")
            try:
                response = await http.get(
                    f"{BASE_URL}{key}",
                    headers={"Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT,
                )
            except ResponseTooLargeError as error:
                raise InvalidResponseError("too large") from error
            except DestinationNotAllowedError as error:
                raise SourceUnavailableError("refused address") from error
            except httpx.HTTPError:
                response = None
            if response is not None and response.status_code == 429:
                text = response.headers.get("Retry-After", "").strip()
                wait = float(text) if text.isdigit() else 5.0
                if waits >= 2 or wait > MAX_RETRY_AFTER_SECONDS:
                    raise SourceUnavailableError("rate limited")
                waits += 1
                await self._sleep(wait)
                continue
            if response is None or response.status_code >= 500:
                if retries >= RETRIES:
                    raise SourceUnavailableError("no answer")
                await self._sleep(BACKOFF_SECONDS[retries])
                retries += 1
                continue
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise InvalidResponseError(f"HTTP {response.status_code}")
            try:
                body = json.loads(response.content)
            except ValueError as error:
                raise InvalidResponseError("not JSON") from error
            if not isinstance(body, dict):
                raise InvalidResponseError("not a profile")
            return body


__all__ = ["BASE_URL", "HOST", "MAX_ACCOUNTS", "VERIFIED", "GravatarShuttle", "profile_hash"]
