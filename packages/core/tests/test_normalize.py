"""Table-driven tests for every normalizer: valid input, edge cases and rejections."""

import socket
from collections.abc import Callable

import pytest

from nightloom_core import normalize as n
from nightloom_core.entities import EntityType
from nightloom_core.normalize import NormalizationError, Normalized

Case = tuple[str, str]

# Test data, not a bind address.
UNSPECIFIED_V4 = "0.0.0.0"  # noqa: S104


def full_width(text: str) -> str:
    """ASCII letters as their full-width forms (U+FF21...), spaces as U+3000."""
    return "".join(
        "\u3000" if char == " " else chr(ord(char) + 0xFEE0) if char.isalpha() else char
        for char in text
    )


# "example.org" in full-width letters with an ideographic full stop (U+3002).
FULL_WIDTH_DOMAIN = full_width("example") + "\u3002" + full_width("org")
FULL_WIDTH_ACME = full_width("ACME Corp")


def check_valid(func: Callable[[str], object], raw: str, expected: object) -> None:
    assert func(raw) == expected
    # Idempotent on its own output.
    assert func(str(expected)) == expected


def check_invalid(func: Callable[[str], object], raw: str) -> None:
    with pytest.raises(NormalizationError) as info:
        func(raw)
    message = str(info.value)
    assert message
    # User-safe: the message never repeats the input.
    if len(raw.strip()) > 3:
        assert raw.strip() not in message


# --- domains ----------------------------------------------------------------------------

DOMAINS_VALID: list[Case] = [
    ("example.org", "example.org"),
    ("Example.ORG.", "example.org"),
    ("  www.Example.org  ", "www.example.org"),
    ("a.b.c.d.example.org", "a.b.c.d.example.org"),
    ("_dmarc.example.org", "_dmarc.example.org"),
    ("_443._tcp.example.org", "_443._tcp.example.org"),
    ("*.example.org", "*.example.org"),
    ("xn--bcher-kva.example", "xn--bcher-kva.example"),
    ("Bücher.example", "xn--bcher-kva.example"),
    ("BÜCHER.EXAMPLE.", "xn--bcher-kva.example"),
    (FULL_WIDTH_DOMAIN, "example.org"),
    ("例え.テスト", "xn--r8jz45g.xn--zckzah"),
    ("faß.example", "xn--fa-hia.example"),  # IDNA 2008: ß is kept, not mapped to ss
    ("1.example.org", "1.example.org"),
    ("r4---sn-abc.example.org", "r4---sn-abc.example.org"),
    ("a" * 63 + ".org", "a" * 63 + ".org"),
]

DOMAINS_INVALID = [
    "",
    "   ",
    "localhost",
    "example..org",
    ".example.org",
    "example.org..",
    "-example.org",
    "example-.org",
    "exa mple.org",
    "exa\tmple.org",
    "a" * 64 + ".org",
    ".".join(["a" * 63] * 4) + ".org",  # > 253 characters
    "192.0.2.1",
    "example.123",
    "exa!mple.org",
    "example.org/path",
    "user@example.org",
    "xn--zz.example",  # invalid punycode
    "xn--abc-.example",  # punycode of a plain ASCII label
    "www.*.example.org",
    "\x00.example.org",
    "a‍.example",  # joiner outside a valid context
    "x" * 3000,
]


@pytest.mark.parametrize(("raw", "expected"), DOMAINS_VALID)
def test_domain_valid(raw: str, expected: str) -> None:
    check_valid(n.normalize_domain, raw, expected)


@pytest.mark.parametrize("raw", DOMAINS_INVALID)
def test_domain_invalid(raw: str) -> None:
    check_invalid(n.normalize_domain, raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.org", "example.org"),
        ("WWW.Example.org.", "example.org"),
        ("a.b.example.co.uk", "example.co.uk"),
        ("*.example.org", "example.org"),
        ("www.bücher.xn--p1ai", "xn--bcher-kva.xn--p1ai"),
        ("co.uk", None),
        ("example.invalidtld", None),
    ],
)
def test_registrable_domain(raw: str, expected: str | None) -> None:
    assert n.registrable_domain(raw) == expected


