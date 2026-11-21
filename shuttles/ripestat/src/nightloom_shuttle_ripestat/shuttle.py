"""The RIPEstat Shuttle: which prefix and AS route an address, and who holds the AS.

RIPEstat (https://stat.ripe.net/) is the RIPE NCC's public data service. Its
routing data comes from RIS, route collectors that peer with hundreds of
networks worldwide and record the BGP announcements they see. The Shuttle
asks the Data API through ``ctx.http`` only
(:mod:`nightloom_shuttle_ripestat.client`) and yields:

- **IPAddress.** ``network-info``: the most specific announced prefix
  covering the address, as a Netblock (``announced: true``) with ``WITHIN``
  from the address, and its origin ASes with ``ANNOUNCED_BY`` from the
  prefix. Then ``as-overview`` for each origin AS (at most
  :data:`MAX_HOLDER_LOOKUPS`): ``as_name``, ``announced`` and the IANA block
  on the ASN, and the holder as an Organization with ``OWNS``. An address
  nobody announces is a job note, not an error.
- **Netblock.** ``prefix-overview``: ``announced`` and the IANA block on the
  input, its origin ASes with ``ANNOUNCED_BY`` and their holders as
  Organizations with ``OWNS``. When the input is not announced itself but a
  less specific prefix covering it is, that prefix is added too, with
  ``WITHIN`` from the input, and carries the ``ANNOUNCED_BY`` edges.
- **ASN.** ``as-overview``: ``as_name``, ``announced`` and the IANA block on
  the ASN, and the holder as an Organization with ``OWNS``. Then
  ``announced-prefixes``: every prefix it originated in RIPEstat's default
  period (the last two weeks) as a Netblock with ``ANNOUNCED_BY`` and
  ``bgp_first_seen`` / ``bgp_last_seen``, at most :data:`MAX_PREFIXES`, in a
  stable order (shortest prefix first, then IPv4 before IPv6, then address).

Holders (``"CLOUDFLARENET - Cloudflare, Inc."``) are split by
:func:`~nightloom_shuttle_ripestat.holders.parse_holder`. Malformed items in
an answer are skipped and counted in a job note.

Provenance: **B2** (confidence 0.8). RIS is a usually reliable observer of
the global routing table, and what it saw is probably true, but routing
changes all the time and a collector's view is not the whole Internet.
"""

import asyncio
import ipaddress
import time
from collections.abc import AsyncIterator, Iterable, Mapping
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from nightloom_core.entities import EntityType, Sensitivity
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
from nightloom_shuttle_ripestat.client import (
    REQUEST_TIMEOUT,
    SLOW_REQUEST_TIMEOUT,
    Clock,
    InvalidResponseError,
    RipeStatClient,
    RipeStatError,
    Sleep,
    SourceUnavailableError,
)
from nightloom_shuttle_ripestat.holders import Holder, parse_holder
from nightloom_shuttle_ripestat.models import (
    AnnouncedPrefix,
    AnnouncedPrefixes,
    AsOverview,
    Block,
    NetworkInfo,
    PrefixOrigin,
    PrefixOverview,
)

IP = EntityType.IP_ADDRESS
NB = EntityType.NETBLOCK
AS = EntityType.ASN
ORG = EntityType.ORGANIZATION

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

TIMEOUT_SECONDS = 120
# No new request starts this long after the run began.
REQUEST_BUDGET_SECONDS = 100
# as-overview lookups for an address's origin ASes (more is a MOAS oddity).
MAX_HOLDER_LOOKUPS = 5
# Netblocks from announced-prefixes; 2 findings each keeps a run far below 5,000.
MAX_PREFIXES = 500

ADMIRALTY, CONFIDENCE = "B2", 0.8

NOTE_IP_NOT_ANNOUNCED = "This address is not announced in the global routing table."
NOTE_NETBLOCK_NOT_ANNOUNCED = "This netblock is not announced in the global routing table."
NOTE_AS_NOT_ANNOUNCED = "This AS announced no prefixes in the global routing table recently."
NOTE_COVERED = (
    "The input is not announced as a prefix of its own; added the less specific announced "
    "prefix that covers it."
)
NOTE_MOAS = "Announced by {count} origin ASes at once (MOAS)."
NOTE_HOLDERS_CAPPED = "Looked up the holders of the first {limit} of {count} origin ASes."
NOTE_HOLDERS_FAILED = "Could not look up the holder of {count} origin AS(es)."
NOTE_PREFIXES_CAPPED = (
    "The AS announced {count:,} prefixes; kept {limit:,} (shortest first, then by address)."
)
NOTE_MALFORMED = "Ignored {count:,} malformed item(s) in RIPEstat's answers."
NOTE_REDACTED = "Skipped {count:,} holder name(s) that looked redacted."


