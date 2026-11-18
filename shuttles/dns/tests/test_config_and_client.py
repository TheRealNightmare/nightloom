"""Resolver policy (configuration), internal names and the resolver client, without network."""

from collections.abc import Awaitable, Callable

import dns.message
import dns.name
import dns.rdatatype
import dns.resolver
import pytest

from nightloom_sdk import ShuttleConfigurationError
from nightloom_sdk.meta import RateLimit
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_shuttle_dns.client import (
    QUERY_TIMEOUT_SECONDS,
    DnsQueryError,
    ResolverClient,
    make_resolver,
)
from nightloom_shuttle_dns.config import DEFAULT_RESOLVERS, DnsConfig, load_config
from nightloom_shuttle_dns.names import is_internal_name

R = dns.rdatatype.RdataType

# --- configuration -----------------------------------------------------------------------


def test_defaults_are_cloudflare_and_quad9() -> None:
    assert load_config({}) == DnsConfig(resolvers=("1.1.1.1", "9.9.9.9"))
    assert DEFAULT_RESOLVERS == ("1.1.1.1", "9.9.9.9")
    assert load_config({"NIGHTLOOM_DNS_RESOLVERS": "  "}).resolvers == DEFAULT_RESOLVERS


def test_resolvers_are_overridable() -> None:
    config = load_config({"NIGHTLOOM_DNS_RESOLVERS": " 8.8.8.8 , 2606:4700:4700::1111,8.8.8.8, "})
    assert config.resolvers == ("8.8.8.8", "2606:4700:4700::1111")
    assert config.allow_private_resolvers is False


@pytest.mark.parametrize(
    "resolvers",
    ["10.0.0.53", "1.1.1.1,192.168.1.1", "127.0.0.53", "::1", "fd00::53", "169.254.169.254"],
)
def test_private_resolvers_are_refused(resolvers: str) -> None:
    with pytest.raises(ShuttleConfigurationError, match="not a public address"):
        load_config({"NIGHTLOOM_DNS_RESOLVERS": resolvers})


def test_resolvers_must_be_addresses() -> None:
    with pytest.raises(ShuttleConfigurationError, match="must list IP addresses"):
        load_config({"NIGHTLOOM_DNS_RESOLVERS": "dns.google"})
    with pytest.raises(ShuttleConfigurationError, match="more than 8"):
        load_config({"NIGHTLOOM_DNS_RESOLVERS": ",".join(f"1.1.1.{i}" for i in range(9))})


def test_private_resolvers_only_in_the_test_mode() -> None:
    env = {
        "NIGHTLOOM_DNS_RESOLVERS": "172.30.0.53",
        "NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS": "true",
    }
    config = load_config(env)
    assert config == DnsConfig(resolvers=("172.30.0.53",), allow_private_resolvers=True)
    assert load_config({**env, "NIGHTLOOM_ENV": "test"}).allow_private_resolvers
    with pytest.raises(ShuttleConfigurationError, match="refused with NIGHTLOOM_ENV=production"):
        load_config({**env, "NIGHTLOOM_ENV": " Production "})


@pytest.mark.parametrize(
    ("raw", "value"), [("1", True), ("YES", True), ("off", False), ("", False)]
)
def test_flag_values(raw: str, value: bool) -> None:
    flag = load_config({"NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS": raw}).allow_private_resolvers
    assert flag is value


def test_bad_flag() -> None:
    with pytest.raises(ShuttleConfigurationError, match="must be true or false"):
        load_config({"NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS": "maybe"})


def test_production_without_the_flag_is_fine() -> None:
    assert load_config({"NIGHTLOOM_ENV": "production"}).resolvers == DEFAULT_RESOLVERS


# --- internal names ----------------------------------------------------------------------


def test_internal_names() -> None:
    assert is_internal_name("localhost")
    assert is_internal_name("printer.local.")
    assert is_internal_name("A.B.CORP")
    assert not is_internal_name("corp.example.org")
    assert not is_internal_name("example.org")
    assert is_internal_name("nightloom-test.example")
    assert not is_internal_name("nightloom-test.example", allow_test_zone=True)
    assert not is_internal_name("mx.nightloom-test.example", allow_test_zone=True)
    assert is_internal_name("other.example", allow_test_zone=True)
    assert is_internal_name("xnightloom-test.example", allow_test_zone=True)


# --- the resolver client -------------------------------------------------------------------


def test_resolvers_never_read_the_host_configuration() -> None:
    resolver = make_resolver("9.9.9.9")
    assert resolver.nameservers == ["9.9.9.9"]
    assert resolver.port == 53
    assert resolver.search == []
    assert resolver.use_search_by_default is False
    assert resolver.cache is None
    assert resolver.timeout == resolver.lifetime == QUERY_TIMEOUT_SECONDS == 3.0
    assert make_resolver("192.0.2.53", port=5353).port == 5353


