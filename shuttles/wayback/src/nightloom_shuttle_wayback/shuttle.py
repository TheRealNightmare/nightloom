"""The Wayback Shuttle: a domain's historical URLs from the Internet Archive.

The Wayback Machine (https://web.archive.org) has archived the public web
since 1996. Its CDX server lists what it captured under a domain. For the
input domain the Shuttle asks it, through ``ctx.http`` only
(:mod:`nightloom_shuttle_wayback.client`), three things: the first and the
latest capture of the domain's home page (exact-match queries, which the
archive answers quickly) and every archived address under the domain,
subdomains included (``matchType=domain``, one row per address with
``collapse=urlkey``, at most :data:`LISTING_LIMIT` rows). It never contacts
the domain itself, which is why it is passive. It yields:

- the input Domain again, with ``wayback_first_capture``,
  ``wayback_last_capture`` and ``wayback_url_count``;
- each subdomain that hosts a stored URL as a Domain with ``HAS_SUBDOMAIN``
  from the input (at most :data:`MAX_SUBDOMAINS`);
- each archived page as a URL with ``HOSTS`` from its own host (the input
  or the subdomain), at most :data:`MAX_URLS`, most recently captured
  first, with ``first_captured``, ``capture_count``, ``last_status``,
  ``mimetype``, ``archive_url``, ``query_params`` and, for the home page,
  ``last_captured``.

What is kept, and in what form, is decided by
:mod:`nightloom_shuttle_wayback.filtering`: only the domain and names under
it, no static assets, and never a query string, fragment or email address:
query values and paths with ``@`` can hold tokens and personal data, so
only the query parameters' names are stored.

Provenance: **B2** (confidence 0.8). The archive faithfully records what it
captured (usually reliable, probably true), but a page may differ today or
be gone.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, registrable_domain
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    FailureReason,
    Finding,
    InputRefusedError,
    Mode,
    RateLimit,
    RefusalReason,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleFailedError,
    ShuttleMeta,
)
from nightloom_shuttle_wayback.client import (
    ARCHIVE_URL,
    CdxClient,
    Clock,
    InvalidResponseError,
    ResultTooLargeError,
    Sleep,
    SourceUnavailableError,
    first_capture_params,
    last_capture_params,
    listing_params,
)
from nightloom_shuttle_wayback.filtering import (
    DROP_LABELS,
    ArchivedUrl,
    Capture,
    Collected,
    collect,
    parse_rows,
    select,
)

D = EntityType.DOMAIN
U = EntityType.URL

MAX_URLS = 2_000
# 1 + 2 x MAX_SUBDOMAINS + 2 x MAX_URLS = 4,901 findings at most, under the
# worker's limit of 5,000.
MAX_SUBDOMAINS = 450
# Rows asked for in the domain-wide listing, and again after an answer over
# the 5 MB response cap.
LISTING_LIMIT = 10_000
SMALLER_LISTING_LIMIT = 2_500
TIMEOUT_SECONDS = 300
# No new request starts this long after the run began.
REQUEST_BUDGET_SECONDS = 230

ADMIRALTY, CONFIDENCE = "B2", 0.8

USAGE_NOTE = (
    "Data from the Internet Archive. Please keep requests modest; the archive is a "
    "non-profit service."
)

NOTE_NO_CAPTURES = "The Wayback Machine has no captures of this domain."
NOTE_DROPPED = "Dropped {total:,} archived address(es): {details}."
NOTE_STRIPPED = (
    "Removed the query string or fragment from {count:,} archived address(es); "
    "only parameter names were kept."
)
NOTE_MALFORMED = "Ignored {count:,} malformed row(s) in the archive's answer."
NOTE_LISTING_LIMIT = (
    "The archive lists more than {limit:,} addresses under this domain; only the first "
    "{limit:,} (in the archive's address order) were read."
)
NOTE_SMALLER_LISTING = "The archive's answer was too large; asked again for fewer addresses."
NOTE_URLS_CAPPED = "Found {found:,} URLs; kept the {kept:,} most recently captured (the limit)."
NOTE_SUBDOMAINS_CAPPED = (
    "Left out {count:,} URL(s) on further subdomains after {limit:,} subdomains (the limit)."
)


def _timestamp(capture_time: Any) -> str:
    return f"{capture_time:%Y%m%d%H%M%S}"


def archive_url(entry: ArchivedUrl) -> str:
    """The Wayback Machine's copy of the stored (stripped) URL, at its latest capture seen."""
    return f"{ARCHIVE_URL}{_timestamp(entry.latest)}/{entry.url}"


