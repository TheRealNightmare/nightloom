"""Names the DNS Shuttle refuses to look up: internal-only names.

Asking a public resolver about ``fileserver.corp`` or ``printer.local``
cannot give a public answer, but it does tell the resolver's operator about
an internal name. The Shuttle refuses such inputs before sending anything:
single-label names, and names under suffixes that are reserved (RFC 2606,
RFC 6761, RFC 6762, RFC 7686, RFC 8375) or used for private networks.
"""

# The test zone the integration tests serve from a local DNS server. Allowed
# only in the test-only mode (NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS).
TEST_ZONE = "nightloom-test.example"

INTERNAL_SUFFIXES = (
    "local",  # mDNS (RFC 6762)
    "localhost",  # RFC 6761
    "internal",  # reserved for private use (ICANN, 2024)
    "home.arpa",  # home networks (RFC 8375)
    "lan",
    "corp",
    "intranet",
    "test",  # RFC 2606 / RFC 6761
    "invalid",
    "example",
    "onion",  # Tor (RFC 7686)
)


def _under(name: str, suffix: str) -> bool:
    return name == suffix or name.endswith(f".{suffix}")


def is_internal_name(name: str, *, allow_test_zone: bool = False) -> bool:
    """Whether ``name`` (a normalized domain) is internal-only and must not be queried."""
    name = name.rstrip(".").lower()
    if "." not in name:
        return True
    if allow_test_zone and _under(name, TEST_ZONE):
        return False
    return any(_under(name, suffix) for suffix in INTERNAL_SUFFIXES)


__all__ = ["INTERNAL_SUFFIXES", "TEST_ZONE", "is_internal_name"]