def real_answer(name: str, rdtype: R, *texts: str) -> dns.resolver.Answer:
    """A dnspython Answer, built from a fake response message."""
    qname = dns.name.from_text(name)
    query = dns.message.make_query(qname, rdtype)
    response = dns.message.make_response(query)
    assert isinstance(response, dns.message.QueryMessage)
    if texts:
        rrset = response.find_rrset(response.answer, qname, dns.rdataclass.IN, rdtype, create=True)
        for text in texts:
            rrset.add(dns.rdata.from_text(dns.rdataclass.IN, rdtype, text), 300)
    return dns.resolver.Answer(qname, rdtype, dns.rdataclass.IN, response)


Behaviour = Callable[[dns.name.Name, R], Awaitable[dns.resolver.Answer]]


class FakeResolver:
    def __init__(self, address: str, log: list[tuple[str, str]], behaviour: Behaviour) -> None:
        self.address = address
        self.log = log
        self.behaviour = behaviour

    async def resolve(
        self, qname: dns.name.Name, rdtype: R, *, raise_on_no_answer: bool, search: bool
    ) -> dns.resolver.Answer:
        assert raise_on_no_answer is False and search is False
        assert qname.is_absolute()
        self.log.append((self.address, qname.to_text()))
        return await self.behaviour(qname, rdtype)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def client_with(
    behaviours: dict[str, Behaviour], *, limiter: HostRateLimiter | None = None
) -> tuple[ResolverClient, list[tuple[str, str]]]:
    log: list[tuple[str, str]] = []
    client = ResolverClient(
        list(behaviours),
        limiter or HostRateLimiter(RateLimit(requests=100, period_seconds=1)),
        factory=lambda address: FakeResolver(address, log, behaviours[address]),
    )
    return client, log


async def ok(qname: dns.name.Name, rdtype: R) -> dns.resolver.Answer:
    return real_answer(qname.to_text(), rdtype, "192.0.2.1")


async def timeout(qname: dns.name.Name, rdtype: R) -> dns.resolver.Answer:
    raise dns.resolver.LifetimeTimeout(timeout=3.0, errors=[])  # type: ignore[no-untyped-call]


async def unreachable(qname: dns.name.Name, rdtype: R) -> dns.resolver.Answer:
    raise OSError("Network is unreachable")


async def nxdomain(qname: dns.name.Name, rdtype: R) -> dns.resolver.Answer:
    raise dns.resolver.NXDOMAIN()  # type: ignore[no-untyped-call]


async def no_answer(qname: dns.name.Name, rdtype: R) -> dns.resolver.Answer:
    return real_answer(qname.to_text(), rdtype)


async def test_answers_are_returned_with_their_owner() -> None:
    client, log = client_with({"1.1.1.1": ok})
    answer = await client.query("Example.org", R.A)
    assert answer is not None
    assert answer.name == "example.org"
    assert [str(r) for r in answer.records] == ["192.0.2.1"]
    assert log == [("1.1.1.1", "Example.org.")]


async def test_nxdomain_and_no_answer_are_quiet() -> None:
    client, _ = client_with({"1.1.1.1": nxdomain})
    assert await client.query("missing.example.org", R.A) is None
    client, _ = client_with({"1.1.1.1": no_answer})
    assert await client.query("example.org", R.CAA) is None


async def test_a_failed_try_moves_to_the_next_resolver() -> None:
    client, log = client_with({"1.1.1.1": timeout, "9.9.9.9": ok})
    assert await client.query("example.org", R.A) is not None
    assert [address for address, _ in log] == ["1.1.1.1", "9.9.9.9"]


async def test_resolvers_take_turns() -> None:
    client, log = client_with({"1.1.1.1": ok, "9.9.9.9": ok})
    for _ in range(4):
        await client.query("example.org", R.A)
    assert [address for address, _ in log] == ["1.1.1.1", "9.9.9.9", "1.1.1.1", "9.9.9.9"]


async def test_two_tries_then_an_error() -> None:
    client, log = client_with({"1.1.1.1": timeout, "9.9.9.9": unreachable})
    with pytest.raises(DnsQueryError, match=r"LifetimeTimeout|OSError"):
        await client.query("example.org", R.A)
    assert len(log) == 2

    client, log = client_with({"1.1.1.1": timeout})  # one resolver: both tries on it
    with pytest.raises(DnsQueryError, match="LifetimeTimeout"):
        await client.query("example.org", R.A)
    assert len(log) == 2


async def test_rate_limit_applies_per_resolver() -> None:
    clock = Clock()
    limiter = HostRateLimiter(
        RateLimit(requests=2, period_seconds=1), clock=clock, sleep=clock.sleep
    )
    client, log = client_with({"1.1.1.1": ok, "9.9.9.9": ok}, limiter=limiter)
    for _ in range(4):  # two per resolver: within each one's burst
        await client.query("example.org", R.A)
    assert clock.now == 0
    await client.query("example.org", R.A)  # a third for 1.1.1.1 waits for a token
    assert clock.now == pytest.approx(0.5)
    assert len(log) == 5


def test_a_client_needs_resolvers() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ResolverClient([], HostRateLimiter(RateLimit(requests=1, period_seconds=1)))
