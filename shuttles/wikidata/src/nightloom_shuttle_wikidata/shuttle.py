"""The Wikidata Shuttle: organizations' websites, country, industry and corporate family.

Wikidata (https://www.wikidata.org/) is the Wikimedia Foundation's free,
collaboratively edited knowledge base, published under CC0. The Shuttle
asks its query service through ``ctx.http`` only
(:mod:`nightloom_shuttle_wikidata.client`), with the fixed SPARQL queries of
:mod:`nightloom_shuttle_wikidata.queries`, and never contacts the
organization or the domain.

**Organization.** The item is found, in order, by the ``wikidata_id``
property; by the name if it *is* an item ID (``Q95``: the organization is
then added under its English label, the input keeping ``wikidata_id``); by
the ``lei`` property (Wikidata's P1278); or by an English label or alias
equal to the name among items that are organizations. None is a note;
several are a note listing their item IDs and nothing is added. From the
item it yields:

- the Organization with ``wikidata_id``, ``country`` (when the item has
  exactly one), ``lei`` (when exactly one, with valid check digits),
  ``industry`` (up to three, alphabetical) and ``wikidata_checked_at``;
- each official website's registrable domain as a Domain with ``OWNS``
  from the organization (at most :data:`MAX_WEBSITES`);
- its parent organizations (at most :data:`MAX_PARENTS`) and subsidiaries
  (at most :data:`MAX_SUBSIDIARIES`), stated on either item, as
  Organizations with ``SUBSIDIARY_OF``.

**Domain.** The organizations whose official website is the domain's home
page (``https``/``http``, with or without ``www.`` and a trailing slash),
at most :data:`MAX_OWNERS`, each with ``OWNS`` to the domain.

**People.** Wikidata describes people too. Every query excludes items that
are an instance of *human*; an item ID that turns out to be a person (or
not an organization at all) adds nothing and says so in a note.

Provenance: Wikidata is edited by anyone and usually well sourced for
organizations, but not checked by an authority: **C2** (confidence 0.7).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import (
    NormalizationError,
    normalize,
    normalize_text,
    registrable_domain,
    text_key,
)
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
from nightloom_shuttle_wikidata import queries
from nightloom_shuttle_wikidata.client import (
    Clock,
    InvalidResponseError,
    Row,
    Sleep,
    SourceUnavailableError,
    WikidataClient,
)

ORG = EntityType.ORGANIZATION
DOMAIN = EntityType.DOMAIN
OWNS = RelationType.OWNS
SUBSIDIARY_OF = RelationType.SUBSIDIARY_OF

TIMEOUT_SECONDS = 120
# No new request starts this long after the run began.
REQUEST_BUDGET_SECONDS = 100
MAX_WEBSITES = 20
MAX_PARENTS = 10
MAX_SUBSIDIARIES = 50
MAX_OWNERS = 10
MAX_INDUSTRIES = 3
# Item IDs listed in the note when a name matches several items.
MAX_LISTED = 5

ADMIRALTY, CONFIDENCE = "C2", 0.7

WEBSITE_NOTE = "official website, per Wikidata"
PARENT_NOTE = "parent organization, per Wikidata"

USAGE_NOTE = (
    "Wikidata's data is free to use under CC0. Anyone can edit it: check important "
    "claims against the sources the item cites."
)

NOTE_NO_MATCH = (
    "Wikidata has no organization with this exact English name or alias. Try the name "
    "Wikidata uses, or add the item ID (such as Q95) as an organization."
)
NOTE_SEVERAL = (
    "{count:,} Wikidata organizations have this name, so nothing was added: {listed}. Add "
    "the item ID you mean as an organization and run this Shuttle on it."
)
NOTE_LEI_SEVERAL = "{count:,} Wikidata items have this LEI; looked the organization up by name."
NOTE_BAD_ID = (
    "The organization's wikidata_id property is not a Wikidata item ID; looked it up another way."
)
NOTE_PERSON = "This Wikidata item is a person, so nothing was kept."
NOTE_NOT_ORGANIZATION = "This Wikidata item is not an organization, so nothing was added."
NOTE_BY_ID = "Looked up the Wikidata item {qid}: added the organization as {name}."
NOTE_NO_LABEL = "The Wikidata item has no English name; its data was added to this entity."
NOTE_NO_WEBSITE = "No Wikidata organization has this domain as its official website."
NOTE_CAPPED = "Wikidata lists {count:,} {what}; added {limit:,}."
NOTE_SKIPPED = "Ignored {count:,} malformed or unusable value(s) in Wikidata's answer."

Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def is_valid_lei(value: str) -> bool:
    """An LEI with correct ISO 7064 MOD 97-10 check digits (same rule as the GLEIF Shuttle)."""
    if not queries.LEI.fullmatch(value):
        return False
    return int("".join(str(int(character, 36)) for character in value)) % 97 == 1


def item_of(text: object) -> str | None:
    """``text`` as a Wikidata item ID if it is one (trimmed, upper-cased)."""
    if not isinstance(text, str):
        return None
    candidate = text.strip().upper()
    return candidate if queries.ITEM_ID.fullmatch(candidate) else None


@dataclass
class Details:
    """What the details query says about one item."""

    label: str | None = None
    websites: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    leis: set[str] = field(default_factory=set)
    industries: set[str] = field(default_factory=set)
    human: bool = False
    organization: bool = False


class WikidataShuttle(Shuttle):
    """Organizations from Wikidata. See the module docstring."""

    meta = ShuttleMeta(
        name="wikidata",
        version="1.0.0",
        title="Organization facts (Wikidata)",
        description=(
            "Looks organizations up in Wikidata (free, no API key). For an organization (by its "
            "exact English name or alias, an item ID such as Q95 given as its name, or its "
            "wikidata_id or lei property): its official websites as Domains it OWNS, its "
            "country, LEI and industry, and its parent organizations and subsidiaries, linked "
            "with SUBSIDIARY_OF. For a domain: the organizations whose official website it is. "
            "Never keeps items that are people."
        ),
        input_types=frozenset({ORG, DOMAIN}),
        output_entity_types=frozenset({ORG, DOMAIN}),
        output_relationship_types=frozenset({OWNS, SUBSIDIARY_OF}),
        # It reads Wikidata; nothing is sent to the organization or the domain.
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.ORGANIZATION,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        # A week: facts about organizations change slowly.
        cache_ttl_seconds=7 * 24 * 3600,
        # It turns a domain into organizations: auto-pivots never follow organizations.
        pivots=False,
        homepage="https://query.wikidata.org/",
        terms_url="https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use",
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
        now: Now = _utcnow,
    ) -> None:
        """``http``, ``sleep``, ``clock`` and ``now`` replace the real ones in tests.

        A given ``http`` client stays open: whoever made it closes it.
        """
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self._now = now

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        run = _Run(self, ctx, entity)
        try:
            findings = run.domain() if entity.type == DOMAIN else run.organization()
            async for finding in findings:
                yield finding
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except SourceUnavailableError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        finally:
            run.report()


class _Run:
    """One run of the Shuttle on one Organization or Domain."""

    def __init__(self, shuttle: WikidataShuttle, ctx: ShuttleContext, entity: EntityInput) -> None:
        self.ctx = ctx
        self.entity = entity
        self.now = shuttle._now
        self.client = WikidataClient(
            shuttle._http or ctx.http,
            sleep=shuttle._sleep,
            clock=shuttle._clock,
            deadline=shuttle._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        self.skipped = 0

    def report(self) -> None:
        if self.skipped:
            self.ctx.note(NOTE_SKIPPED.format(count=self.skipped))
        self.ctx.progress(
            f"{self.client.requests} Wikidata request(s), {self.client.retries} retried, "
            f"{self.client.rate_limited} rate-limited."
        )

    def checked(self) -> dict[str, Any]:
        return {"wikidata_checked_at": self.now()}

    # --- small helpers ---

    def name(self, value: str | None) -> str | None:
        """``value`` as an Organization name, or ``None`` (counted) if unusable."""
        if value is None:
            return None
        try:
            return normalize(ORG, value).value
        except NormalizationError:
            self.skipped += 1
            return None

    def qids(self, rows: Iterable[Row], variable: str = "item") -> list[str]:
        found: list[str] = []
        for row in rows:
            qid = queries.qid_of(row.get(variable, ""))
            if qid is None:
                self.skipped += 1
            elif qid not in found:
                found.append(qid)
        return found

    def capped(self, values: list[Any], limit: int, what: str) -> list[Any]:
        if len(values) > limit:
            self.ctx.note(NOTE_CAPPED.format(count=len(values), what=what, limit=limit))
        return values[:limit]

    # --- a Domain ---

    async def domain(self) -> AsyncIterator[Finding]:
        self.ctx.progress("Asking Wikidata which organizations have this website.")
        try:
            query = queries.by_website(self.entity.value)
        except ValueError:
            self.ctx.note(NOTE_NO_WEBSITE)
            return
        owners: dict[str, str] = {}
        for row in await self.client.select(query):
            qid = queries.qid_of(row.get("item", ""))
            label = self.name(row.get("label"))
            if qid is None or label is None:
                if qid is None:
                    self.skipped += 1
                continue
            owners.setdefault(qid, label)
        if not owners:
            self.ctx.note(NOTE_NO_WEBSITE)
            return
        for qid in self.capped(sorted(owners, key=_item_number), MAX_OWNERS, "organizations"):
            label = owners[qid]
            yield EntityFinding(
                type=ORG, value=label, properties={"wikidata_id": qid, **self.checked()}
            )
            yield RelationshipFinding(
                type=OWNS,
                source=EntityRef(type=ORG, value=label),
                target=self.entity.ref,
                note=WEBSITE_NOTE,
            )

    # --- an Organization ---

    async def organization(self) -> AsyncIterator[Finding]:
        qid = await self.find()
        if qid is None:
            return
        self.ctx.progress(f"Reading the Wikidata item {qid}.")
        details = self.details(await self.client.select(queries.details(qid)))
        if details.human:
            self.ctx.note(NOTE_PERSON)
            return
        if not details.organization:
            self.ctx.note(NOTE_NOT_ORGANIZATION)
            return

        subject = self.entity.ref
        if item_of(self.entity.value) == qid:
            if details.label is None:
                self.ctx.note(NOTE_NO_LABEL)
            else:
                # The input's name is the item ID: the organization goes in under its name.
                yield EntityFinding(
                    type=ORG, value=self.entity.value, properties={"wikidata_id": qid}
                )
                self.ctx.note(NOTE_BY_ID.format(qid=qid, name=details.label))
                subject = EntityRef(type=ORG, value=details.label)
        yield EntityFinding(type=ORG, value=subject.value, properties=self.properties(qid, details))

        for domain in self.capped(sorted(details.websites), MAX_WEBSITES, "official websites"):
            yield EntityFinding(type=DOMAIN, value=domain)
            yield RelationshipFinding(
                type=OWNS,
                source=subject,
                target=EntityRef(type=DOMAIN, value=domain),
                note=WEBSITE_NOTE,
            )

        self.ctx.progress("Asking Wikidata for parent organizations and subsidiaries.")
        async for finding in self.relatives(qid, subject):
            yield finding

    async def find(self) -> str | None:
        """The input's item ID, or ``None`` (with a note) when there is no single one."""
        given = self.entity.properties.get("wikidata_id")
        qid = item_of(given)
        if given is not None and qid is None:
            self.ctx.note(NOTE_BAD_ID)
        qid = qid or item_of(self.entity.value)
        if qid is not None:
            return qid

        lei = self.entity.properties.get("lei")
        if isinstance(lei, str) and is_valid_lei(lei.strip().upper()):
            self.ctx.progress("Asking Wikidata for the organization with this LEI.")
            found = self.qids(await self.client.select(queries.by_lei(lei.strip().upper())))
            if len(found) == 1:
                return found[0]
            if found:
                self.ctx.note(NOTE_LEI_SEVERAL.format(count=len(found)))

        self.ctx.progress("Asking Wikidata for organizations with this name.")
        found = self.qids(await self.client.select(queries.by_label(self.entity.value)))
        if not found:
            self.ctx.note(NOTE_NO_MATCH)
            return None
        if len(found) > 1:
            self.ctx.note(NOTE_SEVERAL.format(count=len(found), listed=_listing(found)))
            return None
        return found[0]

    def details(self, rows: list[Row]) -> Details:
        details = Details()
        for row in rows:
            details.human = details.human or row.get("human") == "true"
            details.organization = details.organization or row.get("organization") == "true"
            if details.label is None and "label" in row:
                details.label = self.name(row["label"])
            if "site" in row:
                domain = self.website(row["site"])
                if domain is not None:
                    details.websites.add(domain)
            country = row.get("country", "").strip().upper()
            if len(country) == 2 and country.isascii() and country.isalpha():
                details.countries.add(country)
            lei = row.get("lei", "").strip().upper()
            if is_valid_lei(lei):
                details.leis.add(lei)
            if "industry" in row:
                try:
                    industry = normalize_text(row["industry"])
                except NormalizationError:
                    continue
                if len(industry) <= 60:
                    details.industries.add(industry)
        return details

    def website(self, url: str) -> str | None:
        """The registrable domain of an official website, or ``None`` (counted)."""
        try:
            parts = urlsplit(url)
            host = parts.hostname
            if parts.scheme not in ("http", "https") or host is None:
                raise ValueError("not a web address")
            domain = registrable_domain(normalize(DOMAIN, host).value)
        except (ValueError, NormalizationError):
            domain = None
        if domain is None:
            self.skipped += 1
        return domain

    def properties(self, qid: str, details: Details) -> dict[str, Any]:
        properties: dict[str, Any] = {"wikidata_id": qid}
        if len(details.countries) == 1:
            (properties["country"],) = details.countries
        if len(details.leis) == 1:
            (properties["lei"],) = details.leis
        if details.industries:
            industries = sorted(details.industries, key=text_key)[:MAX_INDUSTRIES]
            properties["industry"] = "; ".join(industries)
        return {**properties, **self.checked()}

    async def relatives(self, qid: str, subject: EntityRef) -> AsyncIterator[Finding]:
        parents: dict[str, str] = {}
        children: dict[str, str] = {}
        for row in await self.client.select(queries.relatives(qid)):
            other = queries.qid_of(row.get("other", ""))
            label = self.name(row.get("label"))
            relation = row.get("relation")
            if other is None or label is None or relation not in ("parent", "subsidiary"):
                if other is None or relation not in ("parent", "subsidiary"):
                    self.skipped += 1
                continue
            if other == qid or text_key(label) == text_key(subject.value):
                continue  # the organization itself: no self-loops
            (parents if relation == "parent" else children).setdefault(other, label)
        for other in parents:
            children.pop(other, None)  # contradictory data: keep it as a parent

        ordered_parents = sorted(parents, key=_item_number)
        for other in self.capped(ordered_parents, MAX_PARENTS, "parent organizations"):
            label = parents[other]
            yield EntityFinding(
                type=ORG, value=label, properties={"wikidata_id": other, **self.checked()}
            )
            yield RelationshipFinding(
                type=SUBSIDIARY_OF,
                source=subject,
                target=EntityRef(type=ORG, value=label),
                note=PARENT_NOTE,
            )
        ordered_children = sorted(children, key=_item_number)
        for other in self.capped(ordered_children, MAX_SUBSIDIARIES, "subsidiaries"):
            label = children[other]
            yield EntityFinding(
                type=ORG, value=label, properties={"wikidata_id": other, **self.checked()}
            )
            yield RelationshipFinding(
                type=SUBSIDIARY_OF,
                source=EntityRef(type=ORG, value=label),
                target=subject,
                note=PARENT_NOTE,
            )


def _item_number(qid: str) -> int:
    return int(qid[1:])


def _listing(qids: list[str]) -> str:
    ordered = sorted(qids, key=_item_number)
    shown = ordered[:MAX_LISTED]
    if len(ordered) > MAX_LISTED:
        shown.append(f"and {len(ordered) - MAX_LISTED:,} more")
    return ", ".join(shown)


__all__ = [
    "ADMIRALTY",
    "CONFIDENCE",
    "MAX_OWNERS",
    "MAX_PARENTS",
    "MAX_SUBSIDIARIES",
    "MAX_WEBSITES",
    "PARENT_NOTE",
    "REQUEST_BUDGET_SECONDS",
    "TIMEOUT_SECONDS",
    "USAGE_NOTE",
    "WEBSITE_NOTE",
    "WikidataShuttle",
    "is_valid_lei",
    "item_of",
]