def test_registrable_domain_never_uses_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    extractor = type(n._SUFFIXES)(cache_dir=None, suffix_list_urls=(), fallback_to_snapshot=True)
    assert extractor("www.example.co.uk").top_domain_under_public_suffix == "example.co.uk"
    assert n._SUFFIXES.suffix_list_urls == ()


# --- IP addresses and netblocks -----------------------------------------------------------

IPS_VALID: list[Case] = [
    ("192.0.2.1", "192.0.2.1"),
    (" 198.51.100.7 ", "198.51.100.7"),
    ("2001:DB8::1", "2001:db8::1"),
    ("2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
    ("2001:db8:0:0:1:0:0:1", "2001:db8::1:0:0:1"),
    ("::", "::"),
    # IPv4-mapped IPv6 is the IPv4 host itself.
    ("::ffff:192.0.2.1", "192.0.2.1"),
    ("::FFFF:c000:201", "192.0.2.1"),
    ("0:0:0:0:0:ffff:c000:0201", "192.0.2.1"),
    ("::ffff:0.0.0.0", UNSPECIFIED_V4),
    # Other embedded-IPv4 forms stay IPv6, in pure hex.
    ("::192.0.2.1", "::c000:201"),  # IPv4-compatible (deprecated)
    ("2002:c000:0201::1", "2002:c000:201::1"),  # 6to4
    ("64:ff9b::192.0.2.1", "64:ff9b::c000:201"),  # NAT64 well-known prefix
    ("::ffff:0:192.0.2.1", "::ffff:0:c000:201"),  # SIIT, not mapped
    ("fe80::1", "fe80::1"),
]
IPS_INVALID = [
    "",
    "256.1.1.1",
    "192.0.2",
    "01.2.3.4",
    "192.0.2.1/32",
    "[2001:db8::1]",
    "fe80::1%eth0",
    "2001:db8::g",
    "example.org",
    "1.2.3.4 5",
]


@pytest.mark.parametrize(("raw", "expected"), IPS_VALID)
def test_ip_valid(raw: str, expected: str) -> None:
    check_valid(n.normalize_ip, raw, expected)


@pytest.mark.parametrize("raw", IPS_INVALID)
def test_ip_invalid(raw: str) -> None:
    check_invalid(n.normalize_ip, raw)


NETBLOCKS_VALID: list[Case] = [
    ("192.0.2.0/24", "192.0.2.0/24"),
    ("192.0.2.77/24", "192.0.2.0/24"),  # host bits cleared
    ("198.51.100.1", "198.51.100.1/32"),
    ("192.0.2.0/255.255.255.0", "192.0.2.0/24"),
    ("2001:DB8::1/32", "2001:db8::/32"),
    ("2001:db8:0:0::/64", "2001:db8::/64"),
    ("0.0.0.0/0", "0.0.0.0/0"),
    # IPv4-mapped networks (prefix 96 or longer) become IPv4 networks.
    ("::ffff:192.0.2.0/120", "192.0.2.0/24"),
    ("::FFFF:c000:2ff/120", "192.0.2.0/24"),
    ("::ffff:192.0.2.1/128", "192.0.2.1/32"),
    ("::ffff:0.0.0.0/96", "0.0.0.0/0"),
    # Anything wider, or any other embedded form, stays IPv6 in pure hex.
    ("::ffff:0.0.0.0/95", "::fffe:0:0/95"),  # wider than ::ffff:0:0/96
    ("::192.0.2.0/120", "::c000:200/120"),
    ("64:ff9b::192.0.2.0/120", "64:ff9b::c000:200/120"),
    ("2002:c000:200::/40", "2002:c000:200::/40"),
]
NETBLOCKS_INVALID = ["", "192.0.2.0/33", "2001:db8::/129", "198.51.100.0/", "abc/24", "fe80::/64%1"]


@pytest.mark.parametrize(("raw", "expected"), NETBLOCKS_VALID)
def test_netblock_valid(raw: str, expected: str) -> None:
    check_valid(n.normalize_netblock, raw, expected)


@pytest.mark.parametrize("raw", NETBLOCKS_INVALID)
def test_netblock_invalid(raw: str) -> None:
    check_invalid(n.normalize_netblock, raw)


# --- ASNs and certificates ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AS64496", 64496),
        ("as64496", 64496),
        ("64496", 64496),
        (" AS 64496 ", 64496),
        ("1", 1),
        ("4294967295", 4294967295),
        ("AS0064496", 64496),
    ],
)
def test_asn_valid(raw: str, expected: int) -> None:
    assert n.normalize_asn(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "AS", "0", "AS0", "4294967296", "AS-1", "ASN64496", "64496.5", "AS1.10"]
)
def test_asn_invalid(raw: str) -> None:
    check_invalid(n.normalize_asn, raw)


