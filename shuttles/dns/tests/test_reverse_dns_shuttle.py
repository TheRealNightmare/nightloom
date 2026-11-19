"""The reverse DNS (PTR) Shuttle with a fake resolver: no network."""

import asyncio
import ipaddress
import os
from datetime import UTC, datetime

import dns.rdata
import dns.rdataclass
import dns.rdatatype
import pytest

from nightloom_core.entities import EntityType, Sensitivity, build_entity
from nightloom_core.relationships import RelationType, validate_relationship
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    FailureReason,
    Finding,
    InputRefusedError,
    JobCancelledError,
    Mode,
    RefusalReason,
    RelationshipFinding,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.shuttle import REFUSAL_MESSAGES
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_dns import DnsShuttle, ReverseDnsShuttle
from nightloom_shuttle_dns.client import RESOLVER_RATE_LIMIT, Answer, DnsQueryError
from nightloom_shuttle_dns.config import DnsConfig
from nightloom_shuttle_dns.reverse import (
    CONFIRMED_NOTE,
    MAX_PTR_NAMES,
    NOTE_NOT_PUBLIC,
    SWEEP_BUDGET_SECONDS,
    UNCONFIRMED_NOTE,
    reverse_name,
    sweep_size_allowed,
)

R = dns.rdatatype.RdataType
D = EntityType.DOMAIN
IP = EntityType.IP_ADDRESS
NB = EntityType.NETBLOCK
REL = RelationType
CONFIG = DnsConfig()
NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)

# Public addresses, so the Shuttle queries them (documentation ranges are not
# public and would be skipped). Every answer comes from the fake below.
V4 = "93.184.215.14"
V4_PTR = "14.215.184.93.in-addr.arpa"
V6 = "2606:4700:4700::1111"
V6_PTR = "1.1.1.1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.7.4.0.0.7.4.6.0.6.2.ip6.arpa"


def records(rdtype: R, *texts: str, name: str) -> Answer:
    return Answer(
        name=name,
        records=tuple(dns.rdata.from_text(dns.rdataclass.IN, rdtype, text) for text in texts),
    )


def ptr(address: str, *targets: str, owner: str | None = None) -> tuple[tuple[str, R], Answer]:
    name = reverse_name(ipaddress.ip_address(address))
    return (name, R.PTR), records(R.PTR, *targets, name=owner or name)


def forward(name: str, *addresses: str, owner: str | None = None) -> tuple[tuple[str, R], Answer]:
    rdtype = R.AAAA if ":" in addresses[0] else R.A
    return (name, rdtype), records(rdtype, *addresses, name=owner or name)


class FakeDns:
    """Answers from a table; anything missing does not exist (NXDOMAIN / NoAnswer)."""

    def __init__(self, *entries: tuple[tuple[str, R], Answer | Exception]) -> None:
        self.table: dict[tuple[str, R], Answer | Exception] = dict(entries)
        self.asked: list[tuple[str, R]] = []

    async def query(self, name: str, rdtype: R) -> Answer | None:
        self.asked.append((name, rdtype))
        result = self.table.get((name, rdtype))
        if isinstance(result, Exception):
            raise result
        return result


def shuttle(fake: FakeDns, **kwargs: object) -> ReverseDnsShuttle:
    return ReverseDnsShuttle(client=fake, config=CONFIG, now=lambda: NOW, **kwargs)  # type: ignore[arg-type]


async def run(
    entity_type: EntityType, value: str, fake: FakeDns, **kwargs: object
) -> tuple[list[Finding], tuple[str, ...]]:
    entity = entity_input(entity_type, value)
    ctx = fake_context(ReverseDnsShuttle.meta)
    found = await collect(shuttle(fake, **kwargs), entity, ctx=ctx)
    assert_declared(ReverseDnsShuttle.meta, found, entity)
    return found, ctx.notes


def entities(found: list[Finding], entity_type: EntityType = D) -> dict[str, EntityFinding]:
    return {f.value: f for f in found if isinstance(f, EntityFinding) and f.type == entity_type}


def relationships(found: list[Finding], relation: RelationType) -> list[RelationshipFinding]:
    return [f for f in found if isinstance(f, RelationshipFinding) and f.type == relation]


def edges(found: list[Finding]) -> set[tuple[str, str, str]]:
    return {
        (f.source.value, f.type.value, f.target.value)
        for f in found
        if isinstance(f, RelationshipFinding)
    }


