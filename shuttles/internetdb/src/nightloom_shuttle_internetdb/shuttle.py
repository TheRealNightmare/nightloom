"""The InternetDB Shuttle: what Shodan's scans saw open on an IPv4 address.

Shodan's InternetDB (https://internetdb.shodan.io/) is a free summary of
Shodan's own Internet-wide scans, updated about weekly: per IPv4 address,
the open ports, the software identified (CPE names), vulnerabilities Shodan
associates with that software (CVE IDs), tags and hostnames. The Shuttle
reads that record through ``ctx.http`` only
(:mod:`nightloom_shuttle_internetdb.client`); it never contacts the address
itself, which is why it is passive. It yields:

- the input IPAddress again, with ``open_ports`` (ascending), ``cpes``,
  ``vulns`` (CVE IDs only, ascending), ``tags`` and ``internetdb_checked_at``;
- each open port as a Service ``<ip>:<port>/tcp`` with ``RUNS_ON`` to the
  address (at most :data:`MAX_PORTS`);
- each hostname as a Domain with ``RESOLVES_TO`` to the address (at most
  :data:`MAX_HOSTNAMES`), rated lower (**B3**, confidence 0.6) because
  Shodan saw the name once and it may not resolve there now. Invalid and
  internal-looking names are dropped and counted.

CPEs and CVEs are properties, not entities: they describe what Shodan
inferred about this one address, are reported by software version rather
than verified, and would otherwise add hundreds of nodes per address.

Addresses InternetDB cannot know are skipped with a job note and no
request: IPv6 (InternetDB covers IPv4 only) and addresses that are not
publicly routable (the SDK's ``is_public_address``). A 404 (*No information
available*) is a note, not an error.

Provenance: **B2** (confidence 0.8) for ports, CPEs, vulnerabilities and
tags: Shodan's own scans are a usually reliable source, probably true, but
up to a week old. Hostnames **B3** (0.6).
"""

import asyncio
import ipaddress
import re
import time
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import httpx

from nightloom_core.entities import (
    EXPOSURE_MAX_CPES,
    EXPOSURE_MAX_PORTS,
    EXPOSURE_MAX_TAGS,
    EXPOSURE_MAX_VULNS,
    EntityType,
    Sensitivity,
)
from nightloom_core.normalize import NormalizationError, normalize, registrable_domain
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
from nightloom_sdk.http import is_public_address
from nightloom_shuttle_internetdb.client import (
    Clock,
    InternetDBClient,
    InvalidResponseError,
    Sleep,
    SourceUnavailableError,
)
from nightloom_shuttle_internetdb.models import Record

IP = EntityType.IP_ADDRESS
SERVICE = EntityType.SERVICE
DOMAIN = EntityType.DOMAIN

TIMEOUT_SECONDS = 60
# No new request starts this long after the run began.
REQUEST_BUDGET_SECONDS = 45
MAX_PORTS = EXPOSURE_MAX_PORTS  # 200
MAX_HOSTNAMES = 100
MAX_CPES = EXPOSURE_MAX_CPES  # 100
MAX_VULNS = EXPOSURE_MAX_VULNS  # 500
MAX_TAGS = EXPOSURE_MAX_TAGS  # 50
MAX_TEXT_LENGTH = 200
MAX_CPE_LENGTH = 300

ADMIRALTY, CONFIDENCE = "B2", 0.8
HOSTNAME_ADMIRALTY, HOSTNAME_CONFIDENCE = "B3", 0.6
HOSTNAME_NOTE = "hostname reported by Shodan; may not resolve to this address now"

CVE_PATTERN = re.compile(r"^CVE-\d{4}-\d{4,}$")
# Reverse-DNS names (in-addr.arpa, ip6.arpa), home.arpa and Tor names have a
# "registrable" part in the Public Suffix List but are never public hosts.
INTERNAL_SUFFIXES = (".arpa", ".onion")

USAGE_NOTE = (
    "Free for non-commercial use. Building a paid product on this data requires a Shodan license."
)