def url_properties(entry: ArchivedUrl) -> dict[str, Any]:
    """A stored URL's properties (only those the archive gave a value for)."""
    properties: dict[str, Any] = {
        "first_captured": entry.first,
        "last_captured": entry.latest if entry.last_known else None,
        "capture_count": entry.count,
        "last_status": entry.status,
        "mimetype": entry.mimetype,
        "archive_url": archive_url(entry),
        "query_params": entry.sorted_params() or None,
    }
    return {key: value for key, value in properties.items() if value is not None}


class WaybackShuttle(Shuttle):
    """Domain → archived URLs and subdomains (Wayback Machine). See the module docstring."""

    meta = ShuttleMeta(
        name="wayback",
        version="1.0.0",
        title="Archived URLs (Wayback Machine)",
        description=(
            "Lists the pages of the domain and its subdomains that the Internet Archive's "
            "Wayback Machine has captured (no API key), with when they were first captured, "
            "their last HTTP status and a link to the archived copy. Never contacts the domain "
            "itself. Static assets and other domains are left out; query strings are removed "
            "(only parameter names are kept) and addresses containing an email address are "
            "dropped. Large domains can take a few minutes; the archive is asked at most once "
            "a second."
        ),
        input_types=frozenset({D}),
        # Domain: the input gets properties, and subdomains that host URLs are added.
        output_entity_types=frozenset({U, D}),
        output_relationship_types=frozenset({RelationType.HOSTS, RelationType.HAS_SUBDOMAIN}),
        # It reads the archive's existing captures; nothing is sent to the domain.
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        homepage="https://web.archive.org",
        terms_url="https://archive.org/about/terms",
        usage_note=USAGE_NOTE,
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
    )

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        """``http``, ``sleep`` and ``clock`` replace ``ctx.http`` and the real time in tests.

        A given ``http`` client stays open: whoever made it closes it.
        """
        self._http = http
        self._sleep = sleep
        self._clock = clock

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        scope = entity.value.removeprefix("*.")
        try:
            registrable = registrable_domain(scope)
        except NormalizationError:
            registrable = None
        if registrable is None:
            raise InputRefusedError(RefusalReason.NOT_REGISTRABLE)
        run = _Run(self, ctx, entity, scope)
        try:
            async for finding in run.findings():
                yield finding
        except ResultTooLargeError as error:
            raise ShuttleFailedError(FailureReason.RESULT_TOO_LARGE) from error
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except SourceUnavailableError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        finally:
            run.report()


