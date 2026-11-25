"""The Have I Been Pwned Shuttles: the breaches of a site, and of an email address.

Have I Been Pwned (https://haveibeenpwned.com/) catalogues published data
breaches. Its breach data is licensed CC BY 4.0 (credit HIBP when you share
it). Both Shuttles ask its API v3 through ``ctx.http`` only
(:mod:`nightloom_shuttle_hibp.client`) and keep **breach metadata only**:
never a password, a leaked record, a paste or HIBP's description text.

``hibp-breaches`` (Domain, passive, free, no key)
    The breaches *of the site*: HIBP's public breach list filtered to the
    domain (its registrable domain, for a subdomain: HIBP lists breaches by
    the site's domain). Each becomes a Breach with ``BREACH_OF`` to the
    domain. Retired breaches are left out.

``hibp-account`` (Email, person-related, the operator's paid key)
    The breaches *an address is in*, from ``breachedaccount``, with the key
    in ``NIGHTLOOM_HIBP_API_KEY`` (:mod:`nightloom_shuttle_hibp.config`). Each
    becomes a Breach with ``EXPOSED_IN`` from the address, and the breached
    site's Domain with ``BREACH_OF``. **Sensitive** breaches (those HIBP
    marks sensitive, such as adult sites, where membership itself is
    harmful to reveal) and retired ones are never kept: a note counts them.
    It only runs in cases that allow person-related entities, like every
    Shuttle on an Email, and never in an auto-pivot.

Provenance follows HIBP's flags: a verified breach is **A2** (0.9), an
unverified one **C3** (0.5) and a fabricated one **D4** (0.3).
"""

import asyncio
import time
from collections.abc import AsyncIterator

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import registrable_domain
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
from nightloom_shuttle_hibp.breaches import HibpBreach, InvalidBreachError, parse_breach
from nightloom_shuttle_hibp.client import (
    Clock,
    HibpClient,
    InvalidResponseError,
    KeyRefusedError,
    Sleep,
    SourceUnavailableError,
)
from nightloom_shuttle_hibp.config import KEY_VARIABLE, load_api_key

DOMAIN = EntityType.DOMAIN
EMAIL = EntityType.EMAIL
BREACH = EntityType.BREACH
BREACH_OF = RelationType.BREACH_OF
EXPOSED_IN = RelationType.EXPOSED_IN
HAS_SUBDOMAIN = RelationType.HAS_SUBDOMAIN

MAX_BREACHES = 200
ADMIRALTY, CONFIDENCE = "A2", 0.9
UNVERIFIED = ("C3", 0.5)
FABRICATED = ("D4", 0.3)

HOMEPAGE = "https://haveibeenpwned.com/API/v3"
TERMS_URL = "https://haveibeenpwned.com/API/v3#AcceptableUse"
LICENSE_NOTE = (
    "Breach data from Have I Been Pwned (haveibeenpwned.com), licensed CC BY 4.0: "
    "credit HIBP when you share it."
)

NOTE_NO_SITE_BREACH = "Have I Been Pwned lists no breach of this site."
NOTE_SITE_LOOKUP = "Asked about {domain}: HIBP lists breaches by the site's domain."
NOTE_NOT_PWNED = "Have I Been Pwned has this address in no breach it can show."
NOTE_NO_KEY = (
    f"This Shuttle needs an HIBP API key (a paid subscription): the operator sets it as "
    f"{KEY_VARIABLE} for the worker."
)
NOTE_KEY_REFUSED = f"Have I Been Pwned refused the API key in {KEY_VARIABLE}."
NOTE_SENSITIVE = "Left out {count:,} sensitive breach(es): being in one can be harmful to reveal."
NOTE_RETIRED = "Left out {count:,} retired breach(es)."
NOTE_MALFORMED = "Ignored {count:,} malformed breach(es) in HIBP's answer."
NOTE_CAPPED = "HIBP lists {count:,} breaches; added the first {limit:,}."


