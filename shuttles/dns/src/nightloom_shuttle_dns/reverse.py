"""The reverse DNS Shuttle: an address's PTR names, checked against forward DNS.

It uses the DNS Shuttle's resolver policy unchanged: the same configuration
(:mod:`nightloom_shuttle_dns.config`), the same client
(:mod:`nightloom_shuttle_dns.client`, public resolvers only, never the host's
``/etc/resolv.conf``) and the same rate limit per resolver.

- **IPAddress.** An address that is not public (the SDK's
  ``is_public_address``) is skipped with a job note before any query. For a
  public one, the PTR records of its reverse name (``in-addr.arpa`` or
  ``ip6.arpa``) are asked for. Each PTR name (at most :data:`MAX_PTR_NAMES`,
  alphabetically; invalid and internal-only names are dropped and counted)
  becomes a Domain with ``HAS_PTR`` from the address. Then each name is
  **forward-confirmed**: its A (IPv4) or AAAA (IPv6) records are asked for.
  If they contain the address, the ``HAS_PTR`` note says
  ``forward_confirmed: true``, a ``RESOLVES_TO`` edge from the name back to
  the address is added and both are rated B2. Otherwise the note says
  ``forward_confirmed: false``, and the name and edge are rated C3: whoever
  controls the reverse zone can publish any name there, so an unconfirmed PTR
  is a claim, not a fact. The address gets ``ptr_checked_at``.
- **Netblock.** At most :data:`MAX_SWEEP_ADDRESSES` addresses (IPv4 /24 or
  smaller, IPv6 /120 or smaller); a larger block is refused before any query.
  Every address is asked in order (network and broadcast addresses too: they
  often have PTR records), with cancellation checked between queries. Only
  addresses that have PTR names are added, as IPAddresses with ``WITHIN`` to
  the netblock, followed by the same PTR findings as above.

Names that do not exist (NXDOMAIN) and empty answers are quiet. Failed
queries (SERVFAIL, timeouts) are counted; a job note always summarizes the
run. The job fails with ``source_unavailable`` when no PTR query got an
answer, or after :data:`MAX_CONSECUTIVE_FAILURES` failures in a row in a sweep.
"""

import ipaddress
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import cast

import dns.rdatatype
import dns.reversename
from dns.rdtypes.ANY.PTR import PTR

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, normalize
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
from nightloom_sdk.http import is_public_address
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
from nightloom_shuttle_dns.names import is_internal_name, name_text

R = dns.rdatatype.RdataType
D = EntityType.DOMAIN
IP = EntityType.IP_ADDRESS
NETBLOCK = EntityType.NETBLOCK

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# At most this many PTR names per address (alphabetically, after filtering).
MAX_PTR_NAMES = 10
# Netblock sweeps: at most 256 addresses, i.e. an IPv4 /24 or an IPv6 /120.
MAX_SWEEP_ADDRESSES = 256
MIN_PREFIX = {4: 24, 6: 120}
# At most this many PTR names in one sweep: each costs up to three findings
# (Domain, HAS_PTR, RESOLVES_TO) and each address two more (IPAddress,
# WITHIN), which keeps a full sweep under the worker's 5,000-finding limit.
MAX_SWEEP_NAMES = 1400
# A sweep stops after this many PTR queries in a row failed (the resolvers are
# unwell), and no query starts after this long (the Shuttle's timeout is 300 s).
MAX_CONSECUTIVE_FAILURES = 10
SWEEP_BUDGET_SECONDS = 240.0

# A PTR name that resolves back to the address: a recursive resolver's answer,
# usually reliable and probably true, as for the DNS Shuttle.
CONFIRMED_ADMIRALTY = "B2"
CONFIRMED_CONFIDENCE = 0.9
# A PTR name that does not: the reverse zone's owner can publish any name.
UNCONFIRMED_ADMIRALTY = "C3"
UNCONFIRMED_CONFIDENCE = 0.5

# Relationships carry no typed properties: forward confirmation is stated in
# the HAS_PTR note, as crt.sh's "wildcard" and RDAP's "role" are.
CONFIRMED_NOTE = "forward_confirmed: true (the name resolves back to this address)"
UNCONFIRMED_NOTE = (
    "forward_confirmed: false. PTR not confirmed by forward DNS; anyone controlling the "
    "reverse zone can set any name."
)
CNAME_NOTE = "Resolved through a CNAME chain; canonical name {name}."

