"""The typosquat Shuttle: which lookalikes of a domain are registered.

It uses the DNS Shuttle's resolver policy unchanged: the same configuration
(:mod:`nightloom_shuttle_dns.config`), the same client
(:mod:`nightloom_shuttle_dns.client`, public resolvers only, never the host's
``/etc/resolv.conf``) and the same rate limit per resolver. It never contacts
the domain or the lookalikes themselves: only the resolvers, which ask the
registries' name servers.

A Domain is checked as its registrable domain (``shop.example.org`` →
``example.org``, with a job note). :func:`~nightloom_shuttle_dns.permutations.lookalikes`
gives at most :data:`~nightloom_shuttle_dns.permutations.MAX_CANDIDATES`
candidates (typos, lookalike characters, other suffixes). For each, the
Shuttle asks for its **NS** records: a registered domain is delegated to name
servers, so an answer means *registered*, and a name that does not exist
(NXDOMAIN) or has no NS records is left out. A registered domain kept on hold
without name servers is therefore missed; the note says so.

Each registered lookalike becomes a Domain that is ``LOOKALIKE_OF`` the
domain, with the kind of change in the relationship's note
(``kind: transposition (two letters swapped)``). The domain gets
``typosquats_checked_at``, ``typosquat_candidates`` and
``typosquats_registered``. Run the DNS Shuttle (or a scan profile) on a
lookalike to see where it points and whether it can receive mail.

At most :data:`CONCURRENCY` queries are in flight, under the per-resolver rate
limit. No query starts after :data:`BUDGET_SECONDS`; a run stops after
:data:`MAX_CONSECUTIVE_FAILURES` failed queries in a row, and fails with
``source_unavailable`` when no query got an answer.

``pivots=False``: an auto-pivot would check the lookalikes' own lookalikes,
hundreds of queries each, without getting closer to the target.

Provenance **B2** (confidence 0.8): a recursive resolver's answer, usually
reliable and probably true. A lookalike is a similar *name*, registered by
someone: not necessarily malicious, and possibly the owner's own defensive
registration.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import dns.rdatatype

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
    RefusalReason,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleFailedError,
    ShuttleMeta,
)
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_shuttle_dns.client import (
    RESOLVER_RATE_LIMIT,
    DnsClient,
    DnsQueryError,
    ResolverClient,
)
from nightloom_shuttle_dns.config import (
    RESOLVER_HOMEPAGE,
    RESOLVER_TERMS_URL,
    DnsConfig,
    load_config,
)
from nightloom_shuttle_dns.names import is_internal_name
from nightloom_shuttle_dns.permutations import KINDS, MAX_CANDIDATES, Candidate, lookalikes

D = EntityType.DOMAIN
R = dns.rdatatype.RdataType

CONCURRENCY = 8
BUDGET_SECONDS = 240.0
MAX_CONSECUTIVE_FAILURES = 25
ADMIRALTY, CONFIDENCE = "B2", 0.8
CACHE_TTL_SECONDS = 24 * 60 * 60

NOTE_REGISTRABLE = "Checked lookalikes of the registrable domain {domain}."
NOTE_SUMMARY = (
    "Tried {tried} lookalike name(s): {registered} registered, {failed} quer(ies) failed. "
    "Registered means delegated to name servers; a domain on hold without them is not found."
)
NOTE_TIME_LIMIT = "Reached the time limit after {tried} of {total} names."
NOTE_FAILING = "Stopped after {count} failed queries in a row: {tried} of {total} names."
NOTE_NONE = "No lookalike names could be made from this domain."

Now = Callable[[], datetime]
Clock = Callable[[], float]


def _now() -> datetime:
    return datetime.now(UTC)


def lookalike_note(kind: str) -> str:
    """The note on a ``LOOKALIKE_OF`` relationship: the kind of change and what it means."""
    return f"kind: {kind} ({KINDS[kind]})"


class TyposquatShuttle(Shuttle):
    """Domain → its registered lookalike domains. See the module docstring."""

    meta = ShuttleMeta(
        name="typosquats",
        version="1.0.0",
        title="Lookalike domains (typosquats)",
        description=(
            f"Makes up to {MAX_CANDIDATES} lookalikes of the domain (typos, swapped or "
            "neighbouring keys, lookalike characters such as 1 for l or Cyrillic letters, other "
            "suffixes) and asks public resolvers which are registered, like the DNS Shuttle. "
            "Registered ones are added as Domains linked with LOOKALIKE_OF; run DNS on them to "
            "see where they point. Never contacts the domain or the lookalikes, and never runs "
            "in an auto-pivot."
        ),
        input_types=frozenset({D}),
        output_entity_types=frozenset({D}),
        output_relationship_types=frozenset({RelationType.LOOKALIKE_OF}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        rate_limit=RESOLVER_RATE_LIMIT,
        timeout_seconds=300,
        cache_ttl_seconds=CACHE_TTL_SECONDS,
        homepage=RESOLVER_HOMEPAGE,
        terms_url=RESOLVER_TERMS_URL,
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
        pivots=False,
    )

    def __init__(
        self,
        *,
        client: DnsClient | None = None,
        config: DnsConfig | None = None,
        now: Now = _now,
        clock: Clock = time.monotonic,
        limit: int = MAX_CANDIDATES,
    ) -> None:
        """Tests replace ``client``, ``config``, ``now``, ``clock`` and ``limit``."""
        self._client = client
        self._config = config
        self._now = now
        self._clock = clock
        self._limit = limit

    @classmethod
    def check_configuration(cls) -> None:
        load_config()

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        config = self._config or load_config()
        if is_internal_name(entity.value, allow_test_zone=config.allow_private_resolvers):
            raise InputRefusedError(RefusalReason.INTERNAL_NAME)
        try:
            domain = registrable_domain(entity.value)
        except NormalizationError:
            domain = None
        if domain is None:
            raise InputRefusedError(RefusalReason.NOT_REGISTRABLE)
        if domain != entity.value:
            ctx.note(NOTE_REGISTRABLE.format(domain=domain))
        candidates = lookalikes(domain, limit=self._limit)
        if not candidates:
            ctx.note(NOTE_NONE)
        rate_limit = self.meta.rate_limit
        assert rate_limit is not None  # noqa: S101 - network=True requires it
        client = self._client or ResolverClient(config.resolvers, HostRateLimiter(rate_limit))
        sweep = _Sweep(ctx, client, candidates, clock=self._clock)
        registered = await sweep.run()
        if candidates and sweep.answered == 0:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)
        ctx.note(
            NOTE_SUMMARY.format(tried=sweep.tried, registered=len(registered), failed=sweep.failed)
        )
        yield EntityFinding(
            type=D,
            value=domain,
            properties={
                "typosquats_checked_at": self._now(),
                "typosquat_candidates": sweep.tried,
                "typosquats_registered": len(registered),
            },
        )
        target = EntityRef(type=D, value=domain)
        for candidate in registered:
            yield EntityFinding(type=D, value=candidate.name)
            yield RelationshipFinding(
                type=RelationType.LOOKALIKE_OF,
                source=EntityRef(type=D, value=candidate.name),
                target=target,
                note=lookalike_note(candidate.kind),
            )


class _Sweep:
    """The NS queries of one run, a few at a time, with their counts."""

    def __init__(
        self,
        ctx: ShuttleContext,
        client: DnsClient,
        candidates: list[Candidate],
        *,
        clock: Clock,
    ) -> None:
        self.ctx = ctx
        self.client = client
        self.candidates = candidates
        self.clock = clock
        self.tried = 0
        self.answered = 0
        self.failed = 0
        self.failing = 0  # failures in a row
        self.stopped = False

    async def run(self) -> list[Candidate]:
        """The registered candidates, in the order they were generated."""
        deadline = self.clock() + BUDGET_SECONDS
        total = len(self.candidates)
        queue = iter(enumerate(self.candidates))
        found: dict[int, Candidate] = {}

        async def worker() -> None:
            for index, candidate in queue:
                if self.stopped:
                    return
                self.ctx.raise_if_cancelled()
                if self.clock() >= deadline:
                    if not self.stopped:
                        self.stopped = True
                        self.ctx.note(NOTE_TIME_LIMIT.format(tried=self.tried, total=total))
                    return
                self.tried += 1
                if self.tried % 50 == 0:
                    self.ctx.progress(f"Checked {self.tried} of {total} names.")
                try:
                    answer = await self.client.query(candidate.name, R.NS)
                except DnsQueryError as error:
                    self.failed += 1
                    self.failing += 1
                    # The error class only; never the name.
                    self.ctx.logger.warning("NS query failed: %s", error)
                    if self.failing >= MAX_CONSECUTIVE_FAILURES and not self.stopped:
                        self.stopped = True
                        self.ctx.note(
                            NOTE_FAILING.format(count=self.failing, tried=self.tried, total=total)
                        )
                    continue
                self.answered += 1
                self.failing = 0
                if answer is not None and answer.records:
                    found[index] = candidate

        try:
            # A task group cancels the other workers when one raises (a cancelled job).
            async with asyncio.TaskGroup() as group:
                for _ in range(CONCURRENCY):
                    group.create_task(worker())
        except BaseExceptionGroup as errors:
            raise errors.exceptions[0] from None
        return [found[index] for index in sorted(found)]


__all__ = [
    "BUDGET_SECONDS",
    "CONCURRENCY",
    "MAX_CONSECUTIVE_FAILURES",
    "TyposquatShuttle",
    "lookalike_note",
]
