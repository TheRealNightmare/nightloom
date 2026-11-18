"""The crt.sh Shuttle: a domain's subdomains and certificates from Certificate Transparency.

Every publicly trusted TLS certificate is written to public Certificate
Transparency (CT) logs, and `crt.sh <https://crt.sh>`_ indexes them. For the
input domain the Shuttle asks crt.sh (through ``ctx.http`` only) for the
certificates naming the domain itself (``q=<domain>``) and any name under it
(``q=%.<domain>``), and yields:

- **Subdomains.** Every name in those certificates (SANs and common name),
  with a leading ``*.`` stripped, normalized, and kept only if it is the
  input or under it: a Domain and ``HAS_SUBDOMAIN`` from the input (one
  edge from the input, not a parent chain). Names seen as a wildcard
  (``*.dev.example.org``) say ``wildcard: true`` in the relationship's note.
  Email addresses (person data), IP addresses, invalid names and names
  outside the input are dropped and counted. At most
  :data:`MAX_SUBDOMAINS`, most recently certified first.
- **Certificates.** crt.sh's JSON has no fingerprint, so the
  :data:`MAX_CERTIFICATES` most recent certificates (by ``not_before``) are
  downloaded and parsed: a Certificate keyed by its SHA-256 fingerprint,
  with issuer, subject CN, serial, validity, crt.sh ID, SAN count and
  whether it is self-signed, and ``COVERS`` to the kept names it contains
  (at most :data:`MAX_COVERS` edges in all). Older certificates contribute
  names only.

Large domains: an answer over the 5 MB response cap is asked again without
expired certificates (``exclude=expired``, and a job note says so); if it is
still too large the job fails, suggesting a narrower subdomain. crt.sh's
frequent 502/503s and timeouts are retried (see
:mod:`nightloom_shuttle_crtsh.client`) before the job fails with a clear
message. Public suffixes (``co.uk``) and internal-only names are refused
before anything is sent.
"""

import asyncio
import time
from collections import Counter
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
from nightloom_shuttle_crtsh.certificates import (
    CrtShEntry,
    ParsedCertificate,
    parse_certificate,
    parse_entries,
)
from nightloom_shuttle_crtsh.client import (
    Clock,
    CrtShClient,
    CrtShError,
    InvalidResponseError,
    ResultTooLargeError,
    Sleep,
    SourceUnavailableError,
)
from nightloom_shuttle_crtsh.names import DROP_LABELS, DropReason, Name, classify

D = EntityType.DOMAIN
C = EntityType.CERTIFICATE

MAX_SUBDOMAINS = 2_000
MAX_CERTIFICATES = 50
# COVERS edges in all. With at most 2 x MAX_SUBDOMAINS + MAX_CERTIFICATES other
# findings this keeps a run under the worker's 5,000-finding limit.
MAX_COVERS = 900
TIMEOUT_SECONDS = 300
# No new crt.sh request starts this long after the run began, leaving time
# to finish before the job's timeout. Searches get the first part of it.
SEARCH_BUDGET_SECONDS = 180
DOWNLOAD_BUDGET_SECONDS = 250
# Certificate downloads stop after this many failures in a row (crt.sh is down).
MAX_DOWNLOAD_FAILURES_IN_A_ROW = 3

NOTE_EXPIRED_EXCLUDED = "Expired certificates excluded (result too large)."
NOTE_SUBDOMAINS_CAPPED = (
    "Found {found:,} subdomains; kept the {kept:,} most recently certified (the limit)."
)
NOTE_COVERS_CAPPED = "Stopped adding COVERS relationships at {limit:,} (the limit)."
NOTE_DOWNLOADS_STOPPED = (
    "Reached the time limit after {done} of {planned} certificate downloads; "
    "the rest contribute names only."
)
NOTE_DOWNLOADS_FAILED = "{failed} certificate download(s) failed; those contribute names only."
NOTE_DROPPED = "Dropped {total:,} name(s): {details}."
NOTE_MALFORMED = "Ignored {count:,} malformed crt.sh row(s)."
WILDCARD_NOTE = "wildcard: true (certificates name *.{name})"


def _domain(value: str) -> EntityRef:
    return EntityRef(type=D, value=value)