def assert_valid(found: list[Finding], entity: EntityInput) -> None:
    """Endpoints come before their relationships; properties and pairs pass the core rules."""
    seen = {(entity.type, entity.value)}
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties or {})
            seen.add((finding.type, finding.value))
        else:
            validate_relationship(finding.type, finding.source.type, finding.target.type)
            assert (finding.source.type, finding.source.value) in seen
            assert (finding.target.type, finding.target.value) in seen


# --- metadata and registration -------------------------------------------------------------


def test_meta() -> None:
    meta = ReverseDnsShuttle.meta
    assert meta.name == "reverse-dns"
    assert meta.title == "Reverse DNS (PTR)"
    assert meta.mode == Mode.PASSIVE
    assert meta.sensitivity == Sensitivity.INFRASTRUCTURE
    assert meta.network is True
    assert meta.input_types == {IP, NB}
    assert meta.output_entity_types == {D, IP}
    assert meta.output_relationship_types == {REL.HAS_PTR, REL.RESOLVES_TO, REL.WITHIN}
    assert meta.timeout_seconds == 300
    assert meta.timeout_seconds > SWEEP_BUDGET_SECONDS
    # The DNS Shuttle's resolver policy, rate limit and links, not copies of them.
    assert meta.rate_limit is RESOLVER_RATE_LIMIT is DnsShuttle.meta.rate_limit
    assert (meta.homepage, meta.terms_url) == (DnsShuttle.meta.homepage, DnsShuttle.meta.terms_url)
    assert meta.default_admiralty == "B2"


def test_registered_through_its_entry_point() -> None:
    shuttles = ShuttleRegistry.discover()
    assert shuttles.get("reverse-dns") is ReverseDnsShuttle
    assert shuttles.get("dns") is DnsShuttle


def test_check_configuration_is_the_dns_shuttles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIGHTLOOM_DNS_RESOLVERS", raising=False)
    ReverseDnsShuttle.check_configuration()
    monkeypatch.setenv("NIGHTLOOM_DNS_RESOLVERS", "10.0.0.53")
    with pytest.raises(Exception, match="not a public address"):
        ReverseDnsShuttle.check_configuration()


def test_reverse_names_and_sweep_sizes() -> None:
    assert reverse_name(ipaddress.ip_address(V4)) == V4_PTR
    assert reverse_name(ipaddress.ip_address(V6)) == V6_PTR
    assert sweep_size_allowed(ipaddress.ip_network("93.184.215.0/24"))
    assert sweep_size_allowed(ipaddress.ip_network("93.184.215.14/32"))
    assert not sweep_size_allowed(ipaddress.ip_network("93.184.214.0/23"))
    assert sweep_size_allowed(ipaddress.ip_network("2606:4700::/120"))
    assert not sweep_size_allowed(ipaddress.ip_network("2606:4700::/119"))


# --- one address -----------------------------------------------------------------------------


async def test_ipv4_ptr_forward_confirmed() -> None:
    fake = FakeDns(ptr(V4, "host.example.org."), forward("host.example.org", V4, "93.184.215.15"))
    found, notes = await run(IP, V4, fake)

    assert fake.asked == [(V4_PTR, R.PTR), ("host.example.org", R.A)]
    assert edges(found) == {
        (V4, "HAS_PTR", "host.example.org"),
        ("host.example.org", "RESOLVES_TO", V4),
    }
    domain = entities(found)["host.example.org"]
    assert (domain.admiralty, domain.confidence) == ("B2", 0.9)
    (has_ptr,) = relationships(found, REL.HAS_PTR)
    assert (has_ptr.admiralty, has_ptr.note) == ("B2", CONFIRMED_NOTE)
    assert has_ptr.note is not None and has_ptr.note.startswith("forward_confirmed: true")
    (resolves,) = relationships(found, REL.RESOLVES_TO)
    assert resolves.note is None and resolves.admiralty is None  # the Shuttle's B2
    # The input gets ptr_checked_at, last.
    assert found[-1] == EntityFinding(type=IP, value=V4, properties={"ptr_checked_at": NOW})
    assert_valid(found, entity_input(IP, V4))
    assert notes == (
        "1 address(es) queried, 1 with PTR names, 1 name(s) forward-confirmed, 0 quer(ies) failed.",
    )