FINGERPRINT = "a" * 32 + "0123456789abcdef" * 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (FINGERPRINT, FINGERPRINT),
        (FINGERPRINT.upper(), FINGERPRINT),
        (":".join(FINGERPRINT[i : i + 2] for i in range(0, 64, 2)).upper(), FINGERPRINT),
    ],
)
def test_certificate_valid(raw: str, expected: str) -> None:
    check_valid(n.normalize_certificate, raw, expected)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        FINGERPRINT[:-1],
        FINGERPRINT + "a",
        "g" * 64,
        "a" * 40,
        FINGERPRINT[:32] + " " + "a" * 32,
    ],
)
def test_certificate_invalid(raw: str) -> None:
    check_invalid(n.normalize_certificate, raw)


# --- URLs and services --------------------------------------------------------------------

URLS_VALID: list[Case] = [
    ("http://example.org", "http://example.org/"),
    ("HTTP://Example.ORG:80/", "http://example.org/"),
    ("https://example.org:443/a/b?x=1#frag", "https://example.org/a/b?x=1"),
    ("https://example.org:8443/", "https://example.org:8443/"),
    ("http://example.org:443/", "http://example.org:443/"),
    ("ftp://files.example.org:21/pub", "ftp://files.example.org/pub"),
    ("ftp://files.example.org:2121/pub", "ftp://files.example.org:2121/pub"),
    ("https://[2001:DB8::1]:443/x", "https://[2001:db8::1]/x"),
    ("http://[2001:db8::1]:8080", "http://[2001:db8::1]:8080/"),
    ("http://192.0.2.1:80/index.html", "http://192.0.2.1/index.html"),
    ("https://Bücher.example/Pfad", "https://xn--bcher-kva.example/Pfad"),
    ("https://example.org./", "https://example.org/"),
    ("https://example.org/Case/Sensitive?Q=A", "https://example.org/Case/Sensitive?Q=A"),
    ("https://example.org:/", "https://example.org/"),
    ("http://[::ffff:192.0.2.1]:80/", "http://192.0.2.1/"),
    ("https://[::FFFF:c000:201]:8443/x", "https://192.0.2.1:8443/x"),
    ("http://[::192.0.2.1]/", "http://[::c000:201]/"),
    ("http://[64:ff9b::192.0.2.1]/", "http://[64:ff9b::c000:201]/"),
]
URLS_INVALID = [
    "",
    "example.org",
    "//example.org/",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "mailto:user@example.org",
    "gopher://example.org/",
    "https://user:secret@example.org/",
    "https://user@example.org/",
    "http://localhost/",
    "http://exa mple.org/",
    "http://example.org:99999/",
    "http://example.org:0/",
    "http://example.org:abc/",
    "http://[192.0.2.1]/",
    "http://[2001:db8::1/",
    "http://*.example.org/",
    "https:///path",
    "https://example.org/" + "a" * 2100,
]


