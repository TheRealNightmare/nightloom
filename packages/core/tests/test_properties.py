"""Property-based tests: normalizing a normalized value changes nothing."""

import ipaddress
import string
from collections.abc import Callable

from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

from nightloom_core import normalize as n
from nightloom_core.entities import EntityType
from nightloom_core.normalize import NormalizationError

FULL_WIDTH_DOMAIN = "".join(chr(ord(c) + 0xFEE0) for c in "example") + "\u3002org"

LDH = string.ascii_letters + string.digits + "-_"

# Labels: mostly ASCII LDH, sometimes arbitrary Unicode letters (IDNs).
ascii_label = st.text(alphabet=LDH, min_size=1, max_size=20)
unicode_label = st.text(
    alphabet=st.characters(categories=("Ll", "Lu", "Lo")), min_size=1, max_size=8
)
label = st.one_of(ascii_label, ascii_label, unicode_label)
domain_like = st.builds(
    lambda labels, tld, dot: ".".join([*labels, tld]) + dot,
    st.lists(label, min_size=1, max_size=4),
    st.sampled_from(["org", "COM", "example", "co.uk", "xn--p1ai", "テスト"]),
    st.sampled_from(["", "."]),
)


def assert_idempotent(func: Callable[[str], str], raw: str) -> None:
    try:
        once = func(raw)
    except NormalizationError:
        return  # rejecting is fine; accepting must be stable
    assert func(once) == once


@settings(max_examples=300)
@given(domain_like)
@example("Example.ORG.")
@example(FULL_WIDTH_DOMAIN)
def test_domain_idempotent(raw: str) -> None:
    assert_idempotent(n.normalize_domain, raw)


@settings(max_examples=200)
@given(st.text(max_size=60))
def test_domain_idempotent_on_arbitrary_text(raw: str) -> None:
    assert_idempotent(n.normalize_domain, raw)


ipv4_int = st.integers(min_value=0, max_value=2**32 - 1)


def _v6(number: int) -> ipaddress.IPv6Address:
    return ipaddress.IPv6Address(number)


# Embedded-IPv4 forms whose text differs between Python releases.
embedded_ipv6 = st.one_of(
    ipv4_int.map(lambda v4: _v6(0xFFFF << 32 | v4)),  # IPv4-mapped
    ipv4_int.map(_v6),  # IPv4-compatible
    ipv4_int.map(lambda v4: _v6(0x64FF9B << 96 | v4)),  # NAT64
    st.tuples(ipv4_int, st.integers(0, 2**80 - 1)).map(
        lambda t: _v6(0x2002 << 112 | t[0] << 80 | t[1])  # 6to4
    ),
)
any_ip = st.one_of(st.ip_addresses(), embedded_ipv6)
ip_text = st.one_of(
    any_ip.map(str),
    st.ip_addresses(v=6).map(lambda ip: ip.exploded.upper()),
    embedded_ipv6.map(lambda ip: ip.exploded),
    st.ip_addresses(v=4).map(lambda ip: f" {ip} "),
)


def _same_host(canonical: str, raw: str) -> bool:
    """The canonical text names the raw address (IPv4-mapped compares as IPv4)."""
    address = ipaddress.ip_address(raw.strip())
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return ipaddress.ip_address(canonical) == address


@given(ip_text)
def test_ip_idempotent(raw: str) -> None:
    once = n.normalize_ip(raw)
    assert n.normalize_ip(once) == once
    assert _same_host(once, raw)
    assert "." not in once or ":" not in once  # never a dotted tail on IPv6


@given(any_ip)
def test_explicit_formatter_round_trips(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    text = n.format_ipv4(int(ip)) if ip.version == 4 else n.format_ipv6(int(ip))
    assert int(ipaddress.ip_address(text)) == int(ip)
    assert text == text.lower()


@given(any_ip, st.data())
def test_netblock_idempotent(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, data: st.DataObject
) -> None:
    prefix = data.draw(st.integers(min_value=0, max_value=ip.max_prefixlen))
    once = n.normalize_netblock(f"{ip}/{prefix}")
    assert n.normalize_netblock(once) == once
    network = ipaddress.ip_network(once)
    mapped = ip.version == 6 and prefix >= 96 and int(ip) >> 32 == 0xFFFF
    assert network.prefixlen == (prefix - 96 if mapped else prefix)
    assert network.version == (4 if mapped else ip.version)


@given(embedded_ipv6, st.integers(min_value=1, max_value=65535))
def test_url_and_service_hosts_are_idempotent(ip: ipaddress.IPv6Address, port: int) -> None:
    url = n.normalize_url(f"http://[{ip}]:{port}/")
    assert n.normalize_url(url) == url
    service = n.normalize_service(f"[{ip}]:{port}/tcp")
    assert n.normalize_service(service) == service


local_part = st.text(
    alphabet=string.ascii_letters + string.digits + "+-_.'", min_size=1, max_size=30
)


@settings(max_examples=300)
@given(local_part, domain_like)
def test_email_idempotent(local: str, domain: str) -> None:
    assert_idempotent(n.normalize_email, f"{local}@{domain}")


path_text = st.text(
    alphabet=string.ascii_letters + string.digits + "/-._~%!$&'()*+,;=:@", max_size=30
)


@settings(max_examples=300)
@given(
    st.sampled_from(["http", "HTTPS", "ftp", "Http"]),
    st.one_of(
        domain_like, st.ip_addresses(v=4).map(str), st.ip_addresses(v=6).map(lambda ip: f"[{ip}]")
    ),
    st.one_of(st.none(), st.integers(min_value=1, max_value=65535)),
    path_text,
    st.one_of(st.none(), path_text),
    st.one_of(st.none(), path_text),
)
def test_url_idempotent(
    scheme: str, host: str, port: int | None, path: str, query: str | None, fragment: str | None
) -> None:
    raw = f"{scheme}://{host}"
    if port is not None:
        raw += f":{port}"
    raw += "/" + path.lstrip("/")
    if query is not None:
        raw += f"?{query}"
    if fragment is not None:
        raw += f"#{fragment}"
    assert_idempotent(n.normalize_url, raw)


@given(st.text(max_size=80))
def test_url_idempotent_on_arbitrary_text(raw: str) -> None:
    assert_idempotent(n.normalize_url, raw)


@given(st.text(min_size=1, max_size=80))
def test_text_keys_are_stable(raw: str) -> None:
    try:
        display = n.normalize_text(raw)
    except NormalizationError:
        return
    assert n.normalize_text(display) == display
    key = n.normalize(EntityType.ORGANIZATION, display).key
    # The key validates against itself, so GET /entities/{key} can always find it.
    assert n.parse_key(key)[0] == EntityType.ORGANIZATION


@given(st.sampled_from(sorted(EntityType)), st.text(max_size=60))
def test_every_accepted_value_has_a_parsable_key(entity_type: EntityType, raw: str) -> None:
    assume(entity_type != EntityType.PHONE or raw.strip().startswith("+"))
    try:
        result = n.normalize(entity_type, raw)
    except NormalizationError:
        return
    assert n.normalize(entity_type, result.value) == result
    assert n.parse_key(result.key)[0] == entity_type
