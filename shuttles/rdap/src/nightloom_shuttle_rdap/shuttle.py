"""The RDAP Shuttle: registration data for domains, IP networks and AS numbers.

RDAP (Registration Data Access Protocol, RFC 9082/9083) is the JSON
successor of WHOIS, run by domain registries and registrars and by the five
Regional Internet Registries. The Shuttle finds the responsible server in
IANA's bootstrap registries (:mod:`nightloom_shuttle_rdap.bootstrap`), asks
it through ``ctx.http`` only (:mod:`nightloom_shuttle_rdap.client`), and
yields:

- **Domain.** The *registrable* domain is looked up (``www.example.org`` →
  ``example.org``, with a job note). Its registration dates, statuses,
  DNSSEC flag, registrar name and IANA ID and the answering server become
  properties of that Domain; the registrar becomes an Organization with
  ``REGISTERED_BY`` (note ``role: registrar``), as does the registrant
  (``role: registrant``) only when it is an organization whose name is not
  redacted; the name servers become Domains with ``HAS_NS``. When the
  registry links to the registrar's own RDAP server (common for .com and
  .net), that one answer is fetched too and merged, registry data winning.
- **IPAddress / Netblock.** The registered network becomes Netblocks (the
  ``cidr0`` extension, else its address range as the minimal CIDR list, at
  most :data:`~nightloom_shuttle_rdap.objects.MAX_CIDRS`) with handle, name,
  type, country, registry (the RIR), dates and parent handle, and ``WITHIN``
  from the input address (or from the input network when it lies inside a
  larger one); the owning organization becomes an Organization with
  ``OWNS``.
- **ASN.** Handle, name, country, registry and dates on the ASN, and the
  owning Organization with ``OWNS``.

Contacts are read only by :func:`~nightloom_shuttle_rdap.privacy.extract_contacts`:
no person's name, email address, phone number or postal address is ever
kept; skipped contacts are counted in a job note.

Provenance: the registry's answer is authoritative but may be redacted or
out of date, **A2** (confidence 0.9); what only the registrar's answer
says is **B2** (0.8).
"""

import asyncio
import ipaddress
import time
from collections import Counter
from collections.abc import AsyncIterator, Mapping
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
from nightloom_shuttle_rdap import objects
from nightloom_shuttle_rdap.bootstrap import (
    SHARED_CACHE,
    BootstrapCache,
    BootstrapError,
    BootstrapFile,
    Registry,
)
from nightloom_shuttle_rdap.client import (
    Clock,
    InvalidResponseError,
    RdapClient,
    RdapError,
    Sleep,
    SourceUnavailableError,
)
from nightloom_shuttle_rdap.privacy import (
    REGISTRANT,
    REGISTRAR,
    SKIP_LABELS,
    Contacts,
    SkipReason,
    extract_contacts,
)

D = EntityType.DOMAIN
IP = EntityType.IP_ADDRESS
NB = EntityType.NETBLOCK
AS = EntityType.ASN
ORG = EntityType.ORGANIZATION

TIMEOUT_SECONDS = 120
# No new request starts this long after the run began.
REQUEST_BUDGET_SECONDS = 100

REGISTRY_ADMIRALTY, REGISTRY_CONFIDENCE = "A2", 0.9
REGISTRAR_ADMIRALTY, REGISTRAR_CONFIDENCE = "B2", 0.8

NOTE_SUBDOMAIN = "The input is a subdomain; looked up its registrable domain instead."
NOTE_NOT_FOUND = "The RDAP server has no record of this {what}."
NOTE_REGISTRAR_FAILED = "The registrar's RDAP server did not answer usefully; registry data only."
NOTE_REGISTRAR_NOT_FOUND = (
    "The registrar's RDAP server has no record of this domain; registry data only."
)
NOTE_CIDRS_CAPPED = (
    "The network's address range needs more than {limit} CIDR blocks; kept the first {limit}."
)
NOTE_NO_RANGE = "The RDAP answer has no usable address range; no netblock added."
NOTE_NOT_INSIDE = "The input network is not inside the registered network; no WITHIN added."
NOTE_SKIPPED = "Skipped {total:,} contact(s): {details}. Only organization names are kept."

ROLE_NOTE = "role: {roles}"


def _ip_network(entity: EntityInput) -> objects.Network:
    if entity.type == IP:
        return ipaddress.ip_network(entity.value)
    return ipaddress.ip_network(entity.value, strict=False)


def _drop_empty(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in properties.items() if value not in (None, [], "")}