def _certificate_properties(entry: CrtShEntry, parsed: ParsedCertificate) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "issuer": parsed.issuer or None,
        "subject_cn": parsed.subject_cn,
        "serial_number": parsed.serial_number,
        "not_before": parsed.not_before,
        "not_after": parsed.not_after,
        "crtsh_id": entry.id,
        "san_count": parsed.san_count,
        "self_signed": parsed.self_signed,
    }
    return {key: value for key, value in properties.items() if value is not None}


class CrtShShuttle(Shuttle):
    """Domain → subdomains and certificates from CT logs (crt.sh). See the module docstring."""

    meta = ShuttleMeta(
        name="crtsh",
        version="1.0.0",
        title="Certificate Transparency (crt.sh)",
        description=(
            "Finds the domain's subdomains and TLS certificates in public Certificate "
            "Transparency logs, through crt.sh (no API key). Adds every name under the domain "
            "that a certificate was issued for, and the 50 most recent certificates (by "
            "SHA-256 fingerprint) with the names they cover. Names outside the domain, email "
            "addresses and IP addresses in certificates are dropped. Large domains can take a "
            "few minutes; crt.sh is a free service and is asked at most once every 3 seconds."
        ),
        input_types=frozenset({D}),
        output_entity_types=frozenset({D, C}),
        output_relationship_types=frozenset({RelationType.HAS_SUBDOMAIN, RelationType.COVERS}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        # crt.sh is run on donated resources and asks for restraint.
        rate_limit=RateLimit(requests=1, period_seconds=3),
        timeout_seconds=TIMEOUT_SECONDS,
        # crt.sh (operated by Sectigo) publishes no terms of use; its source
        # and issue tracker are at https://github.com/crtsh. See README.md.
        homepage="https://crt.sh/",
        # CT logs are append-only and cryptographically verifiable, but a name
        # in a certificate only shows it was certified, not that it resolves
        # today: usually reliable, probably true.
        default_admiralty="B2",
        default_confidence=0.8,
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


class _Run:
    """One run of the Shuttle: the searches, the names, the certificates."""

    def __init__(
        self, shuttle: CrtShShuttle, ctx: ShuttleContext, entity: EntityInput, scope: str
    ) -> None:
        self.ctx = ctx
        self.entity = entity
        self.scope = scope
        self.clock = shuttle._clock
        self.started = self.clock()
        self.client = CrtShClient(
            shuttle._http or ctx.http,
            sleep=shuttle._sleep,
            clock=self.clock,
            deadline=self.started + SEARCH_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        self.dropped: Counter[DropReason] = Counter()
        self.covers = 0

    # --- searching ---

    async def search(self, query: str) -> list[Any]:
        """One query; asked again without expired certificates if the answer is too large."""
        try:
            return await self.client.search(query)
        except ResultTooLargeError:
            self.ctx.note(NOTE_EXPIRED_EXCLUDED)
            self.ctx.progress("The answer was too large; asking again without expired ones.")
            return await self.client.search(query, exclude_expired=True)

    async def entries(self) -> list[CrtShEntry]:
        self.ctx.progress("Searching Certificate Transparency logs (crt.sh)...")
        rows = [
            *await self.search(self.scope),
            *await self.search(f"%.{self.scope}"),
        ]
        entries, malformed = parse_entries(rows)
        if malformed:
            self.ctx.note(NOTE_MALFORMED.format(count=malformed))
        return entries

    def names(self, entries: list[CrtShEntry]) -> dict[str, bool]:
        """Kept names (most recently certified first) and whether each was seen as a wildcard."""
        names: dict[str, bool] = {}
        for entry in entries:
            for raw in entry.identities():
                kept = classify(raw, self.scope)
                if isinstance(kept, DropReason):
                    self.dropped[kept] += 1
                else:
                    names[kept.value] = names.get(kept.value, False) or kept.wildcard
        return names

    def target(self, name: str) -> EntityRef:
        """The input itself for its own name, else the subdomain."""
        return self.entity.ref if name == self.scope else _domain(name)

    # --- findings ---

    async def findings(self) -> AsyncIterator[Finding]:
        entries = await self.entries()
        names = self.names(entries)
        subdomains = [name for name in names if name != self.scope]
        if len(subdomains) > MAX_SUBDOMAINS:
            self.ctx.note(NOTE_SUBDOMAINS_CAPPED.format(found=len(subdomains), kept=MAX_SUBDOMAINS))
            subdomains = subdomains[:MAX_SUBDOMAINS]
        self.ctx.progress(
            f"{len(entries):,} certificates, {len(subdomains):,} subdomains; adding them."
        )
        for name in subdomains:
            yield EntityFinding(type=D, value=name)
            yield RelationshipFinding(
                type=RelationType.HAS_SUBDOMAIN,
                source=self.entity.ref,
                target=_domain(name),
                note=WILDCARD_NOTE.format(name=name) if names[name] else None,
            )
        coverable = {*subdomains, self.scope}
        async for finding in self.certificates(entries[:MAX_CERTIFICATES], coverable):
            yield finding
        self.report()

    async def certificates(
        self, recent: list[CrtShEntry], coverable: set[str]
    ) -> AsyncIterator[Finding]:
        downloaded = failed = failed_in_a_row = 0
        for index, entry in enumerate(recent):
            self.ctx.raise_if_cancelled()
            if self.clock() - self.started >= DOWNLOAD_BUDGET_SECONDS:
                self.ctx.note(NOTE_DOWNLOADS_STOPPED.format(done=index, planned=len(recent)))
                break
            if failed_in_a_row >= MAX_DOWNLOAD_FAILURES_IN_A_ROW:
                failed += len(recent) - index
                break
            self.ctx.progress(f"Downloading certificate {index + 1} of {len(recent)}.")
            self.client.extend_deadline(self.started + DOWNLOAD_BUDGET_SECONDS)
            try:
                parsed = parse_certificate(await self.client.download(entry.id))
            except (CrtShError, ValueError) as error:
                # Only the type: never the response or the ID's content.
                self.ctx.logger.warning("certificate download failed: %s", type(error).__name__)
                failed += 1
                failed_in_a_row += 1
                continue
            failed_in_a_row = 0
            downloaded += 1
            for finding in self.certificate(entry, parsed, coverable):
                yield finding
        if failed:
            self.ctx.note(NOTE_DOWNLOADS_FAILED.format(failed=failed))
        self.ctx.progress(f"Downloaded {downloaded} certificate(s).")

    def certificate(
        self, entry: CrtShEntry, parsed: ParsedCertificate, coverable: set[str]
    ) -> list[Finding]:
        findings: list[Finding] = [
            EntityFinding(
                type=C,
                value=parsed.fingerprint,
                properties=_certificate_properties(entry, parsed),
                # Downloaded and parsed here, fingerprint computed locally.
                admiralty="B1",
                confidence=0.95,
            )
        ]
        covered: dict[str, bool] = {}
        for raw in parsed.names:
            kept = classify(raw, self.scope)
            if isinstance(kept, Name) and kept.value in coverable:
                covered[kept.value] = covered.get(kept.value, False) or kept.wildcard
        for name, wildcard in covered.items():
            if self.covers >= MAX_COVERS:
                self.ctx.note(NOTE_COVERS_CAPPED.format(limit=MAX_COVERS))
                break
            self.covers += 1
            findings.append(
                RelationshipFinding(
                    type=RelationType.COVERS,
                    source=EntityRef(type=C, value=parsed.fingerprint),
                    target=self.target(name),
                    note=WILDCARD_NOTE.format(name=name) if wildcard else None,
                )
            )
        return findings

    def report(self) -> None:
        total = sum(self.dropped.values())
        if total:
            details = ", ".join(
                f"{count:,} {DROP_LABELS[reason]}"
                for reason, count in sorted(self.dropped.items(), key=lambda item: item[0].value)
            )
            self.ctx.note(NOTE_DROPPED.format(total=total, details=details))
        self.ctx.progress(
            f"{self.client.requests} request(s) to crt.sh, {self.client.retries} retried."
        )


__all__ = [
    "DOWNLOAD_BUDGET_SECONDS",
    "MAX_CERTIFICATES",
    "MAX_COVERS",
    "MAX_SUBDOMAINS",
    "SEARCH_BUDGET_SECONDS",
    "CrtShShuttle",
]
