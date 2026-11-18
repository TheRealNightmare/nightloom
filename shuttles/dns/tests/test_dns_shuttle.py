"""The DNS Shuttle with a fake resolver: no network."""

import asyncio
import os

import dns.rdata
import dns.rdataclass
import dns.rdatatype
import pytest

from nightloom_core.entities import EntityType, Sensitivity, build_entity
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
)
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_dns import DnsShuttle
from nightloom_shuttle_dns.client import Answer, DnsQueryError
from nightloom_shuttle_dns.config import DnsConfig
from nightloom_shuttle_dns.shuttle import (
    MAX_CNAME_DEPTH,
    PRIVATE_NOTE,
    AllQueriesFailedError,
)

R = dns.rdatatype.RdataType
D = EntityType.DOMAIN
IP = EntityType.IP_ADDRESS
CONFIG = DnsConfig()


def answer(rdtype: R, *texts: str, name: str = "example.org") -> Answer:
    return Answer(
        name=name,
        records=tuple(dns.rdata.from_text(dns.rdataclass.IN, rdtype, text) for text in texts),
    )


class FakeDns:
    """Answers from a table; anything missing does not exist (NXDOMAIN / NoAnswer)."""

    def __init__(self, table: dict[tuple[str, R], Answer | Exception] | None = None) -> None:
        self.table = table or {}
        self.asked: list[tuple[str, R]] = []

    async def query(self, name: str, rdtype: R) -> Answer | None:
        self.asked.append((name, rdtype))
        result = self.table.get((name, rdtype))
        if isinstance(result, Exception):
            raise result
        return result


async def run(
    value: str, fake: FakeDns, *, config: DnsConfig = CONFIG, raw: bool = False
) -> list[Finding]:
    entity = (
        EntityInput(type=D, value=value, key=f"domain:{value}") if raw else entity_input(D, value)
    )
    found = await collect(DnsShuttle(client=fake, config=config), entity)
    assert_declared(DnsShuttle.meta, found, entity)
    return found


def entities(found: list[Finding], entity_type: EntityType = D) -> dict[str, EntityFinding]:
    return {f.value: f for f in found if isinstance(f, EntityFinding) and f.type == entity_type}


def edges(found: list[Finding]) -> set[tuple[str, str, str]]:
    return {
        (f.source.value, f.type.value, f.target.value)
        for f in found
        if isinstance(f, RelationshipFinding)
    }


def input_properties(found: list[Finding], name: str = "example.org") -> dict[str, object]:
    finding = entities(found).get(name)
    return dict(finding.properties or {}) if finding else {}


def test_meta() -> None:
    meta = DnsShuttle.meta
    assert meta.name == "dns"
    assert meta.mode == Mode.PASSIVE
    assert meta.sensitivity == Sensitivity.INFRASTRUCTURE
    assert meta.network is True
    assert meta.rate_limit is not None and meta.rate_limit.requests == 10
    assert meta.timeout_seconds == 60
    assert meta.input_types == {D}
    assert meta.output_entity_types == {D, IP}
    assert meta.output_relationship_types == {
        RelationType.RESOLVES_TO,
        RelationType.ALIAS_OF,
        RelationType.HAS_MX,
        RelationType.HAS_NS,
    }
    assert meta.terms_url and meta.homepage


def test_registered_through_its_entry_point() -> None:
    assert ShuttleRegistry.discover().get("dns") is DnsShuttle


def test_check_configuration_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIGHTLOOM_DNS_RESOLVERS", raising=False)
    DnsShuttle.check_configuration()
    monkeypatch.setenv("NIGHTLOOM_DNS_RESOLVERS", "10.0.0.53")
    with pytest.raises(Exception, match="not a public address"):
        DnsShuttle.check_configuration()