async def test_ipv6_ptr_uses_ip6_arpa_and_aaaa() -> None:
    fake = FakeDns(ptr(V6, "one.one.one.one."), forward("one.one.one.one", V6))
    found, _ = await run(IP, V6, fake)
    assert fake.asked == [(V6_PTR, R.PTR), ("one.one.one.one", R.AAAA)]
    assert edges(found) == {
        (V6, "HAS_PTR", "one.one.one.one"),
        ("one.one.one.one", "RESOLVES_TO", V6),
    }


@pytest.mark.parametrize(
    "forward_answer",
    [
        forward("mail.example.net", "93.184.215.99"),  # resolves elsewhere
        None,  # the name does not exist forward
        (("mail.example.net", R.A), DnsQueryError("LifetimeTimeout")),  # cannot tell
    ],
)
async def test_unconfirmed_ptr_is_kept_lower_rated_without_resolves_to(
    forward_answer: tuple[tuple[str, R], Answer | Exception] | None,
) -> None:
    fake = FakeDns(ptr(V4, "mail.example.net."), *([forward_answer] if forward_answer else []))
    found, notes = await run(IP, V4, fake)
    assert edges(found) == {(V4, "HAS_PTR", "mail.example.net")}
    domain = entities(found)["mail.example.net"]
    assert (domain.admiralty, domain.confidence) == ("C3", 0.5)
    (has_ptr,) = relationships(found, REL.HAS_PTR)
    assert (has_ptr.admiralty, has_ptr.confidence) == ("C3", 0.5)
    assert has_ptr.note == UNCONFIRMED_NOTE
    assert "forward_confirmed: false" in UNCONFIRMED_NOTE
    assert "PTR not confirmed by forward DNS" in UNCONFIRMED_NOTE
    assert "anyone controlling the reverse zone can set any name" in UNCONFIRMED_NOTE
    failed = 1 if isinstance(fake.table.get(("mail.example.net", R.A)), Exception) else 0
    assert notes[-1].endswith(f"0 name(s) forward-confirmed, {failed} quer(ies) failed.")


async def test_forward_confirmation_through_a_cname() -> None:
    fake = FakeDns(
        ptr(V4, "www.example.org."), forward("www.example.org", V4, owner="edge.example.net")
    )
    found, _ = await run(IP, V4, fake)
    (resolves,) = relationships(found, REL.RESOLVES_TO)
    assert resolves.source.value == "www.example.org"
    assert resolves.note == "Resolved through a CNAME chain; canonical name edge.example.net."


async def test_classless_delegation_answers_are_used() -> None:
    """RFC 2317: the reverse name is a CNAME to another reverse name, which has the PTR."""
    fake = FakeDns(ptr(V4, "host.example.org.", owner="14.0-25.215.184.93.in-addr.arpa"))
    found, _ = await run(IP, V4, fake)
    assert (V4, "HAS_PTR", "host.example.org") in edges(found)


async def test_multiple_ptr_names_are_sorted_and_capped() -> None:
    targets = [f"h{i:02d}.example.org." for i in reversed(range(12))]
    fake = FakeDns(
        ptr(V4, *targets, "h00.example.org."),  # a repeat
        forward("h03.example.org", V4),
        forward("h07.example.org", V4),
    )
    found, notes = await run(IP, V4, fake)
    kept = [f.target.value for f in relationships(found, REL.HAS_PTR)]
    assert kept == [f"h{i:02d}.example.org" for i in range(MAX_PTR_NAMES)]
    assert [n for n, t in fake.asked if t == R.A] == kept  # names beyond the cap: no query
    confirmed = {f.source.value for f in relationships(found, REL.RESOLVES_TO)}
    assert confirmed == {"h03.example.org", "h07.example.org"}
    assert (
        "1 address(es) had more than 10 PTR names; the first 10 alphabetically were kept." in notes
    )
    assert notes[-1].startswith("1 address(es) queried, 1 with PTR names, 2 name(s) forward")


async def test_invalid_and_internal_ptr_names_are_dropped_and_counted() -> None:
    fake = FakeDns(
        ptr(
            V4,
            "ok.example.org.",
            "*.example.org.",  # a wildcard is not a host
            "host.123.",  # numeric top-level label
            "localhost.",  # single labels are not valid Domains
            "router.",
            "printer.local.",  # internal-only
            "14.215.184.93.in-addr.arpa.",  # a reverse name, not a host
        )
    )
    found, notes = await run(IP, V4, fake)
    assert set(entities(found)) == {"ok.example.org"}
    assert [n for n, t in fake.asked if t != R.PTR] == ["ok.example.org"]
    assert "Dropped 6 PTR name(s): 4 invalid, 2 internal-only." in notes


