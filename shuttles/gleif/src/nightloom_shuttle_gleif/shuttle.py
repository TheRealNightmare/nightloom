"""The GLEIF Shuttle: an organization's Legal Entity Identifier record and parents.

The Global Legal Entity Identifier Foundation publishes every LEI (ISO
17442) with its reference data and, where reported, the entity's parent
companies, free and without a key under CC0
(https://www.gleif.org/en/lei-data/gleif-api). The Shuttle reads it through
``ctx.http`` only (:mod:`nightloom_shuttle_gleif.client`), never contacting
the organization, and finds the record in one of three ways:

1. the input Organization has an ``lei`` property: that LEI;
2. the input's name *is* an LEI (``5493001KJTIIGC8Y1R12``): that LEI, and the
   organization is added under its legal name, the input keeping ``lei``;
3. otherwise by legal name: GLEIF is asked for records with that legal
   name, and only those whose legal or other name equals the input
   (ignoring case and spacing) count. None is a note; several are a note
   listing their LEIs (add one as an organization to choose it), and
   nothing is added, because one name can belong to unrelated companies.
   Sole proprietors' records are removed first, so a person's LEI is
   never listed.

From the record it yields:

- the Organization with ``lei``, ``legal_name``, ``jurisdiction``,
  ``legal_form``, ``registered_as``, ``entity_status``, ``lei_status``,
  ``country`` (of the legal address; no street address is ever kept) and
  ``gleif_checked_at``;
- its direct and ultimate parents, as reported to GLEIF, as Organizations
  with ``SUBSIDIARY_OF`` from it (note ``direct parent``, ``ultimate
  parent`` or both);
- its direct subsidiaries (at most :data:`MAX_CHILDREN`) as Organizations
  with ``SUBSIDIARY_OF`` to it.

**People.** An LEI can belong to a sole proprietor: a natural person in
business on their own account. Such a record is never kept, whether it is
the match or a parent or subsidiary; the job says so in a note.

Provenance: the record is registered by the entity and validated by its
LEI issuer, **A2** (confidence 0.9); parent relationships are reported by
the entity with varying corroboration, **B2** (0.8).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import text_key
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
from nightloom_shuttle_gleif.client import (
    Clock,
    GleifClient,
    InvalidResponseError,
    Sleep,
    SourceUnavailableError,
    is_valid_lei,
)
from nightloom_shuttle_gleif.records import InvalidRecordError, LeiRecord, parse_record

ORG = EntityType.ORGANIZATION
SUBSIDIARY_OF = RelationType.SUBSIDIARY_OF

TIMEOUT_SECONDS = 60
# No new request starts this long after the run began.
REQUEST_BUDGET_SECONDS = 45
# Records asked for when searching by name (a name rarely has more).
MAX_MATCHES = 10
MAX_CHILDREN = 50
# LEIs listed in the note when a name matches several records.
MAX_LISTED = 5

ADMIRALTY, CONFIDENCE = "A2", 0.9
PARENT_ADMIRALTY, PARENT_CONFIDENCE = "B2", 0.8

DIRECT_PARENT = "direct parent"
ULTIMATE_PARENT = "ultimate parent"
BOTH_PARENTS = "direct and ultimate parent"

USAGE_NOTE = "GLEIF's LEI data is free to use under CC0; no key is needed."

NOTE_NO_MATCH = (
    "GLEIF has no LEI record with this exact legal name. Use the full registered name "
    '(for example "Example Holdings Limited"), or add the LEI itself as an organization.'
)
NOTE_SEVERAL = (
    "{count:,} LEI records have this name, so nothing was added: {listed}. Add the LEI "
    "you mean as an organization and run this Shuttle on it."
)
NOTE_UNKNOWN_LEI = "GLEIF has no record of the LEI {lei}."
NOTE_BAD_LEI = "The organization's lei property is not a valid LEI; looked it up by name instead."
NOTE_BY_LEI = "Looked up the LEI {lei}: added the organization as {name}."
NOTE_SOLE_PROPRIETOR = (
    "This LEI belongs to a sole proprietor (a person in business on their own account), "
    "so nothing was kept."
)
NOTE_CHILDREN_CAPPED = "GLEIF lists {total:,} direct subsidiaries; added the first {limit:,}."
NOTE_SKIPPED_PEOPLE = "Skipped {count:,} LEI record(s) of sole proprietors (people)."
NOTE_MALFORMED = "Ignored {count:,} malformed record(s) in GLEIF's answer."

Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def lei_of(text: object) -> str | None:
    """``text`` as an LEI if it is one (trimmed, upper-cased, check digits correct)."""
    if not isinstance(text, str):
        return None
    candidate = text.strip().upper()
    return candidate if is_valid_lei(candidate) else None


class GleifShuttle(Shuttle):
    """An organization's LEI record, parents and subsidiaries. See the module docstring."""

    meta = ShuttleMeta(
        name="gleif",
        version="1.0.0",
        title="Legal entity record (GLEIF)",
        description=(
            "Looks up an organization's Legal Entity Identifier (LEI) record at GLEIF (free, no "
            "API key): by its exact legal name, by an LEI given as its name, or by its lei "
            "property. Adds the LEI, legal name, jurisdiction, legal form, business register "
            "number, statuses and country, and the parent companies and direct subsidiaries "
            "reported to GLEIF, linked with SUBSIDIARY_OF. Never keeps street addresses, and "
            "never keeps the LEI record of a sole proprietor (a person)."
        ),
        input_types=frozenset({ORG}),
        output_entity_types=frozenset({ORG}),
        output_relationship_types=frozenset({SUBSIDIARY_OF}),
        # It reads GLEIF's register; nothing is sent to the organization.
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.ORGANIZATION,
        network=True,
        # GLEIF allows 60 requests a minute.
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        # A day: GLEIF publishes its data several times a day, but LEI records change rarely.
        cache_ttl_seconds=24 * 3600,
        # Organizations are never auto-pivot seeds; keep it out of pivots explicitly.
        pivots=False,
        homepage="https://www.gleif.org/en/lei-data/gleif-api",
        terms_url="https://www.gleif.org/en/meta/lei-data-terms-of-use",
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
            async for finding in run.findings():
                yield finding
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except SourceUnavailableError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        finally:
            run.report()


class _Run:
    """One run of the Shuttle on one Organization."""

    def __init__(self, shuttle: GleifShuttle, ctx: ShuttleContext, entity: EntityInput) -> None:
        self.ctx = ctx
        self.entity = entity
        self.now = shuttle._now
        self.client = GleifClient(
            shuttle._http or ctx.http,
            sleep=shuttle._sleep,
            clock=shuttle._clock,
            deadline=shuttle._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        self.malformed = 0
        self.people = 0

    def report(self) -> None:
        if self.people:
            self.ctx.note(NOTE_SKIPPED_PEOPLE.format(count=self.people))
        if self.malformed:
            self.ctx.note(NOTE_MALFORMED.format(count=self.malformed))
        self.ctx.progress(
            f"{self.client.requests} GLEIF request(s), {self.client.retries} retried, "
            f"{self.client.rate_limited} rate-limited."
        )

    # --- finding the record ---

    async def findings(self) -> AsyncIterator[Finding]:
        record = await self.find()
        if record is None:
            return
        if record.is_sole_proprietor:
            self.ctx.note(NOTE_SOLE_PROPRIETOR)
            return
        checked = {"gleif_checked_at": self.now()}

        named_by_lei = lei_of(self.entity.value) == record.lei
        if named_by_lei:
            # The input's name is the LEI: the organization goes in under its legal name.
            yield EntityFinding(type=ORG, value=self.entity.value, properties={"lei": record.lei})
            self.ctx.note(NOTE_BY_LEI.format(lei=record.lei, name=record.legal_name))
            subject = EntityRef(type=ORG, value=record.legal_name)
        else:
            subject = self.entity.ref
        yield EntityFinding(
            type=ORG, value=subject.value, properties={**record.properties(), **checked}
        )

        async for finding in self.parents(record, subject, checked):
            yield finding
        async for finding in self.children(record, subject, checked):
            yield finding

    async def find(self) -> LeiRecord | None:
        """The input's LEI record, or ``None`` (with a note) when there is no single one."""
        given = self.entity.properties.get("lei")
        lei = lei_of(given)
        if given is not None and lei is None:
            self.ctx.note(NOTE_BAD_LEI)
        lei = lei or lei_of(self.entity.value)
        if lei is not None:
            self.ctx.progress(f"Asking GLEIF for the LEI {lei}.")
            resource = await self.client.record(lei)
            if resource is None:
                self.ctx.note(NOTE_UNKNOWN_LEI.format(lei=lei))
                return None
            record = self.parse(resource)
            if record is None or record.lei != lei:
                raise InvalidResponseError("the record is not the one asked for")
            return record

        self.ctx.progress("Asking GLEIF for records with this legal name.")
        page = await self.client.search(self.entity.value, size=MAX_MATCHES)
        matches: dict[str, LeiRecord] = {}
        for resource in page.records:
            record = self.parse(resource)
            if record is not None and record.is_named(self.entity.value):
                matches.setdefault(record.lei, record)
        # A sole proprietor's record is a person's: never chosen, listed or kept.
        people = [lei for lei, record in matches.items() if record.is_sole_proprietor]
        if people and len(people) == len(matches):
            self.ctx.note(NOTE_SOLE_PROPRIETOR)
            return None
        for lei in people:
            del matches[lei]
        self.people += len(people)
        if not matches:
            self.ctx.note(NOTE_NO_MATCH)
            return None
        if len(matches) > 1:
            self.ctx.note(
                NOTE_SEVERAL.format(count=len(matches), listed=_listing(matches.values()))
            )
            return None
        (record,) = matches.values()
        return record

    def parse(self, resource: Any) -> LeiRecord | None:
        try:
            return parse_record(resource)
        except InvalidRecordError:
            self.malformed += 1
            return None

    def related(self, resource: Any) -> LeiRecord | None:
        """A parent's or subsidiary's record, unless malformed or a sole proprietor's."""
        record = self.parse(resource)
        if record is not None and record.is_sole_proprietor:
            self.people += 1
            return None
        return record

    # --- parents and subsidiaries ---

    async def parents(
        self, record: LeiRecord, subject: EntityRef, checked: dict[str, Any]
    ) -> AsyncIterator[Finding]:
        self.ctx.progress("Asking GLEIF for the parent companies.")
        notes: dict[str, list[str]] = {}
        found: dict[str, LeiRecord] = {}
        for ultimate, note in ((False, DIRECT_PARENT), (True, ULTIMATE_PARENT)):
            resource = await self.client.parent(record.lei, ultimate=ultimate)
            parent = None if resource is None else self.related(resource)
            if parent is None or parent.lei == record.lei or _same_name(parent, subject):
                continue
            found.setdefault(parent.lei, parent)
            notes.setdefault(parent.lei, []).append(note)
        for lei, parent in found.items():
            note = BOTH_PARENTS if len(notes[lei]) == 2 else notes[lei][0]
            yield EntityFinding(
                type=ORG, value=parent.legal_name, properties={**parent.properties(), **checked}
            )
            yield RelationshipFinding(
                type=SUBSIDIARY_OF,
                source=subject,
                target=EntityRef(type=ORG, value=parent.legal_name),
                admiralty=PARENT_ADMIRALTY,
                confidence=PARENT_CONFIDENCE,
                note=note,
            )

    async def children(
        self, record: LeiRecord, subject: EntityRef, checked: dict[str, Any]
    ) -> AsyncIterator[Finding]:
        self.ctx.progress("Asking GLEIF for the direct subsidiaries.")
        page = await self.client.children(record.lei, size=MAX_CHILDREN)
        if page.total > MAX_CHILDREN:
            self.ctx.note(NOTE_CHILDREN_CAPPED.format(total=page.total, limit=MAX_CHILDREN))
        seen: set[str] = {record.lei}
        for resource in page.records[:MAX_CHILDREN]:
            child = self.related(resource)
            if child is None or child.lei in seen or _same_name(child, subject):
                continue
            seen.add(child.lei)
            yield EntityFinding(
                type=ORG, value=child.legal_name, properties={**child.properties(), **checked}
            )
            yield RelationshipFinding(
                type=SUBSIDIARY_OF,
                source=EntityRef(type=ORG, value=child.legal_name),
                target=subject,
                admiralty=PARENT_ADMIRALTY,
                confidence=PARENT_CONFIDENCE,
                note=DIRECT_PARENT,
            )


def _same_name(record: LeiRecord, subject: EntityRef) -> bool:
    """Whether ``record`` would become the same Organization as ``subject`` (no self-loops)."""
    return text_key(record.legal_name) == text_key(subject.value)


def _listing(records: Iterable[LeiRecord]) -> str:
    """``LEI (jurisdiction)`` for the first :data:`MAX_LISTED` records, by LEI."""
    ordered = sorted(records, key=lambda record: record.lei)
    shown = [
        f"{record.lei} ({record.jurisdiction})" if record.jurisdiction else record.lei
        for record in ordered[:MAX_LISTED]
    ]
    if len(ordered) > MAX_LISTED:
        shown.append(f"and {len(ordered) - MAX_LISTED:,} more")
    return ", ".join(shown)


__all__ = [
    "ADMIRALTY",
    "BOTH_PARENTS",
    "CONFIDENCE",
    "DIRECT_PARENT",
    "MAX_CHILDREN",
    "MAX_LISTED",
    "MAX_MATCHES",
    "PARENT_ADMIRALTY",
    "PARENT_CONFIDENCE",
    "REQUEST_BUDGET_SECONDS",
    "TIMEOUT_SECONDS",
    "ULTIMATE_PARENT",
    "USAGE_NOTE",
    "GleifShuttle",
    "lei_of",
]