def _network(value: object) -> Network | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def _asn(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().upper().removeprefix("AS").isdigit():
        number = int(value.strip().upper().removeprefix("AS"))
    else:
        return None
    return number if 1 <= number <= 2**32 - 1 else None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned if 0 < len(cleaned) <= 200 else None


def _block(block: Block | None) -> dict[str, Any]:
    if block is None:
        return {}
    return {"iana_block": _text(block.resource), "iana_block_description": _text(block.desc)}


def _drop_empty(properties: Mapping[str, Any]) -> dict[str, Any] | None:
    kept = {key: value for key, value in properties.items() if value not in (None, [], "")}
    return kept or None


def _prefix_order(network: Network) -> tuple[int, int, int]:
    return (network.prefixlen, network.version, int(network.network_address))


class RipeStatShuttle(Shuttle):
    """Routing data for an IP address, netblock or ASN. See the module docstring."""

    meta = ShuttleMeta(
        name="ripestat",
        version="1.0.0",
        title="Routing and ASN (RIPEstat)",
        description=(
            "Looks up how an address, netblock or AS number is routed on the Internet, from "
            "the RIPE NCC's RIPEstat Data API (observations of RIS route collectors; no API "
            "key). For an IP address: the announced prefix that covers it and the ASes that "
            "originate it. For a netblock: whether it is announced, by which ASes, and the "
            "covering prefix when only a larger one is. For an ASN: its holder and the "
            "prefixes it announces (at most 500). AS holders become Organizations."
        ),
        input_types=frozenset({IP, NB, AS}),
        # ASN and Netblock: a lookup adds routing data to the input itself.
        output_entity_types=frozenset({NB, AS, ORG}),
        output_relationship_types=frozenset(
            {RelationType.WITHIN, RelationType.ANNOUNCED_BY, RelationType.OWNS}
        ),
        mode=Mode.PASSIVE,
        # Organization is an organization-sensitivity type; no person-related types.
        sensitivity=Sensitivity.ORGANIZATION,
        network=True,
        # RIPEstat sets no request limit, but allows 8 concurrent requests per IP
        # and asks regular users to send a sourceapp; 2 a second is polite.
        rate_limit=RateLimit(requests=2, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        # A day: routing and allocation data changes slowly.
        cache_ttl_seconds=24 * 3600,
        homepage="https://stat.ripe.net/",
        terms_url="https://www.ripe.net/about-us/legal/ripestat-service-terms-and-conditions/",
        # RIS collectors are a usually reliable observer; routing changes over time.
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
        """``http``, ``sleep`` and ``clock`` replace the real ones in tests.

        A given ``http`` client stays open: whoever made it closes it.
        """
        self._http = http
        self._sleep = sleep
        self._clock = clock

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        run = _Run(self, ctx, entity)
        match entity.type:
            case EntityType.IP_ADDRESS:
                findings = run.address()
            case EntityType.NETBLOCK:
                findings = run.netblock()
            case _:
                findings = run.asn()
        try:
            async for finding in findings:
                yield finding
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except SourceUnavailableError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        finally:
            run.report()


class _Run:
    """One run of the Shuttle."""

    def __init__(self, shuttle: RipeStatShuttle, ctx: ShuttleContext, entity: EntityInput) -> None:
        self.ctx = ctx
        self.entity = entity
        self.client = RipeStatClient(
            shuttle._http or ctx.http,
            sleep=shuttle._sleep,
            clock=shuttle._clock,
            deadline=shuttle._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        self.malformed = 0
        self.redacted = 0

    # --- shared steps ---

    async def call[M: BaseModel](
        self,
        endpoint: str,
        resource: str,
        model: type[M],
        *,
        read_timeout: httpx.Timeout = REQUEST_TIMEOUT,
    ) -> M:
        self.ctx.progress(f"Asking RIPEstat ({endpoint}).")
        data = await self.client.get(endpoint, resource, read_timeout=read_timeout)
        try:
            return model.model_validate(data)
        except ValidationError as error:
            raise InvalidResponseError(f"unexpected {endpoint} data") from error

    def items[M: BaseModel](self, values: Iterable[Any], model: type[M]) -> list[M]:
        """The items of a list that validate as ``model``; the others are counted."""
        kept: list[M] = []
        for value in values:
            try:
                kept.append(model.model_validate(value))
            except ValidationError:
                self.malformed += 1
        return kept

    def holder(self, value: object) -> Holder:
        holder = parse_holder(value)
        if holder.redacted:
            self.redacted += 1
        return holder

    def report(self) -> None:
        if self.malformed:
            self.ctx.note(NOTE_MALFORMED.format(count=self.malformed))
        if self.redacted:
            self.ctx.note(NOTE_REDACTED.format(count=self.redacted))
        self.ctx.progress(
            f"{self.client.requests} RIPEstat request(s), {self.client.retries} retried, "
            f"{self.client.rate_limited} rate-limited."
        )

    @staticmethod
    def asn_ref(number: int) -> EntityRef:
        return EntityRef(type=AS, value=f"AS{number}")

    def origins(self, values: Iterable[object]) -> list[int]:
        """Distinct AS numbers, in the order given; malformed ones are counted."""
        numbers: list[int] = []
        for value in values:
            number = _asn(value)
            if number is None:
                self.malformed += 1
            elif number not in numbers:
                numbers.append(number)
        return numbers

    def announced_by(
        self,
        prefix: str,
        numbers: list[int],
        properties: Mapping[int, dict[str, Any] | None],
        organizations: Mapping[int, str],
    ) -> list[Finding]:
        """Each origin AS (once, with its properties), ``ANNOUNCED_BY`` from ``prefix`` to
        it, and its holder's Organization with ``OWNS``."""
        if len(numbers) > 1:
            self.ctx.note(NOTE_MOAS.format(count=len(numbers)))
        findings: list[Finding] = []
        for number in numbers:
            findings.append(
                EntityFinding(type=AS, value=f"AS{number}", properties=properties.get(number))
            )
            findings.append(
                RelationshipFinding(
                    type=RelationType.ANNOUNCED_BY,
                    source=EntityRef(type=NB, value=prefix),
                    target=self.asn_ref(number),
                )
            )
            organization = organizations.get(number)
            if organization is not None:
                findings.extend(self.owns(number, organization))
        return findings

    @staticmethod
    def owns(number: int, organization: str) -> list[Finding]:
        return [
            EntityFinding(type=ORG, value=organization),
            RelationshipFinding(
                type=RelationType.OWNS,
                source=EntityRef(type=ORG, value=organization),
                target=_Run.asn_ref(number),
            ),
        ]

    def overview_properties(self, overview: AsOverview) -> tuple[dict[str, Any] | None, Holder]:
        """The ASN's properties from ``as-overview``, and its parsed holder."""
        holder = self.holder(overview.holder)
        properties = _drop_empty(
            {"as_name": holder.as_name, "announced": overview.announced, **_block(overview.block)}
        )
        return properties, holder

    # --- IP addresses ---

    async def address(self) -> AsyncIterator[Finding]:
        address = ipaddress.ip_address(self.entity.value)
        info = await self.call("network-info", address.compressed, NetworkInfo)
        numbers = self.origins(info.asns)
        prefix = _network(info.prefix)
        if prefix is None or address not in prefix:
            if info.prefix:  # a prefix that is not one, or does not hold the address
                self.malformed += 1
            self.ctx.note(NOTE_IP_NOT_ANNOUNCED)
            return

        # Holders first, so each origin ASN is yielded once, with its properties.
        if len(numbers) > MAX_HOLDER_LOOKUPS:
            self.ctx.note(NOTE_HOLDERS_CAPPED.format(limit=MAX_HOLDER_LOOKUPS, count=len(numbers)))
        properties: dict[int, dict[str, Any] | None] = {}
        organizations: dict[int, str] = {}
        failed = 0
        for number in numbers[:MAX_HOLDER_LOOKUPS]:
            try:
                overview = await self.call("as-overview", f"AS{number}", AsOverview)
            except RipeStatError as error:
                # The prefix and its origins are still worth keeping without this holder.
                self.ctx.logger.warning("as-overview failed: %s", type(error).__name__)
                failed += 1
                continue
            properties[number], holder = self.overview_properties(overview)
            if holder.organization is not None:
                organizations[number] = holder.organization
        if failed:
            self.ctx.note(NOTE_HOLDERS_FAILED.format(count=failed))

        value = prefix.with_prefixlen
        yield EntityFinding(type=NB, value=value, properties={"announced": True})
        yield RelationshipFinding(
            type=RelationType.WITHIN, source=self.entity.ref, target=EntityRef(type=NB, value=value)
        )
        for finding in self.announced_by(value, numbers, properties, organizations):
            yield finding

    # --- netblocks ---

    async def netblock(self) -> AsyncIterator[Finding]:
        query = ipaddress.ip_network(self.entity.value, strict=False)
        overview = await self.call("prefix-overview", query.with_prefixlen, PrefixOverview)
        announced = _network(overview.resource)
        covering: Network | None = None
        if overview.is_less_specific:
            if (
                announced is not None
                and announced.version == query.version
                and announced != query
                and query.subnet_of(announced)  # type: ignore[arg-type]
            ):
                covering = announced
            else:
                self.malformed += 1

        if not overview.announced or (overview.is_less_specific and covering is None):
            properties = _drop_empty({"announced": False, **_block(overview.block)})
            yield EntityFinding(type=NB, value=self.entity.value, properties=properties)
            self.ctx.note(NOTE_NETBLOCK_NOT_ANNOUNCED)
            return

        if covering is not None:
            self.ctx.note(NOTE_COVERED)
            target = covering.with_prefixlen
            yield EntityFinding(
                type=NB,
                value=target,
                properties=_drop_empty({"announced": True, **_block(overview.block)}),
            )
            yield RelationshipFinding(
                type=RelationType.WITHIN,
                source=self.entity.ref,
                target=EntityRef(type=NB, value=target),
            )
        else:
            target = self.entity.value
            yield EntityFinding(
                type=NB,
                value=target,
                properties=_drop_empty({"announced": True, **_block(overview.block)}),
            )

        numbers: list[int] = []
        origin_properties: dict[int, dict[str, Any] | None] = {}
        organizations: dict[int, str] = {}
        for origin in self.items(overview.asns, PrefixOrigin):
            if origin.asn in numbers:
                continue
            numbers.append(origin.asn)
            holder = self.holder(origin.holder)
            origin_properties[origin.asn] = _drop_empty({"as_name": holder.as_name})
            if holder.organization is not None:
                organizations[origin.asn] = holder.organization
        for finding in self.announced_by(target, numbers, origin_properties, organizations):
            yield finding

    # --- AS numbers ---

    async def asn(self) -> AsyncIterator[Finding]:
        number = int(self.entity.value.upper().removeprefix("AS"))
        overview = await self.call("as-overview", f"AS{number}", AsOverview)
        properties, holder = self.overview_properties(overview)
        yield EntityFinding(type=AS, value=self.entity.value, properties=properties)
        if holder.organization is not None:
            for finding in self.owns(number, holder.organization):
                yield finding

        answer = await self.call(
            "announced-prefixes",
            f"AS{number}",
            AnnouncedPrefixes,
            read_timeout=SLOW_REQUEST_TIMEOUT,
        )
        prefixes: dict[Network, list[datetime]] = {}
        ends: dict[Network, list[datetime]] = {}
        for item in self.items(answer.prefixes, AnnouncedPrefix):
            network = _network(item.prefix)
            if network is None:
                self.malformed += 1
                continue
            starts = prefixes.setdefault(network, [])
            stops = ends.setdefault(network, [])
            for timeline in item.timelines:
                if timeline.starttime is not None:
                    starts.append(timeline.starttime)
                if timeline.endtime is not None:
                    stops.append(timeline.endtime)
        if not prefixes:
            self.ctx.note(NOTE_AS_NOT_ANNOUNCED)
            return
        ordered = sorted(prefixes, key=_prefix_order)
        if len(ordered) > MAX_PREFIXES:
            self.ctx.note(NOTE_PREFIXES_CAPPED.format(count=len(ordered), limit=MAX_PREFIXES))
            ordered = ordered[:MAX_PREFIXES]
        for network in ordered:
            value = network.with_prefixlen
            properties = _drop_empty(
                {
                    "bgp_first_seen": min(prefixes[network], default=None),
                    "bgp_last_seen": max(ends[network], default=None),
                }
            )
            yield EntityFinding(type=NB, value=value, properties=properties)
            yield RelationshipFinding(
                type=RelationType.ANNOUNCED_BY,
                source=EntityRef(type=NB, value=value),
                target=self.entity.ref,
            )


__all__ = [
    "ADMIRALTY",
    "CONFIDENCE",
    "MAX_HOLDER_LOOKUPS",
    "MAX_PREFIXES",
    "REQUEST_BUDGET_SECONDS",
    "TIMEOUT_SECONDS",
    "RipeStatShuttle",
]