NOTE_NOT_PUBLIC = "Not a public address; reverse DNS was not queried."
NOTE_SKIPPED = "{count} address(es) in the netblock are not public and were not queried."
NOTE_DROPPED = "Dropped {count} PTR name(s): {invalid} invalid, {internal} internal-only."
NOTE_NAMES_CAPPED = (
    "{count} address(es) had more than {limit} PTR names; the first {limit} alphabetically "
    "were kept."
)
NOTE_SWEEP_CAPPED = (
    "Stopped after {limit} PTR names (the job's finding limit): {done} of {total} addresses "
    "were swept."
)
NOTE_TIME_LIMIT = "Reached the time limit after {done} of {total} addresses."
NOTE_FAILING = "Stopped after {count} failed queries in a row: {done} of {total} addresses."
NOTE_SUMMARY = (
    "{queried} address(es) queried, {with_ptr} with PTR names, {confirmed} name(s) "
    "forward-confirmed, {failed} quer(ies) failed."
)

Now = Callable[[], datetime]
Clock = Callable[[], float]


def _now() -> datetime:
    return datetime.now(UTC)


def reverse_name(address: Address) -> str:
    """The name its PTR records live at (``4.3.2.1.in-addr.arpa``), without the final dot."""
    return name_text(dns.reversename.from_address(str(address)))


def sweep_size_allowed(network: Network) -> bool:
    """Whether ``network`` is small enough to sweep (IPv4 /24, IPv6 /120 or smaller)."""
    return network.prefixlen >= MIN_PREFIX[network.version]


class ReverseDnsShuttle(Shuttle):
    """IPAddress or small Netblock → PTR names, forward-confirmed. See the module docstring."""

    meta = ShuttleMeta(
        name="reverse-dns",
        version="1.0.0",
        title="Reverse DNS (PTR)",
        description=(
            "Looks up the PTR (reverse DNS) names of a public IP address, or of every address "
            "in a netblock of up to 256 addresses (IPv4 /24, IPv6 /120), through public "
            "resolvers only, like the DNS Shuttle. Each name is checked against forward DNS: "
            "a name that resolves back to the address is rated higher and linked with "
            "RESOLVES_TO; one that does not is kept with a lower rating, since whoever runs "
            "the reverse zone can publish any name. Private addresses are skipped."
        ),
        input_types=frozenset({IP, NETBLOCK}),
        # IPAddresses and WITHIN only come from netblock sweeps.
        output_entity_types=frozenset({D, IP}),
        output_relationship_types=frozenset(
            {RelationType.HAS_PTR, RelationType.RESOLVES_TO, RelationType.WITHIN}
        ),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        rate_limit=RESOLVER_RATE_LIMIT,
        timeout_seconds=300,
        homepage=RESOLVER_HOMEPAGE,
        terms_url=RESOLVER_TERMS_URL,
        default_admiralty=CONFIRMED_ADMIRALTY,
        default_confidence=CONFIRMED_CONFIDENCE,
    )

    def __init__(
        self,
        *,
        client: DnsClient | None = None,
        config: DnsConfig | None = None,
        now: Now = _now,
        clock: Clock = time.monotonic,
    ) -> None:
        """``client``, ``config``, ``now`` and ``clock`` replace the real ones in tests."""
        self._client = client
        self._config = config
        self._now = now
        self._clock = clock

    @classmethod
    def check_configuration(cls) -> None:
        load_config()

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        config = self._config or load_config()
        network: Network | None = None
        address: Address | None = None
        if entity.type == NETBLOCK:
            network = ipaddress.ip_network(entity.value, strict=False)
            if not sweep_size_allowed(network):
                raise InputRefusedError(RefusalReason.NETBLOCK_TOO_LARGE)
        else:
            address = ipaddress.ip_address(entity.value)
            if not is_public_address(address):
                ctx.note(NOTE_NOT_PUBLIC)
                return
        rate_limit = self.meta.rate_limit
        assert rate_limit is not None  # noqa: S101 - network=True requires it
        client = self._client or ResolverClient(config.resolvers, HostRateLimiter(rate_limit))
        lookup = _Lookup(
            ctx,
            client,
            allow_test_zone=config.allow_private_resolvers,
            now=self._now,
            clock=self._clock,
        )
        if network is not None:
            findings = lookup.netblock(network, entity.ref)
        else:
            assert address is not None  # noqa: S101 - set above for an IPAddress
            findings = lookup.address(address)
        try:
            async for finding in findings:
                yield finding
        finally:
            lookup.summarize()


