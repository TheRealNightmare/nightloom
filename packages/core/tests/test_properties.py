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


ip_text = st.one_of(
    st.ip_addresses().map(str),
    st.ip_addresses(v=6).map(lambda ip: ip.exploded.upper()),
    st.ip_addresses(v=4).map(lambda ip: f" {ip} "),
)


@given(ip_text)
def test_ip_idempotent(raw: str) -> None:
    once = n.normalize_ip(raw)
    assert n.normalize_ip(once) == once
    assert ipaddress.ip_address(once) == ipaddress.ip_address(raw.strip())


@given(st.ip_addresses(), st.data())
def test_netblock_idempotent(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, data: st.DataObject
) -> None:
    prefix = data.draw(st.integers(min_value=0, max_value=ip.max_prefixlen))
    once = n.normalize_netblock(f"{ip}/{prefix}")
    assert n.normalize_netblock(once) == once
    assert ipaddress.ip_network(once).prefixlen == prefix


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