@pytest.mark.parametrize(
    "address", ["10.0.0.1", "192.168.1.1", "127.0.0.1", "100.64.0.1", "192.0.2.1", "fd00::1"]
)
async def test_private_addresses_are_skipped_without_any_query(address: str) -> None:
    fake = FakeDns()
    found, notes = await run(IP, address, fake)
    assert found == []
    assert fake.asked == []
    assert notes == (NOTE_NOT_PUBLIC,)


async def test_no_ptr_records_only_marks_the_address_checked() -> None:
    fake = FakeDns()
    found, notes = await run(IP, V4, fake)
    assert found == [EntityFinding(type=IP, value=V4, properties={"ptr_checked_at": NOW})]
    assert fake.asked == [(V4_PTR, R.PTR)]
    assert notes[-1].startswith("1 address(es) queried, 0 with PTR names")


async def test_a_failed_ptr_query_fails_the_job() -> None:
    fake = FakeDns(((V4_PTR, R.PTR), DnsQueryError("NoNameservers")))
    ctx = fake_context(ReverseDnsShuttle.meta)
    with pytest.raises(ShuttleFailedError) as failed:
        await collect(shuttle(fake), entity_input(IP, V4), ctx=ctx)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert ctx.notes[-1].endswith("1 quer(ies) failed.")


# --- netblock sweeps -------------------------------------------------------------------------

BLOCK = "93.184.215.0/28"


def sweep_names(block: str) -> list[tuple[str, R]]:
    return [(reverse_name(a), R.PTR) for a in ipaddress.ip_network(block)]


async def test_netblock_sweep() -> None:
    fake = FakeDns(
        ptr("93.184.215.0", "net.example.org."),
        ptr("93.184.215.5", "www.example.org."),
        ptr("93.184.215.15", "bcast.example.org."),
        forward("www.example.org", "93.184.215.5"),
    )
    found, notes = await run(NB, BLOCK, fake)

    ptr_queries = [q for q in fake.asked if q[1] == R.PTR]
    assert ptr_queries == sweep_names(BLOCK)  # every address, in order, .0 and .15 too
    assert len(ptr_queries) == 16
    ips = entities(found, IP)
    assert list(ips) == ["93.184.215.0", "93.184.215.5", "93.184.215.15"]
    assert all(f.properties == {"scope": "public", "ptr_checked_at": NOW} for f in ips.values())
    assert edges(found) == {
        ("93.184.215.0", "WITHIN", BLOCK),
        ("93.184.215.5", "WITHIN", BLOCK),
        ("93.184.215.15", "WITHIN", BLOCK),
        ("93.184.215.0", "HAS_PTR", "net.example.org"),
        ("93.184.215.5", "HAS_PTR", "www.example.org"),
        ("93.184.215.15", "HAS_PTR", "bcast.example.org"),
        ("www.example.org", "RESOLVES_TO", "93.184.215.5"),
    }
    ratings = {f.target.value: f.admiralty for f in relationships(found, REL.HAS_PTR)}
    assert ratings == {
        "net.example.org": "C3",
        "www.example.org": "B2",
        "bcast.example.org": "C3",
    }
    assert_valid(found, entity_input(NB, BLOCK))
    assert notes == (
        "16 address(es) queried, 3 with PTR names, 1 name(s) forward-confirmed, "
        "0 quer(ies) failed.",
    )


async def test_one_forward_query_per_name_in_a_sweep() -> None:
    fake = FakeDns(
        ptr("93.184.215.1", "shared.example.org."),
        ptr("93.184.215.2", "shared.example.org."),
        forward("shared.example.org", "93.184.215.2"),
    )
    found, _ = await run(NB, BLOCK, fake)
    assert fake.asked.count(("shared.example.org", R.A)) == 1
    ratings = {(f.source.value, f.admiralty) for f in relationships(found, REL.HAS_PTR)}
    assert ratings == {("93.184.215.1", "C3"), ("93.184.215.2", "B2")}