class _Lookup:
    """One run: the PTR and forward queries, their counts and the findings they give."""

    def __init__(
        self,
        ctx: ShuttleContext,
        client: DnsClient,
        *,
        allow_test_zone: bool,
        now: Now,
        clock: Clock,
    ) -> None:
        self.ctx = ctx
        self.client = client
        self.allow_test_zone = allow_test_zone
        self.now = now
        self.clock = clock
        self.queried = 0  # addresses whose PTR records were asked for
        self.ptr_failures = 0
        self.with_ptr = 0
        self.confirmed = 0
        self.failed = 0  # every failed query, forward ones included
        self.invalid = 0
        self.internal = 0
        self.capped = 0
        self.names_kept = 0
        self.forward: dict[tuple[str, R], tuple[str, frozenset[Address]] | None] = {}
        self.domains: set[tuple[str, bool]] = set()

    # --- queries ---

    async def ptr_names(self, address: Address) -> list[str] | None:
        """The address's kept PTR names, alphabetically; ``None`` if the query failed."""
        self.ctx.raise_if_cancelled()
        self.queried += 1
        try:
            answer = await self.client.query(reverse_name(address), R.PTR)
        except DnsQueryError as error:
            self.ptr_failures += 1
            self.failed += 1
            # The record type and error class only; never the address or a name.
            self.ctx.logger.warning("PTR query failed: %s", error)
            return None
        if answer is None:
            return []
        # The owner may differ from the reverse name (RFC 2317 classless
        # delegation is a CNAME to another reverse name): the records still
        # belong to this address.
        names = {self.keep(r.target) for r in answer.records if isinstance(r, PTR)}
        kept = sorted(name for name in names if name is not None)
        if len(kept) > MAX_PTR_NAMES:
            self.capped += 1
        return kept[:MAX_PTR_NAMES]

    def keep(self, target: object) -> str | None:
        """A PTR target as a Domain value, or ``None`` (counted) if it is unusable."""
        text = name_text(target)
        if "*" in text:
            self.invalid += 1
            return None
        try:
            name = normalize(D, text).value
        except NormalizationError:
            self.invalid += 1
            return None
        # Reverse names (in-addr.arpa, ip6.arpa) and internal-only names are not hosts
        # that public DNS can confirm; asking about them would only leak them.
        if name.endswith(".arpa") or is_internal_name(name, allow_test_zone=self.allow_test_zone):
            self.internal += 1
            return None
        return name

    async def resolves_back(self, name: str, address: Address) -> tuple[bool, str]:
        """Whether ``name``'s A/AAAA records contain ``address``, and the records' owner."""
        rdtype = R.A if address.version == 4 else R.AAAA
        key = (name, rdtype)
        if key not in self.forward:
            self.ctx.raise_if_cancelled()
            try:
                answer = await self.client.query(name, rdtype)
            except DnsQueryError as error:
                self.failed += 1
                self.ctx.logger.warning("%s query failed: %s", rdtype.name, error)
                self.forward[key] = None
            else:
                addresses = frozenset(
                    ipaddress.ip_address(str(r.address))
                    for r in (answer.records if answer else ())
                    if hasattr(r, "address")
                )
                self.forward[key] = (answer.name if answer else name, addresses)
        result = self.forward[key]
        if result is None or address not in result[1]:
            return False, name
        return True, result[0]

    # --- findings ---

    async def ptr_findings(self, address: Address, names: list[str]) -> list[Finding]:
        ip = EntityRef(type=IP, value=str(address))
        findings: list[Finding] = []
        for name in names:
            confirmed, owner = await self.resolves_back(name, address)
            admiralty = CONFIRMED_ADMIRALTY if confirmed else UNCONFIRMED_ADMIRALTY
            confidence = CONFIRMED_CONFIDENCE if confirmed else UNCONFIRMED_CONFIDENCE
            domain = EntityRef(type=D, value=name)
            if (name, confirmed) not in self.domains:
                self.domains.add((name, confirmed))
                findings.append(
                    EntityFinding(type=D, value=name, admiralty=admiralty, confidence=confidence)
                )
            findings.append(
                RelationshipFinding(
                    type=RelationType.HAS_PTR,
                    source=ip,
                    target=domain,
                    admiralty=admiralty,
                    confidence=confidence,
                    note=CONFIRMED_NOTE if confirmed else UNCONFIRMED_NOTE,
                )
            )
            if confirmed:
                self.confirmed += 1
                findings.append(
                    RelationshipFinding(
                        type=RelationType.RESOLVES_TO,
                        source=domain,
                        target=ip,
                        note=None if owner == name else CNAME_NOTE.format(name=owner)[:500],
                    )
                )
        return findings

    async def address(self, address: Address) -> AsyncIterator[Finding]:
        names = await self.ptr_names(address)
        if names is None:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)
        if names:
            self.with_ptr += 1
        for finding in await self.ptr_findings(address, names):
            yield finding
        yield EntityFinding(type=IP, value=str(address), properties={"ptr_checked_at": self.now()})

    async def netblock(self, network: Network, block: EntityRef) -> AsyncIterator[Finding]:
        total = network.num_addresses
        deadline = self.clock() + SWEEP_BUDGET_SECONDS
        skipped = 0
        in_a_row = 0
        for done, member in enumerate(network):
            # typeshed types a network's members as the common base class.
            address = cast(Address, member)
            if not is_public_address(address):
                skipped += 1
                continue
            if self.clock() >= deadline:
                self.ctx.note(NOTE_TIME_LIMIT.format(done=done, total=total))
                break
            names = await self.ptr_names(address)
            if names is None:
                in_a_row += 1
                if in_a_row >= MAX_CONSECUTIVE_FAILURES:
                    self.ctx.note(NOTE_FAILING.format(count=in_a_row, done=done + 1, total=total))
                    raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)
                continue
            in_a_row = 0
            if not names:
                continue
            if self.names_kept + len(names) > MAX_SWEEP_NAMES:
                self.ctx.note(
                    NOTE_SWEEP_CAPPED.format(limit=MAX_SWEEP_NAMES, done=done, total=total)
                )
                break
            self.names_kept += len(names)
            self.with_ptr += 1
            value = str(address)
            yield EntityFinding(
                type=IP, value=value, properties={"scope": "public", "ptr_checked_at": self.now()}
            )
            yield RelationshipFinding(
                type=RelationType.WITHIN, source=EntityRef(type=IP, value=value), target=block
            )
            for finding in await self.ptr_findings(address, names):
                yield finding
        if skipped:
            self.ctx.note(NOTE_SKIPPED.format(count=skipped))
        if self.queried and self.ptr_failures == self.queried:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)

    def summarize(self) -> None:
        """The run's job notes: counts only, never an address or a name."""
        if self.capped:
            self.ctx.note(NOTE_NAMES_CAPPED.format(count=self.capped, limit=MAX_PTR_NAMES))
        if self.invalid or self.internal:
            self.ctx.note(
                NOTE_DROPPED.format(
                    count=self.invalid + self.internal, invalid=self.invalid, internal=self.internal
                )
            )
        if self.queried:
            summary = NOTE_SUMMARY.format(
                queried=self.queried,
                with_ptr=self.with_ptr,
                confirmed=self.confirmed,
                failed=self.failed,
            )
            self.ctx.note(summary)
            self.ctx.progress(summary)


__all__ = [
    "CONFIRMED_ADMIRALTY",
    "CONFIRMED_NOTE",
    "MAX_PTR_NAMES",
    "MAX_SWEEP_ADDRESSES",
    "MAX_SWEEP_NAMES",
    "UNCONFIRMED_ADMIRALTY",
    "UNCONFIRMED_NOTE",
    "ReverseDnsShuttle",
    "reverse_name",
    "sweep_size_allowed",
]