class RdapShuttle(Shuttle):
    """Registration data for a domain, IP address, netblock or ASN. See the module docstring."""

    meta = ShuttleMeta(
        name="rdap",
        version="1.0.0",
        title="Registration data (RDAP)",
        description=(
            "Looks up public registration data (RDAP, the successor of WHOIS) at the registry "
            "responsible, found through IANA's bootstrap registries (no API key). For a domain "
            "(its registrable domain): registration, expiry and last-change dates, status, "
            "DNSSEC, the registrar, the registrant if it is an organization, and the name "
            "servers. For an IP address or netblock: the registered network with its owner. "
            "For an ASN: its registration and owner. Never keeps people's names, email "
            "addresses, phone numbers or postal addresses, or redacted values."
        ),
        input_types=frozenset({D, IP, NB, AS}),
        # ASN: a lookup adds its registration data to the input ASN itself.
        output_entity_types=frozenset({D, NB, AS, ORG}),
        output_relationship_types=frozenset(
            {
                RelationType.REGISTERED_BY,
                RelationType.HAS_NS,
                RelationType.WITHIN,
                RelationType.OWNS,
            }
        ),
        mode=Mode.PASSIVE,
        # Organization is an organization-sensitivity type; no person-related types.
        sensitivity=Sensitivity.ORGANIZATION,
        network=True,
        # Registries publish no common limit; once a second per server is polite.
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        # A week: registration data changes rarely.
        cache_ttl_seconds=7 * 24 * 3600,
        homepage="https://data.iana.org/rdap/",
        terms_url="https://www.iana.org/help/rdap-faq",
        # The registry is the authoritative source, but its data may be
        # redacted or out of date: completely reliable, probably true.
        default_admiralty=REGISTRY_ADMIRALTY,
        default_confidence=REGISTRY_CONFIDENCE,
    )

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        bootstrap: BootstrapCache | None = None,
    ) -> None:
        """``http``, ``sleep``, ``clock`` and ``bootstrap`` replace the real ones in tests.

        A given ``http`` client stays open: whoever made it closes it. Without
        ``bootstrap``, the process-wide cache is used.
        """
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self._bootstrap = bootstrap or SHARED_CACHE

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        run = _Run(self, ctx, entity)
        match entity.type:
            case EntityType.DOMAIN:
                findings = run.domain(run.registrable())
            case EntityType.ASN:
                findings = run.asn()
            case _:
                findings = run.network()
        try:
            async for finding in findings:
                yield finding
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except BootstrapError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except SourceUnavailableError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        run.report()


