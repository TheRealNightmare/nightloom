"""Which RDAP server to ask: IANA's RDAP bootstrap registries (RFC 9224).

IANA publishes four JSON files at https://data.iana.org/rdap/ that map
top-level domains (``dns.json``), IPv4 and IPv6 ranges (``ipv4.json``,
``ipv6.json``) and AS number ranges (``asn.json``) to the base URLs of the
RDAP servers responsible for them. :class:`BootstrapCache` fetches each file
through the Shuttle's guarded HTTP client and keeps it in this process for
:data:`CACHE_SECONDS`, one fetch per file even when several jobs need it at
once. If a refresh fails, a stale copy is used rather than failing the job.

Lookups pick the most specific entry (the longest matching domain suffix,
the longest matching IP prefix, the smallest AS number range), and prefer an
``https`` URL when an entry lists several.
"""

import asyncio
import ipaddress
import time
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from nightloom_sdk import JobCancelledError

BOOTSTRAP_BASE_URL = "https://data.iana.org/rdap/"
CACHE_SECONDS = 24 * 60 * 60
# More entries than any bootstrap file has had; a larger one is malformed.
MAX_ENTRIES = 10_000

# The Regional Internet Registries' RDAP servers, for the ``registry`` property.
RIR_HOSTS: dict[str, str] = {
    "rdap.afrinic.net": "AFRINIC",
    "rdap.apnic.net": "APNIC",
    "rdap.arin.net": "ARIN",
    "rdap.lacnic.net": "LACNIC",
    "rdap.db.ripe.net": "RIPE NCC",
}

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Clock = Callable[[], float]
Fetch = Callable[[str], Awaitable[Mapping[str, Any]]]


class Registry(StrEnum):
    """One bootstrap file."""

    DNS = "dns.json"
    IPV4 = "ipv4.json"
    IPV6 = "ipv6.json"
    ASN = "asn.json"

    @property
    def url(self) -> str:
        return BOOTSTRAP_BASE_URL + self.value


class BootstrapError(ValueError):
    """A bootstrap file is not in the published format."""


@dataclass(frozen=True)
class Service:
    """An RDAP server: its base URL (ending in ``/``)."""

    base_url: str

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").lower()

    @property
    def rir(self) -> str | None:
        """The Regional Internet Registry running this server, if it is one."""
        return RIR_HOSTS.get(self.host)

    def url(self, path: str) -> str:
        return self.base_url + path


def _service(urls: list[Any]) -> Service | None:
    """The entry's https URL if it has one, else its first http URL."""
    usable = [
        url.strip()
        for url in urls
        if isinstance(url, str) and urlsplit(url.strip()).scheme in {"http", "https"}
    ]
    usable.sort(key=lambda url: urlsplit(url).scheme != "https")
    if not usable:
        return None
    base = usable[0]
    return Service(base if base.endswith("/") else base + "/")


def _asn_range(text: str) -> tuple[int, int]:
    low, _, high = text.partition("-")
    first, last = int(low), int(high or low)
    if not 0 <= first <= last <= 2**32 - 1:
        raise ValueError("not an AS number range")
    return first, last