def rating(breach: HibpBreach) -> tuple[str | None, float | None]:
    """The (admiralty, confidence) override of a breach's findings; ``None`` keeps A2/0.9."""
    if breach.fabricated:
        return FABRICATED
    if breach.verified is False:
        return UNVERIFIED
    return None, None


class _HibpShuttle(Shuttle):
    """What both Shuttles share: the client, parsing and the run's notes."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        api_key: str | None = None,
    ) -> None:
        """``http``, ``sleep``, ``clock`` and ``api_key`` replace the real ones in tests.

        A given ``http`` client stays open: whoever made it closes it.
        """
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self._api_key = api_key

    def _client(self, ctx: ShuttleContext) -> HibpClient:
        return HibpClient(
            self._http or ctx.http,
            sleep=self._sleep,
            clock=self._clock,
            deadline=self._clock() + self.meta.timeout_seconds * 0.75,
            before_request=ctx.raise_if_cancelled,
        )

    @staticmethod
    def _report(ctx: ShuttleContext, client: HibpClient) -> None:
        ctx.progress(
            f"{client.requests} HIBP request(s), {client.retries} retried, "
            f"{client.rate_limited} rate-limited."
        )

    @staticmethod
    def _breaches(
        ctx: ShuttleContext, items: list[object], *, drop_sensitive: bool
    ) -> list[HibpBreach]:
        """The usable breaches, by name; malformed, retired (and sensitive) ones counted."""
        kept: dict[str, HibpBreach] = {}
        malformed = retired = sensitive = 0
        for item in items:
            try:
                breach = parse_breach(item)
            except InvalidBreachError:
                malformed += 1
                continue
            if breach.retired:
                retired += 1
            elif drop_sensitive and breach.sensitive:
                sensitive += 1
            else:
                kept.setdefault(breach.name.casefold(), breach)
        if sensitive:
            ctx.note(NOTE_SENSITIVE.format(count=sensitive))
        if retired:
            ctx.note(NOTE_RETIRED.format(count=retired))
        if malformed:
            ctx.note(NOTE_MALFORMED.format(count=malformed))
        ordered = sorted(kept.values(), key=lambda breach: breach.name.casefold())
        if len(ordered) > MAX_BREACHES:
            ctx.note(NOTE_CAPPED.format(count=len(ordered), limit=MAX_BREACHES))
        return ordered[:MAX_BREACHES]

    @staticmethod
    def _breach(breach: HibpBreach) -> EntityFinding:
        admiralty, confidence = rating(breach)
        return EntityFinding(
            type=BREACH,
            value=breach.name,
            properties=breach.properties(),
            admiralty=admiralty,
            confidence=confidence,
        )

    @staticmethod
    def _link(
        relation: RelationType, source: EntityRef, target: EntityRef, breach: HibpBreach
    ) -> RelationshipFinding:
        admiralty, confidence = rating(breach)
        return RelationshipFinding(
            type=relation, source=source, target=target, admiralty=admiralty, confidence=confidence
        )


class HibpBreachesShuttle(_HibpShuttle):
    """The breaches of a site, from HIBP's public breach list. See the module docstring."""

    meta = ShuttleMeta(
        name="hibp-breaches",
        version="1.0.0",
        title="Breaches of a site (Have I Been Pwned)",
        description=(
            "Lists the published data breaches of a site from Have I Been Pwned's public breach "
            "list (free, no API key): each breach's name, title, date, number of accounts, kinds "
            "of data leaked and whether HIBP verified it, as a Breach linked to the domain with "
            "BREACH_OF. Metadata only: never passwords or leaked records."
        ),
        input_types=frozenset({DOMAIN}),
        output_entity_types=frozenset({BREACH, DOMAIN}),
        output_relationship_types=frozenset({BREACH_OF, HAS_SUBDOMAIN}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.ORGANIZATION,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=2),
        timeout_seconds=60,
        cache_ttl_seconds=24 * 3600,
        pivots=False,
        homepage=HOMEPAGE,
        terms_url=TERMS_URL,
        usage_note=LICENSE_NOTE,
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
    )

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        client = self._client(ctx)
        site = registrable_domain(entity.value) or entity.value
        target = EntityRef(type=DOMAIN, value=site)
        try:
            if site != entity.value:
                ctx.note(NOTE_SITE_LOOKUP.format(domain=site))
                yield EntityFinding(type=DOMAIN, value=site)
                yield RelationshipFinding(type=HAS_SUBDOMAIN, source=target, target=entity.ref)
            ctx.progress("Asking Have I Been Pwned for the site's breaches.")
            items = await client.site_breaches(site)
            breaches = [
                breach
                for breach in self._breaches(ctx, items, drop_sensitive=False)
                if breach.domain == site
            ]
            if not breaches:
                ctx.note(NOTE_NO_SITE_BREACH)
            for breach in breaches:
                yield self._breach(breach)
                yield self._link(
                    BREACH_OF, EntityRef(type=BREACH, value=breach.name), target, breach
                )
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except (SourceUnavailableError, KeyRefusedError) as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        finally:
            self._report(ctx, client)