class _Run:
    """One run of the Shuttle."""

    def __init__(self, shuttle: RdapShuttle, ctx: ShuttleContext, entity: EntityInput) -> None:
        self.ctx = ctx
        self.entity = entity
        self.bootstrap = shuttle._bootstrap
        self.client = RdapClient(
            shuttle._http or ctx.http,
            sleep=shuttle._sleep,
            clock=shuttle._clock,
            deadline=shuttle._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        self.skipped: Counter[SkipReason] = Counter()

    # --- shared steps ---

    async def _fetch_bootstrap(self, url: str) -> Mapping[str, Any]:
        data = await self.client.get(url)
        if data is None:
            raise InvalidResponseError("the bootstrap file is missing")
        return data

    async def registry_file(self, registry: Registry) -> BootstrapFile:
        self.ctx.progress(f"Finding the RDAP server (IANA bootstrap, {registry.value}).")
        return await self.bootstrap.get(registry, self._fetch_bootstrap)

    @staticmethod
    def expect(data: Mapping[str, Any], object_class: str) -> None:
        found = objects.object_class(data)
        if found is not None and found != object_class:
            raise InvalidResponseError("unexpected RDAP object class")

    def contacts(self, data: Mapping[str, Any]) -> Contacts:
        contacts = extract_contacts(data)
        self.skipped.update(contacts.skipped)
        return contacts

    def report(self) -> None:
        total = sum(self.skipped.values())
        if total:
            details = ", ".join(
                f"{count:,} {SKIP_LABELS[reason]}"
                for reason, count in sorted(self.skipped.items(), key=lambda item: item[0].value)
            )
            self.ctx.note(NOTE_SKIPPED.format(total=total, details=details))
        self.ctx.progress(
            f"{self.client.requests} RDAP request(s), {self.client.retries} retried, "
            f"{self.client.rate_limited} rate-limited."
        )

    # --- domains ---

    def registrable(self) -> str:
        """The registrable domain to look up; refuses public suffixes and internal names."""
        scope = self.entity.value.removeprefix("*.")
        try:
            registrable = registrable_domain(scope)
        except NormalizationError:
            registrable = None
        if registrable is None:
            raise InputRefusedError(RefusalReason.NOT_REGISTRABLE)
        return registrable

    async def domain(self, registrable: str) -> AsyncIterator[Finding]:
        if registrable != self.entity.value:
            self.ctx.note(NOTE_SUBDOMAIN)
        service = (await self.registry_file(Registry.DNS)).for_domain(registrable)
        if service is None:
            raise ShuttleFailedError(FailureReason.NOT_COVERED)
        url = service.url(f"domain/{registrable}")
        self.ctx.progress("Asking the registry's RDAP server.")
        registry = await self.client.get(url)
        if registry is None:
            self.ctx.note(NOTE_NOT_FOUND.format(what="domain (it may not be registered)"))
            return
        self.expect(registry, "domain")
        registrar = await self.registrar_answer(registry, url)

        target = (
            self.entity.ref
            if registrable == self.entity.value
            else EntityRef(type=D, value=registrable)
        )
        official = self.contacts(registry)
        extra = self.contacts(registrar) if registrar is not None else Contacts()

        properties = self.domain_properties(registry, official)
        properties["rdap_server"] = service.base_url
        yield EntityFinding(type=D, value=registrable, properties=properties)
        if registrar is not None:
            only_registrar = {
                key: value
                for key, value in self.domain_properties(registrar, extra).items()
                if key not in properties
            }
            if only_registrar:
                yield EntityFinding(
                    type=D,
                    value=registrable,
                    properties=only_registrar,
                    admiralty=REGISTRAR_ADMIRALTY,
                    confidence=REGISTRAR_CONFIDENCE,
                )

        for finding in self.registered_by(target, official, extra):
            yield finding

        servers, from_registry = objects.nameservers(registry), True
        if not servers and registrar is not None:
            servers, from_registry = objects.nameservers(registrar), False
        provenance: dict[str, Any] = (
            {}
            if from_registry
            else {"admiralty": REGISTRAR_ADMIRALTY, "confidence": REGISTRAR_CONFIDENCE}
        )
        for server in servers:
            if server == registrable:
                continue
            yield EntityFinding(type=D, value=server, **provenance)
            yield RelationshipFinding(
                type=RelationType.HAS_NS,
                source=target,
                target=EntityRef(type=D, value=server),
                confidence=provenance.get("confidence"),
            )

    async def registrar_answer(
        self, registry: Mapping[str, Any], url: str
    ) -> Mapping[str, Any] | None:
        """The registrar's answer, if the registry links to it (at most one link is followed)."""
        link = objects.related_link(registry, url)
        if link is None:
            return None
        self.ctx.progress("Asking the registrar's RDAP server.")
        try:
            answer = await self.client.get(link)
        except RdapError as error:
            self.ctx.logger.warning("registrar RDAP lookup failed: %s", type(error).__name__)
            self.ctx.note(NOTE_REGISTRAR_FAILED)
            return None
        if answer is None:
            self.ctx.note(NOTE_REGISTRAR_NOT_FOUND)
            return None
        if objects.object_class(answer) not in (None, "domain"):
            self.ctx.note(NOTE_REGISTRAR_FAILED)
            return None
        return answer

    @staticmethod
    def domain_properties(data: Mapping[str, Any], contacts: Contacts) -> dict[str, Any]:
        return _drop_empty(
            {
                **objects.events(data),
                "status": objects.statuses(data),
                "dnssec_signed": objects.dnssec_signed(data),
                "registrar": contacts.organizations.get(REGISTRAR),
                "registrar_iana_id": contacts.registrar_iana_id,
            }
        )

    def registered_by(
        self, target: EntityRef, official: Contacts, extra: Contacts
    ) -> list[Finding]:
        """Organization + REGISTERED_BY for the registrar and an organization registrant."""
        # Organization name (case-folded) → (name, roles, said by the registry).
        organizations: dict[str, tuple[str, list[str], bool]] = {}
        for role in (REGISTRAR, REGISTRANT):
            name, from_registry = official.organizations.get(role), True
            if name is None:
                name, from_registry = extra.organizations.get(role), False
            if name is None:
                continue
            key = name.casefold()
            if key in organizations:
                first, roles, said = organizations[key]
                organizations[key] = (first, [*roles, role], said or from_registry)
            else:
                organizations[key] = (name, [role], from_registry)
        findings: list[Finding] = []
        for name, roles, from_registry in organizations.values():
            admiralty = None if from_registry else REGISTRAR_ADMIRALTY
            confidence = None if from_registry else REGISTRAR_CONFIDENCE
            findings.append(
                EntityFinding(type=ORG, value=name, admiralty=admiralty, confidence=confidence)
            )
            findings.append(
                RelationshipFinding(
                    type=RelationType.REGISTERED_BY,
                    source=target,
                    target=EntityRef(type=ORG, value=name),
                    confidence=confidence,
                    note=ROLE_NOTE.format(roles=", ".join(sorted(roles))),
                )
            )
        return findings

    # --- IP addresses and netblocks ---

    async def network(self) -> AsyncIterator[Finding]:
        query = _ip_network(self.entity)
        registry = Registry.IPV4 if query.version == 4 else Registry.IPV6
        service = (await self.registry_file(registry)).for_network(query)
        if service is None:
            raise ShuttleFailedError(FailureReason.NOT_COVERED)
        if self.entity.type == IP:
            path = f"ip/{query.network_address.compressed}"
        else:
            path = f"ip/{query.network_address.compressed}/{query.prefixlen}"
        self.ctx.progress("Asking the Regional Internet Registry's RDAP server.")
        data = await self.client.get(service.url(path))
        if data is None:
            self.ctx.note(NOTE_NOT_FOUND.format(what="address range"))
            return
        self.expect(data, "ip network")
        blocks, capped = objects.networks(data)
        if capped:
            self.ctx.note(NOTE_CIDRS_CAPPED.format(limit=objects.MAX_CIDRS))
        if not blocks:
            self.ctx.note(NOTE_NO_RANGE)
            return
        properties = _drop_empty(
            {
                "handle": objects.text(data.get("handle"), objects.HANDLE_MAX_LENGTH),
                "name": objects.text(data.get("name")),
                "type": objects.text(data.get("type")),
                "country": objects.country(data),
                "registry": service.rir,
                "parent_handle": objects.text(data.get("parentHandle"), objects.HANDLE_MAX_LENGTH),
                **{k: v for k, v in objects.events(data).items() if k != "expires"},
            }
        )
        inside = False
        values: list[str] = []
        for block in blocks:
            value = block.with_prefixlen
            values.append(value)
            yield EntityFinding(type=NB, value=value, properties=properties or None)
            within = (
                query.subnet_of(block)  # type: ignore[arg-type]
                if block.version == query.version
                else False
            )
            if not within:
                continue
            inside = True
            if self.entity.type == IP or block != query:
                yield RelationshipFinding(
                    type=RelationType.WITHIN,
                    source=self.entity.ref,
                    target=EntityRef(type=NB, value=value),
                )
        if self.entity.type == NB and not inside:
            self.ctx.note(NOTE_NOT_INSIDE)
        owner = self.contacts(data).organizations.get(REGISTRANT)
        if owner is not None:
            yield EntityFinding(type=ORG, value=owner)
            for value in values:
                yield RelationshipFinding(
                    type=RelationType.OWNS,
                    source=EntityRef(type=ORG, value=owner),
                    target=EntityRef(type=NB, value=value),
                )

    # --- AS numbers ---

    async def asn(self) -> AsyncIterator[Finding]:
        number = int(self.entity.value.upper().removeprefix("AS"))
        service = (await self.registry_file(Registry.ASN)).for_asn(number)
        if service is None:
            raise ShuttleFailedError(FailureReason.NOT_COVERED)
        self.ctx.progress("Asking the Regional Internet Registry's RDAP server.")
        data = await self.client.get(service.url(f"autnum/{number}"))
        if data is None:
            self.ctx.note(NOTE_NOT_FOUND.format(what="AS number"))
            return
        self.expect(data, "autnum")
        properties = _drop_empty(
            {
                "handle": objects.text(data.get("handle"), objects.HANDLE_MAX_LENGTH),
                "name": objects.text(data.get("name")),
                "country": objects.country(data),
                "registry": service.rir,
                **{k: v for k, v in objects.events(data).items() if k != "expires"},
            }
        )
        yield EntityFinding(type=AS, value=self.entity.value, properties=properties or None)
        owner = self.contacts(data).organizations.get(REGISTRANT)
        if owner is not None:
            yield EntityFinding(type=ORG, value=owner)
            yield RelationshipFinding(
                type=RelationType.OWNS,
                source=EntityRef(type=ORG, value=owner),
                target=self.entity.ref,
            )


__all__ = [
    "REGISTRAR_ADMIRALTY",
    "REGISTRY_ADMIRALTY",
    "REQUEST_BUDGET_SECONDS",
    "TIMEOUT_SECONDS",
    "RdapShuttle",
]