async def test_every_record_type() -> None:
    fake = FakeDns(
        {
            ("example.org", R.A): answer(R.A, "93.184.215.14", "192.0.2.10"),
            ("example.org", R.AAAA): answer(R.AAAA, "2606:2800:21f:cb07:6820:80da:af6b:8b2c"),
            ("example.org", R.MX): answer(R.MX, "20 mx2.example.org.", "10 mx1.example.org."),
            ("example.org", R.NS): answer(R.NS, "b.iana-servers.net.", "a.iana-servers.net."),
            ("example.org", R.TXT): answer(
                R.TXT, '"v=spf1 " "-all"', '"google-site-verification=abc"'
            ),
            ("example.org", R.SOA): answer(
                R.SOA, "ns.icann.org. noc.dns.icann.org. 2025011000 7200 3600 1209600 3600"
            ),
            ("example.org", R.CAA): answer(R.CAA, '0 issue "letsencrypt.org"', '0 iodef "x"'),
            ("_dmarc.example.org", R.TXT): answer(
                R.TXT, '"v=DMARC1; p=reject"', '"unrelated"', name="_dmarc.example.org"
            ),
        }
    )
    found = await run("example.org", fake)

    assert {(n, t) for n, t in fake.asked} == {
        *(("example.org", t) for t in (R.A, R.AAAA, R.CNAME, R.MX, R.NS, R.TXT, R.SOA, R.CAA)),
        ("_dmarc.example.org", R.TXT),
    }
    assert edges(found) == {
        ("example.org", "RESOLVES_TO", "93.184.215.14"),
        ("example.org", "RESOLVES_TO", "192.0.2.10"),
        ("example.org", "RESOLVES_TO", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"),
        ("example.org", "HAS_MX", "mx1.example.org"),
        ("example.org", "HAS_MX", "mx2.example.org"),
        ("example.org", "HAS_NS", "a.iana-servers.net"),
        ("example.org", "HAS_NS", "b.iana-servers.net"),
    }
    mx_notes = {
        f.target.value: f.note
        for f in found
        if isinstance(f, RelationshipFinding) and f.type == RelationType.HAS_MX
    }
    assert mx_notes == {
        "mx1.example.org": "MX preference 10",
        "mx2.example.org": "MX preference 20",
    }
    assert set(entities(found, IP)) == {
        "93.184.215.14",
        "192.0.2.10",
        "2606:2800:21f:cb07:6820:80da:af6b:8b2c",
    }
    # Every endpoint is yielded before the relationship that uses it.
    seen = {"example.org"}
    for finding in found:
        if isinstance(finding, EntityFinding):
            seen.add(finding.value)
        else:
            assert {finding.source.value, finding.target.value} <= seen

    properties = input_properties(found)
    assert properties == {
        "dns_txt": ["google-site-verification=abc", "v=spf1 -all"],
        "dns_spf": ["v=spf1 -all"],
        "dns_dmarc": ["v=DMARC1; p=reject"],
        "dns_mx": ["10 mx1.example.org", "20 mx2.example.org"],
        "dns_caa": ['0 iodef "x"', '0 issue "letsencrypt.org"'],
        "dns_soa_primary_ns": "ns.icann.org",
        "dns_soa_responsible": "noc.dns.icann.org",
        "dns_soa_serial": 2025011000,
        "dns_soa_refresh": 7200,
        "dns_soa_retry": 3600,
        "dns_soa_expire": 1209600,
        "dns_soa_minimum": 3600,
    }
    # The runner accepts them: they validate against the Domain's property model.
    build_entity(D, "example.org", properties)
    # The input's own finding comes last, after every relationship.
    assert isinstance(found[-1], EntityFinding) and found[-1].value == "example.org"


@pytest.mark.parametrize(
    ("address", "scope"),
    [
        ("10.1.2.3", "private"),
        ("192.168.0.1", "private"),
        ("127.0.0.1", "private"),
        ("fd00::1", "private"),
        ("100.64.0.1", "private"),  # shared address space
        ("93.184.215.14", "public"),
        ("2606:4700::1", "public"),
    ],
)
async def test_private_addresses_are_recorded_and_marked(address: str, scope: str) -> None:
    rdtype = R.AAAA if ":" in address else R.A
    found = await run("example.org", FakeDns({("example.org", rdtype): answer(rdtype, address)}))
    ip = entities(found, IP)[address]
    assert ip.properties == {"scope": scope}
    relation = next(f for f in found if isinstance(f, RelationshipFinding))
    if scope == "private":
        assert ip.note == PRIVATE_NOTE
        assert relation.note == PRIVATE_NOTE
        assert "never contacted" in PRIVATE_NOTE
    else:
        assert ip.note is None and relation.note is None


async def test_cname_chain_is_followed_and_addresses_belong_to_its_end() -> None:
    fake = FakeDns(
        {
            ("www.example.org", R.CNAME): answer(
                R.CNAME, "edge.example.net.", name="www.example.org"
            ),
            ("edge.example.net", R.CNAME): answer(
                R.CNAME, "lb.cdn.example.com.", name="edge.example.net"
            ),
            # The resolver follows the chain for A queries.
            ("www.example.org", R.A): answer(R.A, "203.0.113.7", name="lb.cdn.example.com"),
            # Records of the alias target are not the input's: no MX for www.
            ("www.example.org", R.MX): answer(
                R.MX, "10 mx.example.com.", name="lb.cdn.example.com"
            ),
        }
    )
    found = await run("www.example.org", fake)
    assert edges(found) == {
        ("www.example.org", "ALIAS_OF", "edge.example.net"),
        ("edge.example.net", "ALIAS_OF", "lb.cdn.example.com"),
        ("lb.cdn.example.com", "RESOLVES_TO", "203.0.113.7"),
    }
    assert input_properties(found, "www.example.org") == {}
    assert ("lb.cdn.example.com", R.CNAME) in fake.asked


async def test_cname_chain_depth_is_capped() -> None:
    names = [f"n{i}.example.org" for i in range(10)]
    table: dict[tuple[str, R], Answer | Exception] = {
        (names[i], R.CNAME): answer(R.CNAME, f"{names[i + 1]}.", name=names[i]) for i in range(9)
    }
    table[(names[0], R.A)] = answer(R.A, "203.0.113.9", name=names[9])
    fake = FakeDns(table)
    found = await run(names[0], fake)
    aliases = [e for e in edges(found) if e[1] == "ALIAS_OF"]
    assert len(aliases) == MAX_CNAME_DEPTH == 5
    assert (names[4], "ALIAS_OF", names[5]) in aliases
    assert (names[5], R.CNAME) not in fake.asked
    # The addresses' owner is beyond the cap: they stay on the input, with a note.
    resolves = next(
        f for f in found if isinstance(f, RelationshipFinding) and f.type.value == "RESOLVES_TO"
    )
    assert resolves.source.value == names[0]
    assert resolves.note is not None and names[9] in resolves.note


async def test_cname_loops_and_internal_targets_stop_the_chain() -> None:
    loop = FakeDns(
        {
            ("a.example.org", R.CNAME): answer(R.CNAME, "b.example.org.", name="a.example.org"),
            ("b.example.org", R.CNAME): answer(R.CNAME, "a.example.org.", name="b.example.org"),
        }
    )
    assert edges(await run("a.example.org", loop)) == {
        ("a.example.org", "ALIAS_OF", "b.example.org")
    }

    internal = FakeDns(
        {("a.example.org", R.CNAME): answer(R.CNAME, "host.corp.", name="a.example.org")}
    )
    assert edges(await run("a.example.org", internal)) == {
        ("a.example.org", "ALIAS_OF", "host.corp")
    }
    # The internal target is recorded (public DNS published it) but never queried.
    assert ("host.corp", R.CNAME) not in internal.asked


async def test_null_mx_is_a_property_only() -> None:
    found = await run("example.org", FakeDns({("example.org", R.MX): answer(R.MX, "0 .")}))
    assert edges(found) == set()
    assert input_properties(found) == {"dns_mx": ["0 ."]}


async def test_txt_records_are_capped_and_cleaned() -> None:
    long = "x" * 3000
    texts = [f'"record-{i:03d}"' for i in range(80)] + [f'"{long[:255]}" "{long[:255]}"']
    texts.append('"line\\010break"')
    fake = FakeDns({("example.org", R.TXT): answer(R.TXT, *texts)})
    properties = input_properties(await run("example.org", fake))
    txt = properties["dns_txt"]
    assert isinstance(txt, list)
    assert len(txt) == 50
    assert all(len(record) <= 1000 for record in txt)
    assert txt[0] == "line�break"  # control characters replaced; sorted first
    build_entity(D, "example.org", properties)


async def test_long_txt_record_is_truncated_to_1000_characters() -> None:
    chunks = " ".join(f'"{"y" * 255}"' for _ in range(6))  # 1,530 characters
    fake = FakeDns({("example.org", R.TXT): answer(R.TXT, chunks, '"tab\\009here"')})
    txt = input_properties(await run("example.org", fake))["dns_txt"]
    assert txt == ["tab�here", "y" * 1000]


async def test_spf_and_dmarc_are_marked() -> None:
    fake = FakeDns(
        {
            ("example.org", R.TXT): answer(
                R.TXT, '"V=SPF1 include:_spf.example.net ~all"', '"v=spf10 nope"', '"v=spf1"'
            ),
            ("_dmarc.example.org", R.TXT): answer(
                R.TXT,
                '"v=DMARC1;p=none"',
                '"v=DMARC10"',
                # Behind a CNAME to a reporting provider: still the domain's policy.
                name="example.org._report.example.net",
            ),
        }
    )
    properties = input_properties(await run("example.org", fake))
    assert properties["dns_spf"] == ["V=SPF1 include:_spf.example.net ~all", "v=spf1"]
    assert properties["dns_dmarc"] == ["v=DMARC1;p=none"]


async def test_dmarc_names_are_not_asked_for_their_own_dmarc() -> None:
    fake = FakeDns()
    await run("_dmarc.example.org", fake)
    assert all(not name.startswith("_dmarc._dmarc") for name, _ in fake.asked)
    assert len(fake.asked) == 8


async def test_nothing_found_is_not_an_error() -> None:
    fake = FakeDns()
    assert await run("nothing-here.example.org", fake) == []


async def test_failed_queries_are_skipped() -> None:
    fake = FakeDns(
        {
            ("example.org", R.A): answer(R.A, "93.184.215.14"),
            ("example.org", R.MX): DnsQueryError("LifetimeTimeout"),
            ("example.org", R.TXT): DnsQueryError("NoNameservers"),
        }
    )
    found = await run("example.org", fake)
    assert edges(found) == {("example.org", "RESOLVES_TO", "93.184.215.14")}


async def test_job_fails_when_no_query_gets_an_answer() -> None:
    class Down(FakeDns):
        async def query(self, name: str, rdtype: R) -> Answer | None:
            raise DnsQueryError("LifetimeTimeout")

    with pytest.raises(AllQueriesFailedError):
        await run("example.org", Down())


@pytest.mark.parametrize(
    "value",
    [
        "localhost",
        "router",  # single label
        "printer.local",
        "app.localhost",
        "db.internal",
        "nas.home.arpa",
        "pc.lan",
        "files.corp",
        "wiki.intranet",
        "service.test",
        "x.invalid",
        "www.example",
        "nightloom-test.example",  # the test zone, outside test mode
        "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion",
    ],
)
async def test_internal_names_are_refused_before_any_query(value: str) -> None:
    fake = FakeDns()
    with pytest.raises(InputRefusedError) as refused:
        await run(value, fake, raw=True)
    assert refused.value.reason == RefusalReason.INTERNAL_NAME
    assert fake.asked == []


async def test_test_zone_is_allowed_only_in_test_mode() -> None:
    fake = FakeDns(
        {
            ("www.nightloom-test.example", R.A): answer(
                R.A, "192.0.2.80", name="www.nightloom-test.example"
            )
        }
    )
    found = await run(
        "www.nightloom-test.example",
        fake,
        config=DnsConfig(resolvers=("172.30.0.53",), allow_private_resolvers=True),
    )
    assert edges(found) == {("www.nightloom-test.example", "RESOLVES_TO", "192.0.2.80")}
    with pytest.raises(InputRefusedError):
        await run("printer.local", FakeDns(), config=DnsConfig(allow_private_resolvers=True))


async def test_public_names_that_look_internal_are_allowed() -> None:
    for value in ("example.org", "local.example.org", "corp.example.com", "test.example.net"):
        assert await run(value, FakeDns()) == []


async def test_wildcard_names_are_not_queried() -> None:
    fake = FakeDns()
    assert await run("*.example.org", fake) == []
    assert fake.asked == []


async def test_cancellation_stops_the_cname_chain() -> None:
    fake = FakeDns(
        {
            ("a.example.org", R.CNAME): answer(R.CNAME, "b.example.org.", name="a.example.org"),
            ("b.example.org", R.CNAME): answer(R.CNAME, "c.example.org.", name="b.example.org"),
        }
    )
    ctx = fake_context(DnsShuttle.meta, cancelled=True)
    found: list[Finding] = []
    with pytest.raises(Exception, match="cancelled"):
        async for finding in DnsShuttle(client=fake, config=CONFIG).run(
            ctx, entity_input(D, "a.example.org")
        ):
            found.append(finding)
    assert ("b.example.org", R.CNAME) not in fake.asked


async def test_uses_the_real_resolver_client_without_a_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a fake, the Shuttle builds a ResolverClient for the configured resolvers."""
    built: list[tuple[str, ...]] = []

    class Recording(FakeDns):
        def __init__(self, resolvers: tuple[str, ...], limiter: object) -> None:
            super().__init__()
            built.append(resolvers)

    monkeypatch.setattr("nightloom_shuttle_dns.shuttle.ResolverClient", Recording)
    monkeypatch.setenv("NIGHTLOOM_DNS_RESOLVERS", "9.9.9.9")
    entity = entity_input(D, "example.org")
    assert await collect(DnsShuttle(), entity) == []
    assert built == [("9.9.9.9",)]


@pytest.mark.network
async def test_example_org_against_real_public_resolvers() -> None:
    """Optional: asks the default public resolvers for example.org (`make test-network`)."""
    if os.environ.get("CI"):
        pytest.skip("No real DNS in CI.")
    entity = entity_input(D, "example.org")
    found = await asyncio.wait_for(collect(DnsShuttle(), entity), timeout=30)
    assert_declared(DnsShuttle.meta, found, entity)
    assert any(f.type == RelationType.HAS_NS for f in found if isinstance(f, RelationshipFinding))
    assert entities(found, IP)
    assert EntityRef(type=D, value="example.org") in {
        f.source for f in found if isinstance(f, RelationshipFinding)
    }