class HibpAccountShuttle(_HibpShuttle):
    """The breaches an email address is in (the operator's HIBP key). See the module docstring."""

    meta = ShuttleMeta(
        name="hibp-account",
        version="1.0.0",
        title="Breaches of an email address (Have I Been Pwned)",
        description=(
            "Lists the published data breaches an email address appears in, from Have I Been "
            "Pwned (needs the operator's paid HIBP API key, NIGHTLOOM_HIBP_API_KEY): each "
            "breach as a Breach with EXPOSED_IN from the address, and the breached site's "
            "domain with BREACH_OF. Leaves out sensitive breaches. Metadata only: never "
            "passwords, pastes or leaked records."
        ),
        input_types=frozenset({EMAIL}),
        output_entity_types=frozenset({BREACH, DOMAIN}),
        output_relationship_types=frozenset({EXPOSED_IN, BREACH_OF}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.PERSON,
        network=True,
        # HIBP's smallest subscription allows 10 requests a minute.
        rate_limit=RateLimit(requests=1, period_seconds=6),
        timeout_seconds=90,
        cache_ttl_seconds=24 * 3600,
        pivots=False,
        homepage=HOMEPAGE,
        terms_url=TERMS_URL,
        usage_note=(
            f"{LICENSE_NOTE} Needs the operator's paid HIBP API key ({KEY_VARIABLE}); use it "
            "only for addresses you have a legitimate reason to check."
        ),
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
    )

    @classmethod
    def check_configuration(cls) -> None:
        """A set key must look like one (32 hex characters); no key is fine until a run."""
        load_api_key()

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        key = self._api_key or load_api_key()
        if key is None:
            ctx.note(NOTE_NO_KEY)
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)
        client = self._client(ctx)
        try:
            ctx.progress("Asking Have I Been Pwned for the address's breaches.")
            items = await client.account_breaches(entity.value, key=key)
            breaches = self._breaches(ctx, items, drop_sensitive=True)
            if not breaches:
                ctx.note(NOTE_NOT_PWNED)
            for breach in breaches:
                source = EntityRef(type=BREACH, value=breach.name)
                yield self._breach(breach)
                yield self._link(EXPOSED_IN, entity.ref, source, breach)
                if breach.domain is not None:
                    site = EntityRef(type=DOMAIN, value=breach.domain)
                    yield EntityFinding(type=DOMAIN, value=breach.domain)
                    yield self._link(BREACH_OF, source, site, breach)
        except KeyRefusedError as error:
            ctx.note(NOTE_KEY_REFUSED)
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except SourceUnavailableError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        finally:
            self._report(ctx, client)


__all__ = [
    "ADMIRALTY",
    "CONFIDENCE",
    "FABRICATED",
    "LICENSE_NOTE",
    "MAX_BREACHES",
    "UNVERIFIED",
    "HibpAccountShuttle",
    "HibpBreachesShuttle",
    "rating",
]
