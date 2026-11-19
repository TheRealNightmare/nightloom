"""The DNS Shuttle: what public DNS says about a domain.

For the input domain it asks the configured public resolvers (see
:mod:`nightloom_shuttle_dns.config`) for A, AAAA, CNAME, MX, NS, TXT, SOA and
CAA records, and for the DMARC policy at ``_dmarc.<name>``, and yields:

- A / AAAA: an IPAddress and ``RESOLVES_TO`` from the name that owns the
  addresses (the input, or the end of its CNAME chain). Addresses that are
  not public (RFC 1918 and the like) are recorded too, since publishing them
  in public DNS is itself a finding, with ``scope: private`` and a note.
  The Shuttle never connects to any address it finds.
- CNAME: the target Domain and ``ALIAS_OF``, following the chain up to
  :data:`MAX_CNAME_DEPTH` names.
- MX: the exchange Domain and ``HAS_MX`` (the preference in the note and in
  the input's ``dns_mx`` property). A null MX (``.``) is only a property.
- NS: the name server Domain and ``HAS_NS``.
- TXT, SOA, CAA and DMARC: typed properties of the input Domain (TXT capped
  at :data:`~nightloom_core.entities.DNS_MAX_RECORDS` records of
  :data:`~nightloom_core.entities.DNS_TEXT_MAX_LENGTH` characters; SPF and
  DMARC records repeated in ``dns_spf`` and ``dns_dmarc``).

Internal-only names are refused before anything is sent (see
:mod:`nightloom_shuttle_dns.names`). Names that do not exist and record types
a name does not have are not errors. A query whose every try fails is
skipped; if every query fails, the job fails.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

import dns.rdatatype
from dns.rdtypes.ANY.CAA import CAA
from dns.rdtypes.ANY.CNAME import CNAME
from dns.rdtypes.ANY.MX import MX
from dns.rdtypes.ANY.NS import NS
from dns.rdtypes.ANY.SOA import SOA
from dns.rdtypes.ANY.TXT import TXT

from nightloom_core.entities import (
    DNS_MAX_RECORDS,
    DNS_TEXT_MAX_LENGTH,
    EntityType,
    Sensitivity,
)
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    Finding,
    InputRefusedError,
    Mode,
    RefusalReason,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleMeta,
)
from nightloom_sdk.http import is_public_address
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_shuttle_dns.client import (
    RESOLVER_RATE_LIMIT,
    Answer,
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
from nightloom_shuttle_dns.names import name_text as _name

R = dns.rdatatype.RdataType

# The record types asked for the input name, in this order.
QUERY_TYPES = (R.A, R.AAAA, R.CNAME, R.MX, R.NS, R.TXT, R.SOA, R.CAA)
# At most this many ALIAS_OF steps are followed from the input.
MAX_CNAME_DEPTH = 5
DMARC_PREFIX = "_dmarc."

PRIVATE_NOTE = (
    "Not a public address. It is published in public DNS, which is worth knowing, "
    "but it is never contacted."
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPF = re.compile(r"v=spf1(\s|$)", re.IGNORECASE)
_DMARC = re.compile(r"v=dmarc1(\s*;|\s|$)", re.IGNORECASE)

D = EntityType.DOMAIN


class AllQueriesFailedError(Exception):
    """No resolver answered any query: the job fails rather than report an empty result."""


def _text(value: str) -> str:
    """One line of text, control characters replaced, capped in length."""
    return _CONTROL.sub("�", value).strip()[:DNS_TEXT_MAX_LENGTH]


def _txt(record: TXT) -> str:
    return _text(b"".join(record.strings).decode("utf-8", errors="replace"))


def _domain(value: str) -> EntityRef:
    return EntityRef(type=D, value=value)


class DnsShuttle(Shuttle):
    """Domain → its public DNS records. See the module docstring."""

    meta = ShuttleMeta(
        name="dns",
        version="1.0.0",
        title="DNS records",
        description=(
            "Looks up the domain's public DNS records (A, AAAA, CNAME, MX, NS, TXT, SOA, CAA and "
            "its DMARC policy) through public resolvers only (Cloudflare 1.1.1.1 and Quad9 "
            "9.9.9.9 by default), never the server's own resolver. Adds the addresses, aliases, "
            "mail exchangers and name servers it finds; TXT, SPF, DMARC, SOA and CAA records "
            "become properties of the domain. Internal-only names (.local, .internal, .corp, "
            "single labels, ...) are refused. Never contacts the addresses it finds."
        ),
        input_types=frozenset({D}),
        output_entity_types=frozenset({D, EntityType.IP_ADDRESS}),
        output_relationship_types=frozenset(
            {
                RelationType.RESOLVES_TO,
                RelationType.ALIAS_OF,
                RelationType.HAS_MX,
                RelationType.HAS_NS,
            }
        ),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        # Per resolver and job: a handful of queries per run, well within what
        # public resolvers expect from one client.
        rate_limit=RESOLVER_RATE_LIMIT,
        timeout_seconds=60,
        homepage=RESOLVER_HOMEPAGE,
        terms_url=RESOLVER_TERMS_URL,
        # A recursive resolver's answer: usually reliable, probably true (not
        # DNSSEC-validated here, and it can change at any time).
        default_admiralty="B2",
        default_confidence=0.9,
    )

    def __init__(self, *, client: DnsClient | None = None, config: DnsConfig | None = None) -> None:
        """``client`` and ``config`` replace the real resolvers and settings in tests."""
        self._client = client
        self._config = config

    @classmethod
    def check_configuration(cls) -> None:
        load_config()

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        config = self._config or load_config()
        name = entity.value.rstrip(".").lower()
        if is_internal_name(name, allow_test_zone=config.allow_private_resolvers):
            raise InputRefusedError(RefusalReason.INTERNAL_NAME)
        if name.startswith("*."):
            ctx.progress("A wildcard name has no records of its own; nothing to look up.")
            return
        rate_limit = self.meta.rate_limit
        assert rate_limit is not None  # noqa: S101 - network=True requires it
        client = self._client or ResolverClient(config.resolvers, HostRateLimiter(rate_limit))
        lookup = _Lookup(ctx, client, name, allow_test_zone=config.allow_private_resolvers)
        async for finding in lookup.run():
            yield finding


class _Lookup:
    """One run: the queries for one name and the findings they give."""

    def __init__(
        self, ctx: ShuttleContext, client: DnsClient, name: str, *, allow_test_zone: bool
    ) -> None:
        self.ctx = ctx
        self.client = client
        self.name = name
        self.allow_test_zone = allow_test_zone
        self.queries = 0
        self.failures = 0
        self.properties: dict[str, Any] = {}

    async def ask(self, name: str, rdtype: R) -> Answer | None:
        self.queries += 1
        try:
            return await self.client.query(name, rdtype)
        except DnsQueryError as error:
            self.failures += 1
            # The record type and error class only; never the name.
            self.ctx.logger.warning("%s query failed: %s", rdtype.name, error)
            return None

    def owned(self, answer: Answer | None) -> Answer | None:
        """The answer if its records belong to the input itself (not to a CNAME target)."""
        return answer if answer is not None and answer.name == self.name else None

    async def run(self) -> AsyncIterator[Finding]:
        names = [self.name] * len(QUERY_TYPES)
        types = list(QUERY_TYPES)
        if not self.name.startswith(DMARC_PREFIX):
            names.append(DMARC_PREFIX + self.name)
            types.append(R.TXT)
        answers = await asyncio.gather(*(self.ask(n, t) for n, t in zip(names, types, strict=True)))
        if self.failures == self.queries:
            raise AllQueriesFailedError("no resolver answered")
        by_type = dict(zip(QUERY_TYPES, answers, strict=False))
        dmarc = answers[len(QUERY_TYPES)] if len(answers) > len(QUERY_TYPES) else None

        chain = [self.name]
        async for finding in self.aliases(by_type[R.CNAME], chain):
            yield finding
        for rdtype in (R.A, R.AAAA):
            for finding in self.addresses(by_type[rdtype], chain):
                yield finding
        for finding in self.mail_exchangers(self.owned(by_type[R.MX])):
            yield finding
        for finding in self.name_servers(self.owned(by_type[R.NS])):
            yield finding
        self.texts(self.owned(by_type[R.TXT]), dmarc)
        self.soa(self.owned(by_type[R.SOA]))
        self.caa(self.owned(by_type[R.CAA]))
        if self.properties:
            yield EntityFinding(type=D, value=self.name, properties=self.properties)
        self.ctx.progress(
            f"{self.queries} queries, {self.failures} failed, CNAME chain of {len(chain) - 1}."
        )

    # --- relationships ---

    async def aliases(self, first: Answer | None, chain: list[str]) -> AsyncIterator[Finding]:
        """Follow the CNAME chain from the input, at most MAX_CNAME_DEPTH steps, no loops."""
        answer = self.owned(first)
        while answer is not None:
            records = [r for r in answer.records if isinstance(r, CNAME)]
            if not records:
                return
            target = _name(records[0].target)
            if target in chain or target == ".":
                return
            source = chain[-1]
            chain.append(target)
            yield EntityFinding(type=D, value=target)
            yield RelationshipFinding(
                type=RelationType.ALIAS_OF, source=_domain(source), target=_domain(target)
            )
            if len(chain) > MAX_CNAME_DEPTH or is_internal_name(
                target, allow_test_zone=self.allow_test_zone
            ):
                return
            self.ctx.raise_if_cancelled()
            next_answer = await self.ask(target, R.CNAME)
            answer = next_answer if next_answer is not None and next_answer.name == target else None

    def addresses(self, answer: Answer | None, chain: list[str]) -> list[Finding]:
        if answer is None:
            return []
        if answer.name in chain:
            owner, note = answer.name, None
        else:
            # The resolver followed a longer chain than this Shuttle does.
            owner = self.name
            note = f"Resolved through a CNAME chain; canonical name {answer.name}."[:500]
        findings: list[Finding] = []
        for record in answer.records:
            address = str(getattr(record, "address", ""))
            if not address:
                continue
            public = is_public_address(address)
            findings.append(
                EntityFinding(
                    type=EntityType.IP_ADDRESS,
                    value=address,
                    properties={"scope": "public" if public else "private"},
                    note=None if public else PRIVATE_NOTE,
                )
            )
            findings.append(
                RelationshipFinding(
                    type=RelationType.RESOLVES_TO,
                    source=_domain(owner),
                    target=EntityRef(type=EntityType.IP_ADDRESS, value=address),
                    note=note if public else (note or PRIVATE_NOTE),
                )
            )
        return findings

    def mail_exchangers(self, answer: Answer | None) -> list[Finding]:
        if answer is None:
            return []
        records = sorted(
            (r for r in answer.records if isinstance(r, MX)),
            key=lambda r: (r.preference, _name(r.exchange)),
        )
        self.set_list("dns_mx", [f"{r.preference} {_name(r.exchange)}" for r in records])
        findings: list[Finding] = []
        for record in records:
            exchange = _name(record.exchange)
            if exchange == ".":  # null MX (RFC 7505): the domain accepts no mail
                continue
            findings.append(EntityFinding(type=D, value=exchange))
            findings.append(
                RelationshipFinding(
                    type=RelationType.HAS_MX,
                    source=_domain(self.name),
                    target=_domain(exchange),
                    note=f"MX preference {record.preference}",
                )
            )
        return findings

    def name_servers(self, answer: Answer | None) -> list[Finding]:
        if answer is None:
            return []
        servers = sorted({_name(r.target) for r in answer.records if isinstance(r, NS)} - {"."})
        findings: list[Finding] = []
        for server in servers:
            findings.append(EntityFinding(type=D, value=server))
            findings.append(
                RelationshipFinding(
                    type=RelationType.HAS_NS, source=_domain(self.name), target=_domain(server)
                )
            )
        return findings

    # --- properties of the input ---

    def set_list(self, key: str, values: list[str]) -> None:
        kept = [v for v in dict.fromkeys(_text(v) for v in values) if v][:DNS_MAX_RECORDS]
        if kept:
            self.properties[key] = kept

    def texts(self, answer: Answer | None, dmarc: Answer | None) -> None:
        if answer is not None:
            records = sorted(_txt(r) for r in answer.records if isinstance(r, TXT))
            self.set_list("dns_txt", records)
            self.set_list("dns_spf", [r for r in records if _SPF.match(r)])
        if dmarc is not None:
            # The DMARC record may sit behind a CNAME (to a reporting provider).
            records = sorted(_txt(r) for r in dmarc.records if isinstance(r, TXT))
            self.set_list("dns_dmarc", [r for r in records if _DMARC.match(r)])

    def soa(self, answer: Answer | None) -> None:
        record = next((r for r in answer.records if isinstance(r, SOA)), None) if answer else None
        if record is None:
            return
        self.properties.update(
            dns_soa_primary_ns=_name(record.mname),
            dns_soa_responsible=_name(record.rname),
            dns_soa_serial=record.serial,
            dns_soa_refresh=record.refresh,
            dns_soa_retry=record.retry,
            dns_soa_expire=record.expire,
            dns_soa_minimum=record.minimum,
        )

    def caa(self, answer: Answer | None) -> None:
        if answer is not None:
            records = [r for r in answer.records if isinstance(r, CAA)]
            self.set_list("dns_caa", sorted(r.to_text() for r in records))


__all__ = ["MAX_CNAME_DEPTH", "QUERY_TYPES", "AllQueriesFailedError", "DnsShuttle"]