class _Run:
    """One run of the Shuttle on one domain."""

    def __init__(
        self, shuttle: WaybackShuttle, ctx: ShuttleContext, entity: EntityInput, scope: str
    ) -> None:
        self.ctx = ctx
        self.entity = entity
        self.scope = scope
        self.client = CdxClient(
            shuttle._http or ctx.http,
            sleep=shuttle._sleep,
            clock=shuttle._clock,
            deadline=shuttle._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        self.collected = Collected()
        self.malformed = 0

    def report(self) -> None:
        dropped = self.collected.dropped
        if dropped:
            # In the order the rules apply.
            details = ", ".join(
                f"{dropped[reason]:,} {label}"
                for reason, label in DROP_LABELS.items()
                if dropped[reason]
            )
            self.ctx.note(NOTE_DROPPED.format(total=dropped.total(), details=details))
        if self.collected.stripped:
            self.ctx.note(NOTE_STRIPPED.format(count=self.collected.stripped))
        if self.malformed:
            self.ctx.note(NOTE_MALFORMED.format(count=self.malformed))
        self.ctx.progress(
            f"{self.client.requests} CDX request(s), {self.client.retries} retried, "
            f"{self.client.rate_limited} rate-limited."
        )

    # --- asking ---

    async def captures(self, params: dict[str, str]) -> list[Capture]:
        captures, malformed = parse_rows(await self.client.query(params))
        self.malformed += malformed
        return captures

    async def listing(self) -> list[Capture]:
        """Every archived address under the domain; fewer if the answer is too large."""
        limit = LISTING_LIMIT
        try:
            rows = await self.client.query(listing_params(self.scope, limit))
        except ResultTooLargeError:
            self.ctx.note(NOTE_SMALLER_LISTING)
            limit = SMALLER_LISTING_LIMIT
            rows = await self.client.query(listing_params(self.scope, limit))
        captures, malformed = parse_rows(rows)
        self.malformed += malformed
        if len(captures) + malformed >= limit:
            self.ctx.note(NOTE_LISTING_LIMIT.format(limit=limit))
        return captures

    # --- findings ---

    async def findings(self) -> AsyncIterator[Finding]:
        self.ctx.progress("Asking the Wayback Machine for the home page's first and last capture.")
        first = await self.captures(first_capture_params(self.scope))
        latest = await self.captures(last_capture_params(self.scope))
        self.ctx.progress("Listing the domain's archived addresses (this can take a minute).")
        self.collected = collect(await self.listing(), self.scope)
        # The home page's own captures: the true first and latest, not a new address.
        for capture in first:
            self.collected.add(capture, self.scope, counted=False)
        for capture in latest:
            entry = self.collected.add(capture, self.scope, counted=False)
            if entry is not None and entry.latest == capture.timestamp:
                entry.last_known = True

        collected = self.collected
        if collected.first is None:
            self.ctx.note(NOTE_NO_CAPTURES)
        properties: dict[str, Any] = {
            "wayback_first_capture": collected.first,
            "wayback_last_capture": collected.last,
            "wayback_url_count": len(collected.urls),
        }
        yield EntityFinding(
            type=D,
            value=self.entity.value,
            properties={k: v for k, v in properties.items() if v is not None},
        )

        selection = select(
            collected.urls.values(),
            self.scope,
            max_urls=MAX_URLS,
            max_subdomains=MAX_SUBDOMAINS,
        )
        if selection.over_url_cap:
            self.ctx.note(
                NOTE_URLS_CAPPED.format(found=len(collected.urls), kept=len(selection.urls))
            )
        if selection.over_subdomain_cap:
            self.ctx.note(
                NOTE_SUBDOMAINS_CAPPED.format(
                    count=selection.over_subdomain_cap, limit=MAX_SUBDOMAINS
                )
            )
        self.ctx.progress(
            f"{len(selection.urls):,} URLs on {len(selection.subdomains) + 1:,} host(s); "
            "adding them."
        )
        for name in selection.subdomains:
            yield EntityFinding(type=D, value=name)
            yield RelationshipFinding(
                type=RelationType.HAS_SUBDOMAIN,
                source=self.entity.ref,
                target=EntityRef(type=D, value=name),
            )
        for entry in selection.urls:
            host = (
                self.entity.ref if entry.host == self.scope else EntityRef(type=D, value=entry.host)
            )
            yield EntityFinding(type=U, value=entry.url, properties=url_properties(entry))
            yield RelationshipFinding(
                type=RelationType.HOSTS, source=host, target=EntityRef(type=U, value=entry.url)
            )


__all__ = [
    "ADMIRALTY",
    "CONFIDENCE",
    "LISTING_LIMIT",
    "MAX_SUBDOMAINS",
    "MAX_URLS",
    "REQUEST_BUDGET_SECONDS",
    "SMALLER_LISTING_LIMIT",
    "TIMEOUT_SECONDS",
    "USAGE_NOTE",
    "WaybackShuttle",
    "archive_url",
    "url_properties",
]
