"""Asking the configured public resolvers, and nothing else.

:class:`ResolverClient` wraps one dnspython async resolver per configured
address, each built with ``configure=False`` (so ``/etc/resolv.conf`` is never
read), no search list and no cache. A query goes to one resolver (they take
turns) and, if that one times out or fails, once more to the next: at most
:data:`TRIES` tries of :data:`QUERY_TIMEOUT_SECONDS` each. Every try first
takes a token from the Shuttle's rate limit for that resolver.

An answer that does not exist (NXDOMAIN) or has no records of the type
(NoAnswer) is ``None``, quietly. Only when every try failed does a query
raise :class:`DnsQueryError`.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import dns.asyncresolver
import dns.exception
import dns.name
import dns.rdata
import dns.rdatatype
import dns.resolver

from nightloom_sdk.meta import RateLimit
from nightloom_sdk.ratelimit import HostRateLimiter

QUERY_TIMEOUT_SECONDS = 3.0
TRIES = 2
# The rate limit every Shuttle of this package declares, applied per resolver
# and job: well within what public resolvers expect from one client.
RESOLVER_RATE_LIMIT = RateLimit(requests=10, period_seconds=1)


class DnsQueryError(Exception):
    """Every try of a query failed (timeouts, SERVFAIL, unreachable resolvers)."""


@dataclass(frozen=True)
class Answer:
    """The records of one type for a name.

    ``name`` is the owner of the records without the final dot: the queried
    name, or the end of the CNAME chain the resolver followed.
    """

    name: str
    records: tuple[dns.rdata.Rdata, ...]


class DnsClient(Protocol):
    async def query(self, name: str, rdtype: dns.rdatatype.RdataType) -> Answer | None:
        """The answer, ``None`` if the name or the type does not exist; may raise DnsQueryError."""


class _Resolve(Protocol):
    def resolve(
        self,
        qname: dns.name.Name,
        rdtype: dns.rdatatype.RdataType,
        *,
        raise_on_no_answer: bool,
        search: bool,
    ) -> Awaitable[dns.resolver.Answer]: ...


def make_resolver(address: str, *, port: int = 53) -> dns.asyncresolver.Resolver:
    """A resolver that asks only ``address``: no resolv.conf, search list or cache."""
    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = [address]
    resolver.port = port
    resolver.timeout = QUERY_TIMEOUT_SECONDS
    resolver.lifetime = QUERY_TIMEOUT_SECONDS
    resolver.search = []
    resolver.use_search_by_default = False
    resolver.cache = None
    return resolver


ResolverFactory = Callable[[str], _Resolve]

# Failures worth one more try at the next resolver. OSError covers an
# unreachable network (e.g. an IPv6 resolver on a host without IPv6).
_RETRYABLE = (dns.exception.DNSException, OSError)


class ResolverClient:
    """Queries the configured resolvers in turn, with a rate limit per resolver."""

    def __init__(
        self,
        resolvers: Sequence[str],
        limiter: HostRateLimiter,
        *,
        factory: ResolverFactory = make_resolver,
        tries: int = TRIES,
    ) -> None:
        if not resolvers:
            raise ValueError("at least one resolver is needed")
        self._resolvers = [(address, factory(address)) for address in resolvers]
        self._limiter = limiter
        self._tries = tries
        self._next = 0

    async def query(self, name: str, rdtype: dns.rdatatype.RdataType) -> Answer | None:
        # Absolute (a final dot), so no search domain could ever be appended.
        qname = dns.name.from_text(name)
        start = self._next
        self._next = (self._next + 1) % len(self._resolvers)
        failure: BaseException | None = None
        for attempt in range(self._tries):
            address, resolver = self._resolvers[(start + attempt) % len(self._resolvers)]
            await self._limiter.acquire(address)
            try:
                answer = await resolver.resolve(
                    qname, rdtype, raise_on_no_answer=False, search=False
                )
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.YXDOMAIN):
                return None
            except _RETRYABLE as error:
                failure = error
                continue
            if answer.rrset is None:
                return None
            return Answer(
                name=answer.canonical_name.to_text(omit_final_dot=True).lower(),
                records=tuple(answer.rrset),
            )
        raise DnsQueryError(type(failure).__name__) from failure


__all__ = [
    "QUERY_TIMEOUT_SECONDS",
    "RESOLVER_RATE_LIMIT",
    "TRIES",
    "Answer",
    "DnsClient",
    "DnsQueryError",
    "ResolverClient",
    "make_resolver",
]