@pytest.mark.parametrize("block", ["93.184.214.0/23", "93.184.0.0/16", "0.0.0.0/0"])
async def test_netblocks_over_256_ipv4_addresses_are_refused(block: str) -> None:
    fake = FakeDns()
    with pytest.raises(InputRefusedError) as refused:
        await run(NB, block, fake)
    assert refused.value.reason is RefusalReason.NETBLOCK_TOO_LARGE
    assert str(refused.value) == REFUSAL_MESSAGES[RefusalReason.NETBLOCK_TOO_LARGE]
    assert "limited to 256 addresses; pick a /24 or smaller" in str(refused.value)
    assert fake.asked == []


async def test_a_full_ipv4_24_is_swept() -> None:
    fake = FakeDns()
    await run(NB, "93.184.215.0/24", fake)
    assert len(fake.asked) == 256


async def test_ipv6_120_is_swept_and_64_refused() -> None:
    fake = FakeDns(ptr("2606:4700:4700::1111", "one.one.one.one."))
    found, notes = await run(NB, "2606:4700:4700::1100/120", fake)
    assert [q for q in fake.asked if q[1] == R.PTR] == sweep_names("2606:4700:4700::1100/120")
    assert len(fake.asked) == 256 + 1  # every address, and one forward query
    assert edges(found) == {
        ("2606:4700:4700::1111", "WITHIN", "2606:4700:4700::1100/120"),
        ("2606:4700:4700::1111", "HAS_PTR", "one.one.one.one"),
    }
    assert ("one.one.one.one", R.AAAA) in fake.asked  # IPv6: forward-confirmed with AAAA
    assert notes[-1].startswith("256 address(es) queried, 1 with PTR names")

    fake = FakeDns()
    with pytest.raises(InputRefusedError) as refused:
        await run(NB, "2606:4700:4700::/64", fake)
    assert refused.value.reason is RefusalReason.NETBLOCK_TOO_LARGE
    assert fake.asked == []


async def test_private_netblocks_are_not_queried() -> None:
    fake = FakeDns()
    found, notes = await run(NB, "10.0.0.0/28", fake)
    assert found == []
    assert fake.asked == []
    assert notes == ("16 address(es) in the netblock are not public and were not queried.",)


async def test_cancellation_mid_sweep() -> None:
    event = asyncio.Event()

    class Cancelling(FakeDns):
        async def query(self, name: str, rdtype: R) -> Answer | None:
            answer = await super().query(name, rdtype)
            if len(self.asked) == 5:
                event.set()  # the user cancels while the fifth query is answered
            return answer

    fake = Cancelling(ptr("93.184.215.3", "h3.example.org."))
    ctx = ShuttleContext(ReverseDnsShuttle.meta, cancel_event=event)
    found: list[Finding] = []
    with pytest.raises(JobCancelledError):
        async for finding in shuttle(fake).run(ctx, entity_input(NB, BLOCK)):
            found.append(finding)
    assert len(fake.asked) == 5  # nothing more was asked after the cancellation
    assert (reverse_name(ipaddress.ip_address("93.184.215.4")), R.PTR) not in fake.asked
    assert ("93.184.215.3", "HAS_PTR", "h3.example.org") in edges(found)
    assert ctx.notes[-1].startswith("4 address(es) queried, 1 with PTR names")


async def test_servfail_and_timeouts_are_counted() -> None:
    servfail = DnsQueryError("NoNameservers")
    fake = FakeDns(
        ((reverse_name(ipaddress.ip_address("93.184.215.2")), R.PTR), servfail),
        ((reverse_name(ipaddress.ip_address("93.184.215.3")), R.PTR), servfail),
        ((reverse_name(ipaddress.ip_address("93.184.215.9")), R.PTR), DnsQueryError("Timeout")),
        ptr("93.184.215.4", "h4.example.org."),
        (("h4.example.org", R.A), servfail),
    )
    found, notes = await run(NB, BLOCK, fake)
    assert len([q for q in fake.asked if q[1] == R.PTR]) == 16
    assert ("93.184.215.4", "HAS_PTR", "h4.example.org") in edges(found)
    assert notes == (
        "16 address(es) queried, 1 with PTR names, 0 name(s) forward-confirmed, "
        "4 quer(ies) failed.",
    )