@pytest.mark.parametrize(("raw", "expected"), URLS_VALID)
def test_url_valid(raw: str, expected: str) -> None:
    check_valid(n.normalize_url, raw, expected)


@pytest.mark.parametrize("raw", URLS_INVALID)
def test_url_invalid(raw: str) -> None:
    check_invalid(n.normalize_url, raw)


SERVICES_VALID: list[Case] = [
    ("192.0.2.1:443/tcp", "192.0.2.1:443/tcp"),
    ("192.0.2.1:53/UDP", "192.0.2.1:53/udp"),
    ("[2001:DB8::1]:22/tcp", "[2001:db8::1]:22/tcp"),
    ("192.0.2.1:00080/tcp", "192.0.2.1:80/tcp"),
    ("[::ffff:192.0.2.1]:443/tcp", "192.0.2.1:443/tcp"),
    ("[64:ff9b::192.0.2.1]:53/udp", "[64:ff9b::c000:201]:53/udp"),
]
SERVICES_INVALID = [
    "",
    "198.51.100.1:443",
    "192.0.2.1/tcp",
    "192.0.2.1:0/tcp",
    "192.0.2.1:65536/tcp",
    "192.0.2.1:443/sctp",
    "2001:db8::1:22/tcp",
    "[192.0.2.1]:22/tcp",
    "example.org:443/tcp",
    "999.0.2.1:443/tcp",
]


@pytest.mark.parametrize(("raw", "expected"), SERVICES_VALID)
def test_service_valid(raw: str, expected: str) -> None:
    check_valid(n.normalize_service, raw, expected)


@pytest.mark.parametrize("raw", SERVICES_INVALID)
def test_service_invalid(raw: str) -> None:
    check_invalid(n.normalize_service, raw)


# --- emails and phones ---------------------------------------------------------------------

EMAILS_VALID: list[Case] = [
    ("user@example.org", "user@example.org"),
    ("John.Doe@Example.ORG", "john.doe@example.org"),
    ("  first+tag@example.org ", "first+tag@example.org"),
    ("o'brien@example.org", "o'brien@example.org"),
    ("user@Bücher.example", "user@xn--bcher-kva.example"),
    ("user@example.org.", "user@example.org"),
]
EMAILS_INVALID = [
    "",
    "user",
    "@example.org",
    "user@",
    "user@localhost",
    "user@@example.org",
    ".user@example.org",
    "user.@example.org",
    "us..er@example.org",
    '"quoted"@example.org',
    "us er@example.org",
    "a" * 65 + "@example.org",
    "user@*.example.org",
    "user@192.0.2.1",
    "user@[192.0.2.1]",  # address literals are not accepted as email domains
    "user@[::ffff:192.0.2.1]",
    "üser@example.org",
]


@pytest.mark.parametrize(("raw", "expected"), EMAILS_VALID)
def test_email_valid(raw: str, expected: str) -> None:
    check_valid(n.normalize_email, raw, expected)


@pytest.mark.parametrize("raw", EMAILS_INVALID)
def test_email_invalid(raw: str) -> None:
    check_invalid(n.normalize_email, raw)


def test_email_too_long() -> None:
    long_domain = ".".join(["a" * 60] * 4) + ".org"  # 248 characters
    with pytest.raises(NormalizationError, match="254"):
        n.normalize_email("abcdefgh@" + long_domain)


@pytest.mark.parametrize(
    ("raw", "region", "expected"),
    [
        ("+1 650-253-0000", None, "+16502530000"),
        ("+16502530000", "GB", "+16502530000"),  # international format wins
        ("(650) 253-0000", "US", "+16502530000"),
        ("020 7946 0958", "GB", "+442079460958"),
        ("020 7946 0958", "gb", "+442079460958"),
        ("+44 (0)20 7946 0958", None, "+442079460958"),
    ],
)
def test_phone_valid(raw: str, region: str | None, expected: str) -> None:
    assert n.normalize_phone(raw, default_region=region) == expected
    assert n.normalize_phone(expected, default_region=None) == expected