@dataclass(frozen=True)
class BootstrapFile:
    """One parsed bootstrap file. Malformed entries are skipped, not fatal."""

    registry: Registry
    domains: Mapping[str, Service]
    networks: tuple[tuple[Network, Service], ...]
    asns: tuple[tuple[int, int, Service], ...]

    @classmethod
    def parse(cls, registry: Registry, data: Mapping[str, Any]) -> "BootstrapFile":
        services = data.get("services")
        if not isinstance(services, list) or len(services) > MAX_ENTRIES:
            raise BootstrapError("no services list")
        domains: dict[str, Service] = {}
        networks: list[tuple[Network, Service]] = []
        asns: list[tuple[int, int, Service]] = []
        for entry in services:
            if not (
                isinstance(entry, list)
                and len(entry) == 2
                and isinstance(entry[0], list)
                and isinstance(entry[1], list)
            ):
                continue
            service = _service(entry[1])
            if service is None:
                continue
            for key in entry[0]:
                if not isinstance(key, str):
                    continue
                try:
                    match registry:
                        case Registry.DNS:
                            domains[key.strip().strip(".").lower()] = service
                        case Registry.IPV4 | Registry.IPV6:
                            networks.append((ipaddress.ip_network(key.strip()), service))
                        case Registry.ASN:
                            asns.append((*_asn_range(key.strip()), service))
                except ValueError:
                    continue
        return cls(registry, domains, tuple(networks), tuple(asns))

    def for_domain(self, name: str) -> Service | None:
        """The service for the longest suffix of ``name`` (a TLD, or more labels) listed."""
        labels = name.lower().strip(".").split(".")
        for start in range(len(labels)):
            service = self.domains.get(".".join(labels[start:]))
            if service is not None:
                return service
        return None

    def for_network(self, network: Network) -> Service | None:
        """The service for the longest prefix that contains all of ``network``."""
        best: tuple[int, Service] | None = None
        for entry, service in self.networks:
            if (
                entry.version == network.version
                and network.subnet_of(entry)  # type: ignore[arg-type]
                and (best is None or entry.prefixlen > best[0])
            ):
                best = (entry.prefixlen, service)
        return best[1] if best else None

    def for_asn(self, number: int) -> Service | None:
        """The service for the smallest listed range that contains ``number``."""
        best: tuple[int, Service] | None = None
        for first, last, service in self.asns:
            if first <= number <= last and (best is None or last - first < best[0]):
                best = (last - first, service)
        return best[1] if best else None


class BootstrapCache:
    """Bootstrap files fetched at most once per :data:`CACHE_SECONDS`, per process.

    Concurrent jobs in one event loop share one fetch per file (a lock per
    file). The worker runs each job in a fresh event loop, so locks are kept
    per loop; the cached files are shared by all of them.
    """

    def __init__(self, *, clock: Clock = time.monotonic, ttl: float = CACHE_SECONDS) -> None:
        self._clock = clock
        self._ttl = ttl
        self._files: dict[Registry, tuple[float, BootstrapFile]] = {}
        self._locks: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, dict[Registry, asyncio.Lock]
        ] = weakref.WeakKeyDictionary()
        self.fetches = 0

    def _lock(self, registry: Registry) -> asyncio.Lock:
        locks = self._locks.setdefault(asyncio.get_running_loop(), {})
        return locks.setdefault(registry, asyncio.Lock())

    def _fresh(self, registry: Registry) -> BootstrapFile | None:
        cached = self._files.get(registry)
        if cached is not None and self._clock() - cached[0] < self._ttl:
            return cached[1]
        return None

    async def get(self, registry: Registry, fetch: Fetch) -> BootstrapFile:
        """The parsed file, from the cache or fetched with ``fetch(url)``.

        A failed refresh falls back to a stale copy; with none, the error propagates.
        """
        cached = self._fresh(registry)
        if cached is not None:
            return cached
        async with self._lock(registry):
            cached = self._fresh(registry)
            if cached is not None:
                return cached
            self.fetches += 1
            try:
                parsed = BootstrapFile.parse(registry, await fetch(registry.url))
            except JobCancelledError:
                raise
            except Exception:
                stale = self._files.get(registry)
                if stale is None:
                    raise
                return stale[1]
            self._files[registry] = (self._clock(), parsed)
            return parsed

    def clear(self) -> None:
        self._files.clear()


# Shared by every job in this process.
SHARED_CACHE = BootstrapCache()

__all__ = [
    "BOOTSTRAP_BASE_URL",
    "CACHE_SECONDS",
    "RIR_HOSTS",
    "SHARED_CACHE",
    "BootstrapCache",
    "BootstrapError",
    "BootstrapFile",
    "Registry",
    "Service",
]