async def test_a_failing_resolver_stops_the_sweep() -> None:
    class Down(FakeDns):
        async def query(self, name: str, rdtype: R) -> Answer | None:
            await super().query(name, rdtype)
            raise DnsQueryError("NoNameservers")

    fake = Down()
    ctx = fake_context(ReverseDnsShuttle.meta)
    with pytest.raises(ShuttleFailedError) as failed:
        await collect(shuttle(fake), entity_input(NB, BLOCK), ctx=ctx)
    assert failed.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert len(fake.asked) == 10
    assert ctx.notes == (
        "Stopped after 10 failed queries in a row: 10 of 16 addresses.",
        "10 address(es) queried, 0 with PTR names, 0 name(s) forward-confirmed, "
        "10 quer(ies) failed.",
    )


async def test_a_sweep_where_every_query_failed_fails() -> None:
    fake = FakeDns(
        *(
            ((name, rdtype), DnsQueryError("Timeout"))
            for name, rdtype in sweep_names("93.184.215.0/30")
        )
    )
    with pytest.raises(ShuttleFailedError):
        await run(NB, "93.184.215.0/30", fake)


async def test_the_sweep_stops_at_its_time_budget() -> None:
    now = [0.0]

    class Slow(FakeDns):
        async def query(self, name: str, rdtype: R) -> Answer | None:
            now[0] += SWEEP_BUDGET_SECONDS / 4
            return await super().query(name, rdtype)

    fake = Slow(ptr("93.184.215.1", "h1.example.org."))
    found, notes = await run(NB, BLOCK, fake, clock=lambda: now[0])
    # Each query takes a quarter of the budget: three PTR queries and one forward query.
    assert len(fake.asked) == 4
    assert "Reached the time limit after 3 of 16 addresses." in notes
    assert ("93.184.215.1", "HAS_PTR", "h1.example.org") in edges(found)


async def test_the_sweep_stops_before_too_many_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nightloom_shuttle_dns.reverse.MAX_SWEEP_NAMES", 3)
    fake = FakeDns(
        *(ptr(f"93.184.215.{i}", f"a{i}.example.org.", f"b{i}.example.org.") for i in range(16))
    )
    found, notes = await run(NB, BLOCK, fake)
    assert len(relationships(found, REL.HAS_PTR)) == 2  # the second address would exceed 3
    assert (
        "Stopped after 3 PTR names (the job's finding limit): 1 of 16 addresses were swept."
        in notes
    )


async def test_notes_never_contain_names_or_addresses() -> None:
    fake = FakeDns(
        ptr("93.184.215.1", "secret-host.example.org.", "printer.local."),
        ((reverse_name(ipaddress.ip_address("93.184.215.2")), R.PTR), DnsQueryError("Timeout")),
    )
    _, notes = await run(NB, BLOCK, fake)
    for note in notes:
        assert "example" not in note and "93.184" not in note and "local" not in note


async def test_uses_the_real_resolver_client_without_a_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[tuple[str, ...]] = []

    class Recording(FakeDns):
        def __init__(self, resolvers: tuple[str, ...], limiter: object) -> None:
            super().__init__()
            built.append(resolvers)

    monkeypatch.setattr("nightloom_shuttle_dns.reverse.ResolverClient", Recording)
    monkeypatch.setenv("NIGHTLOOM_DNS_RESOLVERS", "9.9.9.9")
    found = await collect(ReverseDnsShuttle(), entity_input(IP, V4))
    assert built == [("9.9.9.9",)]
    (checked,) = found
    assert isinstance(checked, EntityFinding)
    assert checked.properties is not None
    assert checked.properties["ptr_checked_at"].tzinfo is not None


@pytest.mark.network
async def test_one_one_one_one_against_real_public_resolvers() -> None:
    """Optional: 1.1.1.1's PTR is one.one.one.one, which resolves back (`make test-network`)."""
    if os.environ.get("CI"):
        pytest.skip("No real DNS in CI.")
    entity = entity_input(IP, "1.1.1.1")
    found = await asyncio.wait_for(collect(ReverseDnsShuttle(), entity), timeout=30)
    assert_declared(ReverseDnsShuttle.meta, found, entity)
    assert ("1.1.1.1", "HAS_PTR", "one.one.one.one") in edges(found)
    assert ("one.one.one.one", "RESOLVES_TO", "1.1.1.1") in edges(found)
    (has_ptr,) = relationships(found, REL.HAS_PTR)
    assert has_ptr.note == CONFIRMED_NOTE