@pytest.mark.parametrize(
    ("raw", "region", "message"),
    [
        ("020 7946 0958", None, "international format"),
        ("020 7946 0958", "XX", "Unknown phone region"),
        ("+1 650", None, "Not a valid phone number"),
        ("+999 1234567", None, "Not a valid phone number"),
        ("not a number", "US", "Not a valid phone number"),
        ("+1 650\x00 253 0000", None, "control characters"),
        ("1" * 200, "US", "too long"),
    ],
)
def test_phone_invalid(raw: str, region: str | None, message: str) -> None:
    with pytest.raises(NormalizationError, match=message):
        n.normalize_phone(raw, default_region=region)


def test_phone_region_is_keyword_only_and_required() -> None:
    with pytest.raises(TypeError):
        n.normalize_phone("+16502530000")  # type: ignore[call-arg]


# --- crypto wallets ------------------------------------------------------------------------

WALLETS_VALID: list[Case] = [
    ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "btc:1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"),
    ("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy", "btc:3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"),
    ("1111111111111111111114oLvT2", "btc:1111111111111111111114oLvT2"),  # all-zero hash
    ("BTC:1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "btc:1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"),
    (
        "BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4",
        "btc:bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
    ),
    (
        "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
        "btc:bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
    ),
    ("BC1SW50QGDZ25J", "btc:bc1sw50qgdz25j"),  # v16, 2-byte program
    (
        "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
        "btc:bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
    ),
    (
        "0x52908400098527886E0F7030069857D2E4169EE7",
        "eth:0x52908400098527886e0f7030069857d2e4169ee7",
    ),
    (
        "eth:0X52908400098527886E0F7030069857D2E4169EE7",
        "eth:0x52908400098527886e0f7030069857d2e4169ee7",
    ),
]
WALLETS_INVALID = [
    "",
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb",  # bad checksum
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfN0",  # 0 is not base58
    "1111111111111111111114oLvT3",  # bad checksum
    "11111111111111111111111111",  # decodes to too few bytes
    "2A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",  # wrong version prefix
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5",  # bad checksum
    "bc1qW508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",  # mixed case
    "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",  # testnet
    # BIP 350 invalid vectors: bech32 checksum on v1, bech32m on v0, bad padding.
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd",
    "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7",
    "bc1zw508d6qejxtdg4y5r3zarqfsj8r5c",
    "bc1q0ysh2s",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf",  # padding > 4 bits
    "bc1zw508d6qejxtdg4y5r3zarvaryvqyzf3du",  # non-zero padding
    "BC1QR508D6QEJXTDG4Y5R3ZARVARYV98GJ9P",  # v0 program of 16 bytes
    "0x52908400098527886E0F7030069857D2E4169EE",  # 39 hex digits
    "0x52908400098527886E0F7030069857D2E4169EEG",
    "eth:1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    "btc:0x52908400098527886E0F7030069857D2E4169EE7",
    "doge:DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L",
]


@pytest.mark.parametrize(("raw", "expected"), WALLETS_VALID)
def test_wallet_valid(raw: str, expected: str) -> None:
    check_valid(n.normalize_crypto_wallet, raw, expected)


@pytest.mark.parametrize("raw", WALLETS_INVALID)
def test_wallet_invalid(raw: str) -> None:
    check_invalid(n.normalize_crypto_wallet, raw)


# --- free text -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "display", "key"),
    [
        ("ACME Corp", "ACME Corp", "acme corp"),
        ("  ACME \t\n Corp  ", "ACME Corp", "acme corp"),
        (FULL_WIDTH_ACME, "ACME Corp", "acme corp"),
        ("Straße", "Straße", "strasse"),
        ("ﬁnance", "finance", "finance"),  # NFKC ligature
        ("Ⅻ Group", "XII Group", "xii group"),
        ("Café", "Café", "café"),
        ("Café", "Café", "café"),  # combining accent composed
    ],
)
def test_text(raw: str, display: str, key: str) -> None:
    assert n.normalize_text(raw) == display
    assert n.normalize_text(display) == display
    assert n.text_key(display) == key
    assert n.text_key(key) == key


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", "bad\x00value", "x" * 1001, "x" * 3000])
def test_text_invalid(raw: str) -> None:
    check_invalid(n.normalize_text, raw)


