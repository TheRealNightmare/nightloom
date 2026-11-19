"""Which resolvers the DNS Shuttle may ask, from ``NIGHTLOOM_DNS_*`` environment variables.

The Shuttle never reads the host's ``/etc/resolv.conf``: on a server that
resolver is often an internal one, and asking it about a case's names could
put internal records (split-horizon DNS, internal zones) into the case. It
only asks the resolvers configured here, which must be public addresses.

- ``NIGHTLOOM_DNS_RESOLVERS``: comma-separated IP addresses of public
  recursive resolvers (default ``1.1.1.1,9.9.9.9``: Cloudflare and Quad9).
- ``NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS``: **test only**. Allows private
  resolver addresses (the integration tests' local DNS server) and the
  ``nightloom-test.example`` test zone. Refused with ``NIGHTLOOM_ENV=production``.
"""

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass

from nightloom_sdk import ShuttleConfigurationError
from nightloom_sdk.http import is_public_address

ENV_RESOLVERS = "NIGHTLOOM_DNS_RESOLVERS"
ENV_ALLOW_PRIVATE = "NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS"
ENV_ENVIRONMENT = "NIGHTLOOM_ENV"

DEFAULT_RESOLVERS = ("1.1.1.1", "9.9.9.9")
MAX_RESOLVERS = 8
# Shown with every Shuttle of this package: the default resolver's homepage and
# privacy policy (the operator who changes NIGHTLOOM_DNS_RESOLVERS picks others).
RESOLVER_HOMEPAGE = "https://developers.cloudflare.com/1.1.1.1/"
RESOLVER_TERMS_URL = "https://developers.cloudflare.com/1.1.1.1/privacy/public-dns-resolver/"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class DnsConfig:
    """The resolvers to ask and whether the test-only mode is on."""

    resolvers: tuple[str, ...] = DEFAULT_RESOLVERS
    # Test only: private resolvers and the nightloom-test.example zone.
    allow_private_resolvers: bool = False


def _flag(environ: Mapping[str, str], name: str) -> bool:
    raw = environ.get(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ShuttleConfigurationError(f"{name} must be true or false.")


def _resolver(entry: str, *, allow_private: bool) -> str:
    try:
        address = ipaddress.ip_address(entry)
    except ValueError:
        raise ShuttleConfigurationError(
            f"{ENV_RESOLVERS} must list IP addresses separated by commas; {entry!r} is not one "
            "(a host name would need another resolver to find the resolver)."
        ) from None
    if not allow_private and not is_public_address(address):
        raise ShuttleConfigurationError(
            f"{ENV_RESOLVERS} contains {entry}, which is not a public address. Only public "
            "resolvers are allowed: an internal resolver could leak internal records into a "
            f"case. (Integration tests set {ENV_ALLOW_PRIVATE}=true; never use that in production.)"
        )
    return str(address)


def load_config(environ: Mapping[str, str] | None = None) -> DnsConfig:
    """Read and check the configuration; raise :class:`ShuttleConfigurationError` if invalid."""
    env = os.environ if environ is None else environ
    allow_private = _flag(env, ENV_ALLOW_PRIVATE)
    if allow_private and env.get(ENV_ENVIRONMENT, "").strip().lower() == "production":
        raise ShuttleConfigurationError(
            f"{ENV_ALLOW_PRIVATE} is a test-only setting and is refused with "
            f"{ENV_ENVIRONMENT}=production. Remove it."
        )
    raw = env.get(ENV_RESOLVERS)
    if raw is None or not raw.strip():
        return DnsConfig(allow_private_resolvers=allow_private)
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if len(entries) > MAX_RESOLVERS:
        raise ShuttleConfigurationError(f"{ENV_RESOLVERS} lists more than {MAX_RESOLVERS}.")
    resolvers = tuple(
        dict.fromkeys(_resolver(entry, allow_private=allow_private) for entry in entries)
    )
    return DnsConfig(resolvers=resolvers, allow_private_resolvers=allow_private)


__all__ = [
    "DEFAULT_RESOLVERS",
    "ENV_ALLOW_PRIVATE",
    "ENV_ENVIRONMENT",
    "ENV_RESOLVERS",
    "MAX_RESOLVERS",
    "RESOLVER_HOMEPAGE",
    "RESOLVER_TERMS_URL",
    "DnsConfig",
    "load_config",
]