NOTE_IPV6 = "InternetDB covers IPv4 addresses only."
NOTE_NOT_PUBLIC = (
    "This address is not publicly routable, so InternetDB has no data on it. Nothing was sent."
)
NOTE_NO_INFORMATION = "InternetDB has no information on this address."
NOTE_PORTS_CAPPED = "InternetDB listed {count:,} open ports; kept the lowest {limit:,}."
NOTE_HOSTNAMES_CAPPED = "InternetDB listed {count:,} hostnames; kept {limit:,} (alphabetically)."
NOTE_CAPPED = "InternetDB listed {count:,} {what}; kept {limit:,}."
NOTE_HOSTNAMES_DROPPED = (
    "Dropped {invalid:,} invalid and {internal:,} internal-looking hostname(s)."
)
NOTE_NOT_CVE = "Ignored {count:,} vulnerability ID(s) that are not CVE IDs."
NOTE_MALFORMED = "Ignored {count:,} malformed item(s) in InternetDB's answer."

AddressFilter = Callable[[ipaddress.IPv4Address], bool]
Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _cve_order(cve: str) -> tuple[int, int]:
    _, year, number = cve.split("-")
    return int(year), int(number)


class InternetDBShuttle(Shuttle):
    """Open ports and exposures of an IPv4 address. See the module docstring."""

    meta = ShuttleMeta(
        name="internetdb",
        version="1.0.0",
        title="Open ports and exposures (Shodan InternetDB)",
        description=(
            "Looks up what Shodan's own scans saw on a public IPv4 address, from Shodan's free "
            "InternetDB (no API key; updated about weekly). Never contacts the address itself. "
            "Adds each open port as a Service, the hostnames Shodan saw as Domains, and the "
            "identified software (CPEs), known vulnerabilities (CVE IDs) and tags as properties "
            "of the address."
        ),
        input_types=frozenset({IP}),
        # IPAddress: the record's ports, CPEs, CVEs and tags are added to the input itself.
        output_entity_types=frozenset({SERVICE, DOMAIN, IP}),
        output_relationship_types=frozenset({RelationType.RUNS_ON, RelationType.RESOLVES_TO}),
        # It reads Shodan's existing data; nothing is sent to the address looked up.
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        homepage="https://internetdb.shodan.io/",
        terms_url="https://account.shodan.io/legal/tos",
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
        address_filter: AddressFilter = is_public_address,
    ) -> None:
        """``http``, ``sleep``, ``clock`` and ``now`` replace the real ones in tests.

        ``address_filter`` decides which addresses are worth asking about
        (default: publicly routable ones). Tests widen it to the
        documentation ranges their fixtures use. A given ``http`` client
        stays open: whoever made it closes it.
        """
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self._address_filter = address_filter

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        address = ipaddress.ip_address(entity.value)
        if not isinstance(address, ipaddress.IPv4Address):
            ctx.note(NOTE_IPV6)
            return
        if not self._address_filter(address):
            ctx.note(NOTE_NOT_PUBLIC)
            return
        run = _Run(self, ctx, entity, address)
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
    """One run of the Shuttle on one public IPv4 address."""

    def __init__(
        self,
        shuttle: InternetDBShuttle,
        ctx: ShuttleContext,
        entity: EntityInput,
        address: ipaddress.IPv4Address,
    ) -> None:
        self.ctx = ctx
        self.entity = entity
        self.address = address
        self.now = shuttle._now
        self.client = InternetDBClient(
            shuttle._http or ctx.http,
            sleep=shuttle._sleep,
            clock=shuttle._clock,
            deadline=shuttle._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        self.malformed = 0
        self.not_cve = 0
        self.invalid_hostnames = 0
        self.internal_hostnames = 0

    def report(self) -> None:
        if self.invalid_hostnames or self.internal_hostnames:
            self.ctx.note(
                NOTE_HOSTNAMES_DROPPED.format(
                    invalid=self.invalid_hostnames, internal=self.internal_hostnames
                )
            )
        if self.not_cve:
            self.ctx.note(NOTE_NOT_CVE.format(count=self.not_cve))
        if self.malformed:
            self.ctx.note(NOTE_MALFORMED.format(count=self.malformed))
        self.ctx.progress(
            f"{self.client.requests} InternetDB request(s), {self.client.retries} retried, "
            f"{self.client.rate_limited} rate-limited."
        )

    async def findings(self) -> AsyncIterator[Finding]:
        ip = str(self.address)
        self.ctx.progress("Asking Shodan InternetDB.")
        record = await self.client.get(ip)
        if record is None:
            self.ctx.note(NOTE_NO_INFORMATION)
            return
        if record.ip.strip() != ip:
            raise InvalidResponseError("the record is for another address")

        ports = self.ports(record)
        yield EntityFinding(
            type=IP, value=self.entity.value, properties=self.properties(record, ports)
        )

        for port in ports:
            service = f"{ip}:{port}/tcp"
            yield EntityFinding(type=SERVICE, value=service)
            yield RelationshipFinding(
                type=RelationType.RUNS_ON,
                source=EntityRef(type=SERVICE, value=service),
                target=self.entity.ref,
            )

        for name in self.hostnames(record.hostnames):
            yield EntityFinding(
                type=DOMAIN,
                value=name,
                admiralty=HOSTNAME_ADMIRALTY,
                confidence=HOSTNAME_CONFIDENCE,
            )
            yield RelationshipFinding(
                type=RelationType.RESOLVES_TO,
                source=EntityRef(type=DOMAIN, value=name),
                target=self.entity.ref,
                admiralty=HOSTNAME_ADMIRALTY,
                confidence=HOSTNAME_CONFIDENCE,
                note=HOSTNAME_NOTE,
            )

    # --- the record's lists ---

    def ports(self, record: Record) -> list[int]:
        """Distinct valid TCP ports, ascending, at most :data:`MAX_PORTS`."""
        ports: set[int] = set()
        for value in record.ports:
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
                ports.add(value)
            else:
                self.malformed += 1
        ordered = sorted(ports)
        if len(ordered) > MAX_PORTS:
            self.ctx.note(NOTE_PORTS_CAPPED.format(count=len(ordered), limit=MAX_PORTS))
        return ordered[:MAX_PORTS]

    def texts(self, values: Iterable[object], *, max_length: int = MAX_TEXT_LENGTH) -> list[str]:
        """Distinct non-empty strings, trimmed, in the order given; others are counted."""
        kept: list[str] = []
        for value in values:
            text = value.strip() if isinstance(value, str) else ""
            if not text or len(text) > max_length or any(ord(c) < 32 for c in text):
                self.malformed += 1
            elif text not in kept:
                kept.append(text)
        return kept

    def capped(self, values: list[str], limit: int, what: str) -> list[str]:
        if len(values) > limit:
            self.ctx.note(NOTE_CAPPED.format(count=len(values), what=what, limit=limit))
        return values[:limit]

    def properties(self, record: Record, ports: list[int]) -> dict[str, Any]:
        """The input address's properties. Empty lists are kept: they replace an older
        observation's values, so the address shows what InternetDB says now."""
        cpes: list[str] = []
        for text in self.texts(record.cpes, max_length=MAX_CPE_LENGTH):
            if text.startswith("cpe:"):
                cpes.append(text)
            else:
                self.malformed += 1
        vulns: list[str] = []
        for text in self.texts(record.vulns):
            if CVE_PATTERN.fullmatch(text):
                vulns.append(text)
            else:
                self.not_cve += 1
        vulns.sort(key=_cve_order)
        return {
            "open_ports": ports,
            "cpes": self.capped(cpes, MAX_CPES, "CPEs"),
            "vulns": self.capped(vulns, MAX_VULNS, "vulnerabilities"),
            "tags": self.capped(self.texts(record.tags), MAX_TAGS, "tags"),
            "internetdb_checked_at": self.now(),
        }

    def hostnames(self, values: Iterable[object]) -> list[str]:
        """Distinct valid public-looking hostnames, normalized, alphabetical, capped."""
        names: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value.strip() or "*" in value:
                self.invalid_hostnames += 1
                continue
            try:
                name = normalize(DOMAIN, value.strip()).value
            except NormalizationError:
                self.invalid_hostnames += 1
                continue
            if registrable_domain(name) is None or name.endswith(INTERNAL_SUFFIXES):
                self.internal_hostnames += 1
                continue
            names.add(name)
        ordered = sorted(names)
        if len(ordered) > MAX_HOSTNAMES:
            self.ctx.note(NOTE_HOSTNAMES_CAPPED.format(count=len(ordered), limit=MAX_HOSTNAMES))
        return ordered[:MAX_HOSTNAMES]


__all__ = [
    "ADMIRALTY",
    "CONFIDENCE",
    "HOSTNAME_ADMIRALTY",
    "HOSTNAME_CONFIDENCE",
    "HOSTNAME_NOTE",
    "MAX_CPES",
    "MAX_HOSTNAMES",
    "MAX_PORTS",
    "MAX_TAGS",
    "MAX_VULNS",
    "REQUEST_BUDGET_SECONDS",
    "TIMEOUT_SECONDS",
    "USAGE_NOTE",
    "InternetDBShuttle",
]