# --- dispatch and keys ------------------------------------------------------------------------

E = EntityType


@pytest.mark.parametrize(
    ("entity_type", "raw", "value", "key"),
    [
        (E.DOMAIN, "Example.ORG.", "example.org", "domain:example.org"),
        (E.IP_ADDRESS, "2001:DB8::1", "2001:db8::1", "ip_address:2001:db8::1"),
        (E.NETBLOCK, "192.0.2.9/24", "192.0.2.0/24", "netblock:192.0.2.0/24"),
        (E.ASN, "as64496", "AS64496", "asn:64496"),
        (E.CERTIFICATE, FINGERPRINT.upper(), FINGERPRINT, f"certificate:{FINGERPRINT}"),
        (E.URL, "HTTPS://Example.org:443", "https://example.org/", "url:https://example.org/"),
        (E.SERVICE, "192.0.2.1:22/TCP", "192.0.2.1:22/tcp", "service:192.0.2.1:22/tcp"),
        (E.EMAIL, "A@Example.org", "a@example.org", "email:a@example.org"),
        (E.PHONE, "+1 650 253 0000", "+16502530000", "phone:+16502530000"),
        (
            E.CRYPTO_WALLET,
            "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
            "btc:1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
            "crypto_wallet:btc:1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
        ),
        (E.ORGANIZATION, " ACME  Corp ", "ACME Corp", "organization:acme corp"),
        (E.PERSON, "Jane DOE", "Jane DOE", "person:jane doe"),
        (E.TECHNOLOGY, "nginx", "nginx", "technology:nginx"),
        (E.LOCATION, "Dhaka", "Dhaka", "location:dhaka"),
        (E.USERNAME, "Jane_Doe", "Jane_Doe", "username:jane_doe"),
        (E.SOCIAL_PROFILE, "@JaneDoe", "@JaneDoe", "social_profile:@janedoe"),
        (E.DOCUMENT, "Annual Report", "Annual Report", "document:annual report"),
        (E.IMAGE, "logo.png", "logo.png", "image:logo.png"),
        (E.BREACH, "Example 2024", "Example 2024", "breach:example 2024"),
    ],
)
def test_normalize_dispatch(entity_type: EntityType, raw: str, value: str, key: str) -> None:
    result = n.normalize(entity_type, raw)
    assert result == Normalized(entity_type, value, key)
    assert n.normalize(entity_type, result.value) == result
    assert n.parse_key(key) == (entity_type, key.partition(":")[2])


def test_every_type_has_a_normalizer() -> None:
    covered = {
        E.DOMAIN,
        E.IP_ADDRESS,
        E.NETBLOCK,
        E.ASN,
        E.CERTIFICATE,
        E.URL,
        E.SERVICE,
        E.EMAIL,
        E.PHONE,
        E.CRYPTO_WALLET,
    }
    assert covered | n.TEXT_TYPES == set(EntityType)
    assert not covered & n.TEXT_TYPES


def test_normalize_phone_region_is_passed_through() -> None:
    assert n.normalize(E.PHONE, "020 7946 0958", phone_region="GB").value == "+442079460958"
    with pytest.raises(NormalizationError):
        n.normalize(E.PHONE, "020 7946 0958")


def test_normalize_accepts_plain_strings_as_type() -> None:
    assert n.normalize("domain", "Example.org").key == "domain:example.org"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "key",
    [
        "",
        "domain",
        "domain:",
        "nope:example.org",
        "Domain:example.org",
        "domain:Example.org",
        "domain:example.org.",
        "asn:AS64496",
        "asn:064496",
        "person:Jane Doe",
        "person:jane  doe",
        "url:https://example.org",
        "phone:0207946 0958",
        "domain:" + "a" * 5000,
        "domain:exa mple.org",
        "ip_address:2001:DB8::1",
    ],
)
def test_parse_key_rejects_non_canonical_keys(key: str) -> None:
    with pytest.raises(NormalizationError, match="Not a canonical entity key"):
        n.parse_key(key)


# --- explicit address formatting ---------------------------------------------------------
# Expected strings are written out by hand: nothing here may depend on how a
# given Python release's ``ipaddress`` prints an address.

FORMAT_IPV6_CASES = [
    (0, "::"),
    (1, "::1"),
    (1 << 112, "1::"),
    (0x2001_0DB8_0000_0000_0000_0000_0000_0001, "2001:db8::1"),
    (0x2001_0DB8_0000_0000_0001_0000_0000_0001, "2001:db8::1:0:0:1"),  # first of equal runs
    (0x2001_0DB8_0000_0001_0000_0000_0000_0001, "2001:db8:0:1::1"),  # longest run wins
    (0x2001_0DB8_0000_0001_0001_0001_0001_0001, "2001:db8:0:1:1:1:1:1"),  # single 0 kept
    (0x2001_0DB8_0001_0001_0001_0001_0001_0000, "2001:db8:1:1:1:1:1:0"),
    (0xFFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF, "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"),
    (0x0000_0000_0000_0000_0000_FFFF_C000_0201, "::ffff:c000:201"),  # mapped, as hex
    (0x0000_0000_0000_0000_0000_0000_C000_0201, "::c000:201"),  # IPv4-compatible
    (0x2002_C000_0201_0000_0000_0000_0000_0001, "2002:c000:201::1"),  # 6to4
    (0x0064_FF9B_0000_0000_0000_0000_C000_0201, "64:ff9b::c000:201"),  # NAT64
    (0xFE80_0000_0000_0000_0000_0000_0000_0000, "fe80::"),
    (0x0001_0000_0000_0000_0000_0000_0000_0001, "1::1"),
]


@pytest.mark.parametrize(("number", "expected"), FORMAT_IPV6_CASES)
def test_format_ipv6(number: int, expected: str) -> None:
    assert n.format_ipv6(number) == expected


@pytest.mark.parametrize(
    ("number", "expected"),
    [(0, UNSPECIFIED_V4), (0xC0000201, "192.0.2.1"), (0xFFFFFFFF, "255.255.255.255")],
)
def test_format_ipv4(number: int, expected: str) -> None:
    assert n.format_ipv4(number) == expected


TRICKY_ADDRESSES = {
    "::ffff:192.0.2.1": "192.0.2.1",
    "::ffff:c000:201": "192.0.2.1",
    "::ffff:0:0": UNSPECIFIED_V4,
    "::ffff:255.255.255.255": "255.255.255.255",
    "::192.0.2.1": "::c000:201",
    "::0.0.0.1": "::1",
    "::ffff:0:c000:201": "::ffff:0:c000:201",
    "64:ff9b::192.0.2.1": "64:ff9b::c000:201",
    "64:ff9b:1::c000:201": "64:ff9b:1::c000:201",
    "2002:c000:201::": "2002:c000:201::",
    "2002:c000:0201:0:0:0:0:1": "2002:c000:201::1",
    "1:0:0:2:0:0:0:3": "1:0:0:2::3",
    "0:0:1:0:0:0:0:0": "0:0:1::",
}


def test_tricky_addresses_have_fixed_keys() -> None:
    """The exact canonical keys, identical on every supported Python version."""
    keys = {raw: n.normalize(EntityType.IP_ADDRESS, raw).key for raw in TRICKY_ADDRESSES}
    assert keys == {raw: f"ip_address:{out}" for raw, out in TRICKY_ADDRESSES.items()}
