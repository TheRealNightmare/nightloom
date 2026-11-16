"""Canonical forms and keys for every entity type.

Every entity has exactly one canonical key, ``<type>:<normalized>``, so the
same thing found in different spellings ("Example.ORG." and "example.org")
becomes one graph node. :func:`normalize` returns the canonical display value
and the key; the per-type functions below return the canonical value.

Every normalizer raises :class:`NormalizationError` for input it cannot
accept. Its message is safe to show to a user: it describes the problem and
never repeats the input. Normalizers are idempotent:
``normalize(t, normalize(t, x).value) == normalize(t, x)``.

Nothing here touches the network. In particular the public suffix list used
by :func:`registrable_domain` is the snapshot bundled with ``tldextract``.
"""

import hashlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import idna
import phonenumbers
import tldextract

from nightloom_core.entities import EntityType


class NormalizationError(ValueError):
    """A value cannot be normalized. ``str(error)`` is safe to return to a client."""


@dataclass(frozen=True, slots=True)
class Normalized:
    """A normalized value: the canonical display ``value`` and the canonical ``key``."""

    type: EntityType
    value: str
    key: str


# Longest input any normalizer looks at; anything longer is refused outright.
MAX_INPUT_LENGTH = 2048
MAX_TEXT_LENGTH = 1000
MAX_DOMAIN_LENGTH = 253
MAX_URL_LENGTH = 2048
MAX_ASN = 4_294_967_295

# --- helpers -----------------------------------------------------------------------------


def _check_length(value: str, limit: int = MAX_INPUT_LENGTH) -> None:
    if len(value) > limit:
        raise NormalizationError(f"Value is too long (at most {limit} characters).")


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _token(value: str, what: str) -> str:
    """Strip ``value`` and refuse inner whitespace or control characters."""
    _check_length(value)
    text = value.strip()
    if not text:
        raise NormalizationError(f"{what} is empty.")
    if any(char.isspace() for char in text) or _has_control(text):
        raise NormalizationError(f"{what} must not contain spaces or control characters.")
    return text


# --- domains -----------------------------------------------------------------------------

# An ASCII label: letters, digits, hyphens and underscores (``_dmarc``,
# ``_443._tcp``), not starting or ending with a hyphen, at most 63 characters.
_ASCII_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")

# The public suffix list bundled with tldextract: no cache directory, no
# download URLs, so it never reads or writes files or touches the network.
_SUFFIXES = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=(), fallback_to_snapshot=True)

DOMAIN_INVALID = "Not a valid domain name."


def _a_label(label: str) -> str:
    """One mapped label in ASCII (A-label) form."""
    if label.isascii():
        if not _ASCII_LABEL.fullmatch(label):
            raise NormalizationError(DOMAIN_INVALID)
        if label.startswith("xn--"):
            # Punycode must decode to a valid IDN label and encode back to itself.
            try:
                if idna.alabel(idna.ulabel(label)).decode("ascii") != label:
                    raise NormalizationError(DOMAIN_INVALID)
            except (idna.IDNAError, UnicodeError) as exc:
                raise NormalizationError(DOMAIN_INVALID) from exc
        return label
    try:
        return idna.alabel(label).decode("ascii")
    except (idna.IDNAError, UnicodeError) as exc:
        raise NormalizationError(DOMAIN_INVALID) from exc


def normalize_domain(value: str) -> str:
    """A fully qualified domain name in lower-case A-label (punycode) form.

    Strips surrounding whitespace and one trailing dot, applies UTS #46
    mapping (lower-casing, full-width dots, ...) and IDNA 2008 encoding to
    non-ASCII labels. Underscores are allowed (``_dmarc.example.org``), and a
    leading ``*`` label (a certificate wildcard). Needs at least two labels;
    each is at most 63 characters and the name at most 253.
    """
    text = _token(value, "Domain name")
    try:
        mapped = idna.uts46_remap(text, std3_rules=False, transitional=False)
    except (idna.IDNAError, UnicodeError) as exc:
        raise NormalizationError(DOMAIN_INVALID) from exc
    if mapped.endswith("."):
        mapped = mapped[:-1]
    labels = mapped.split(".")
    if len(labels) < 2:
        raise NormalizationError("A domain name needs at least two labels (e.g. example.org).")
    if any(not label for label in labels):
        raise NormalizationError("A domain name must not contain empty labels.")
    wildcard = labels[0] == "*"
    encoded = [_a_label(label) for label in labels[1 if wildcard else 0 :]]
    if labels[-1].isdigit():
        raise NormalizationError("The last label of a domain name cannot be numeric.")
    result = ".".join(["*", *encoded] if wildcard else encoded)
    if len(result) > MAX_DOMAIN_LENGTH:
        raise NormalizationError(
            f"A domain name is at most {MAX_DOMAIN_LENGTH} characters in A-label form."
        )
    return result


def registrable_domain(value: str) -> str | None:
    """The registrable part of a domain (``www.example.co.uk`` → ``example.co.uk``).

    ``None`` when the name is itself a public suffix (``co.uk``) or has no
    known suffix. Uses only tldextract's bundled public suffix list snapshot.
    """
    domain = normalize_domain(value).removeprefix("*.")
    result = _SUFFIXES(domain).top_domain_under_public_suffix
    return result or None


# --- addresses and networks -------------------------------------------------------------


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if "%" in text:
        raise NormalizationError("IPv6 zone identifiers (%...) are not accepted.")
    try:
        return ipaddress.ip_address(text)
    except ValueError as exc:
        raise NormalizationError("Not a valid IP address.") from exc


def normalize_ip(value: str) -> str:
    """An IPv4 or IPv6 address in its compressed form (``2001:db8::1``)."""
    return _ip(_token(value, "IP address")).compressed


def normalize_netblock(value: str) -> str:
    """A network in CIDR form; host bits are cleared (``192.0.2.7/24`` → ``192.0.2.0/24``)."""
    text = _token(value, "Netblock")
    if "%" in text:
        raise NormalizationError("IPv6 zone identifiers (%...) are not accepted.")
    try:
        return ipaddress.ip_network(text, strict=False).compressed
    except ValueError as exc:
        raise NormalizationError("Not a valid network (expected CIDR, e.g. 192.0.2.0/24).") from exc


_ASN = re.compile(r"(?:AS)?\s*([0-9]{1,10})", re.IGNORECASE)


def normalize_asn(value: str) -> int:
    """An autonomous system number from ``AS64496``, ``as64496`` or ``64496``."""
    _check_length(value)
    match = _ASN.fullmatch(value.strip())
    if match is None:
        raise NormalizationError("Not a valid AS number (expected e.g. AS64496).")
    number = int(match.group(1))
    if not 1 <= number <= MAX_ASN:
        raise NormalizationError(f"An AS number is between 1 and {MAX_ASN}.")
    return number


_HEX64 = re.compile(r"[0-9a-f]{64}")


def normalize_certificate(value: str) -> str:
    """A certificate's SHA-256 fingerprint: 64 lower-case hex digits (colons are removed)."""
    text = _token(value, "Certificate fingerprint").lower().replace(":", "")
    if not _HEX64.fullmatch(text):
        raise NormalizationError(
            "A certificate is identified by its SHA-256 fingerprint (64 hex digits)."
        )
    return text


# --- URLs and services -----------------------------------------------------------------

URL_SCHEMES = frozenset({"http", "https", "ftp"})
DEFAULT_PORTS = {"http": 80, "https": 443, "ftp": 21}


def _host(hostname: str, bracketed: bool) -> str:
    """A URL or service host: an IP address (IPv6 in brackets) or a domain name."""
    if bracketed:
        address = _ip(hostname)
        if address.version != 6:
            raise NormalizationError("Only IPv6 addresses go in brackets.")
        return f"[{address.compressed}]"
    try:
        return ipaddress.IPv4Address(hostname).compressed
    except ValueError:
        pass
    domain = normalize_domain(hostname)
    if domain.startswith("*"):
        raise NormalizationError("A URL host cannot be a wildcard.")
    return domain


def normalize_url(value: str) -> str:
    """An http, https or ftp URL with a canonical scheme, host and port.

    Lower-cases the scheme, normalizes the host like a domain or IP address,
    removes the scheme's default port and the fragment, and uses ``/`` for an
    empty path. Path and query are kept as they are. URLs with embedded
    credentials (``user:password@``) are refused.
    """
    text = _token(value, "URL")
    _check_length(text, MAX_URL_LENGTH)
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise NormalizationError("Not a valid URL.") from exc
    scheme = parts.scheme.lower()
    if scheme not in URL_SCHEMES:
        raise NormalizationError("Only http, https and ftp URLs are accepted.")
    if not parts.hostname:
        raise NormalizationError("A URL needs a host.")
    if "@" in parts.netloc:
        raise NormalizationError("URLs with embedded credentials are not accepted.")
    host = _host(parts.hostname, bracketed=parts.netloc.startswith("["))
    if port == 0:
        raise NormalizationError("Port 0 is not valid.")
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    result = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    _check_length(result, MAX_URL_LENGTH)
    return result


_SERVICE = re.compile(r"(?P<host>\[[^\]]+\]|[^:/\[\]]+):(?P<port>[0-9]{1,5})/(?P<proto>[A-Za-z]+)")
SERVICE_PROTOCOLS = frozenset({"tcp", "udp"})


def normalize_service(value: str) -> str:
    """A network service, ``ip:port/protocol`` (``[ipv6]:port/protocol``), tcp or udp."""
    text = _token(value, "Service")
    match = _SERVICE.fullmatch(text)
    if match is None:
        raise NormalizationError(
            "A service is written ip:port/protocol (e.g. 192.0.2.1:443/tcp, [2001:db8::1]:53/udp)."
        )
    raw_host = match.group("host")
    if raw_host.startswith("["):
        host = _host(raw_host[1:-1], bracketed=True)
    else:
        host = _ip(raw_host).compressed
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise NormalizationError("A port is between 1 and 65535.")
    protocol = match.group("proto").lower()
    if protocol not in SERVICE_PROTOCOLS:
        raise NormalizationError("The protocol must be tcp or udp.")
    return f"{host}:{port}/{protocol}"


# --- people and accounts -----------------------------------------------------------------

# RFC 5322 dot-atom (lower-cased): no quoted local parts, no comments.
_LOCAL_PART = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*")
EMAIL_INVALID = "Not a valid email address."


def normalize_email(value: str) -> str:
    """A lower-cased email address whose domain is normalized like a domain name."""
    text = _token(value, "Email address")
    local, at, domain = text.rpartition("@")
    local = local.lower()
    if not at or len(local) > 64 or not _LOCAL_PART.fullmatch(local):
        raise NormalizationError(EMAIL_INVALID)
    try:
        domain = normalize_domain(domain)
    except NormalizationError as exc:
        raise NormalizationError(EMAIL_INVALID) from exc
    if domain.startswith("*"):
        raise NormalizationError(EMAIL_INVALID)
    result = f"{local}@{domain}"
    if len(result) > 254:
        raise NormalizationError("An email address is at most 254 characters.")
    return result


def normalize_phone(value: str, *, default_region: str | None) -> str:
    """A valid phone number in E.164 form (``+16502530000``).

    ``default_region`` (ISO 3166 alpha-2, e.g. ``"GB"``) is how a number in
    national format is read. It has no default on purpose: pass ``None`` to
    accept only numbers in international format (``+...``).
    """
    _check_length(value, 100)
    if _has_control(value):
        raise NormalizationError("Phone number must not contain control characters.")
    region = default_region.strip().upper() if default_region else None
    if region is not None and region not in phonenumbers.SUPPORTED_REGIONS:
        raise NormalizationError("Unknown phone region (expected an ISO 3166 code, e.g. GB).")
    try:
        number = phonenumbers.parse(value.strip(), region)
    except phonenumbers.NumberParseException as exc:
        if region is None and not value.strip().startswith("+"):
            raise NormalizationError(
                "Give the phone number in international format (+...) or a default region."
            ) from exc
        raise NormalizationError("Not a valid phone number.") from exc
    if not phonenumbers.is_valid_number(number):
        raise NormalizationError("Not a valid phone number.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


# --- crypto wallets ----------------------------------------------------------------------

_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3
_ETH = re.compile(r"0x[0-9a-f]{40}")
WALLET_INVALID = "Not a valid Bitcoin or Ethereum address."


def _base58check(address: str) -> bool:
    """A mainnet P2PKH (``1...``) or P2SH (``3...``) address with a valid checksum."""
    if not 26 <= len(address) <= 35 or address[0] not in "13":
        return False
    number = 0
    for char in address:
        digit = _BASE58.find(char)
        if digit < 0:
            return False
        number = number * 58 + digit
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    raw = b"\x00" * (len(address) - len(address.lstrip("1"))) + raw
    if len(raw) != 25 or raw[0] not in (0x00, 0x05):
        return False
    checksum = hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4]
    return checksum == raw[-4:]


def _polymod(values: list[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for bit in range(5):
            checksum ^= generator[bit] if (top >> bit) & 1 else 0
    return checksum


def _bech32(address: str) -> bool:
    """A mainnet SegWit address (``bc1...``, BIP 173 / BIP 350) with a valid checksum."""
    if not 14 <= len(address) <= 74 or not address.startswith("bc1"):
        return False
    data = [_BECH32.find(char) for char in address[3:]]
    if any(value < 0 for value in data) or len(data) < 7:
        return False
    hrp = [ord(char) >> 5 for char in "bc"] + [0] + [ord(char) & 31 for char in "bc"]
    const = _polymod(hrp + data)
    version, program5 = data[0], data[1:-6]
    if version > 16 or const != (_BECH32_CONST if version == 0 else _BECH32M_CONST):
        return False
    # Regroup the 5-bit program into bytes; leftover bits must be zero padding.
    accumulator = bits = 0
    program: list[int] = []
    for value in program5:
        accumulator = accumulator << 5 | value
        bits += 5
        if bits >= 8:
            bits -= 8
            program.append(accumulator >> bits & 0xFF)
    if bits >= 5 or accumulator & ((1 << bits) - 1):
        return False
    if not 2 <= len(program) <= 40:
        return False
    return version != 0 or len(program) in (20, 32)


def normalize_crypto_wallet(value: str) -> str:
    """A wallet address prefixed with its chain: ``btc:<address>`` or ``eth:0x<hex>``.

    Bitcoin (mainnet): base58check P2PKH/P2SH or bech32/bech32m SegWit
    (stored lower-case), checksums verified. Ethereum: ``0x`` and 40 hex
    digits, stored lower-case (EIP-55 mixed case is accepted, not checked).
    An explicit ``btc:`` or ``eth:`` prefix in the input is honoured.
    """
    text = _token(value, "Wallet address")
    chain, colon, rest = text.partition(":")
    chain = chain.lower()
    if colon and chain in ("btc", "eth"):
        text = rest
    else:
        chain = "eth" if text.lower().startswith("0x") else "btc"
    if chain == "eth":
        address = text.lower()
        if not _ETH.fullmatch(address):
            raise NormalizationError(WALLET_INVALID)
        return f"eth:{address}"
    if text.lower().startswith("bc1"):
        if text != text.lower() and text != text.upper():
            raise NormalizationError(WALLET_INVALID)  # BIP 173: never mixed case
        address = text.lower()
        if not _bech32(address):
            raise NormalizationError(WALLET_INVALID)
        return f"btc:{address}"
    if not _base58check(text):
        raise NormalizationError(WALLET_INVALID)
    return f"btc:{text}"


# --- free text ---------------------------------------------------------------------------


def normalize_text(value: str) -> str:
    """NFKC, trimmed, inner whitespace collapsed to single spaces. Case is kept."""
    _check_length(value)
    text = " ".join(unicodedata.normalize("NFKC", value).split())
    if not text:
        raise NormalizationError("Value is empty.")
    if _has_control(text):
        raise NormalizationError("Value must not contain control characters.")
    if len(text) > MAX_TEXT_LENGTH:
        raise NormalizationError(f"Value is too long (at most {MAX_TEXT_LENGTH} characters).")
    return text


def text_key(value: str) -> str:
    """The case-insensitive key of normalized text: case-folded and NFKC again, until stable."""
    key = value
    for _ in range(4):
        folded = unicodedata.normalize("NFKC", key.casefold())
        if folded == key:
            break
        key = folded
    return key


TEXT_TYPES = frozenset(
    {
        EntityType.ORGANIZATION,
        EntityType.PERSON,
        EntityType.TECHNOLOGY,
        EntityType.LOCATION,
        EntityType.USERNAME,
        EntityType.SOCIAL_PROFILE,
        EntityType.DOCUMENT,
        EntityType.IMAGE,
        EntityType.BREACH,
    }
)


# --- dispatch ----------------------------------------------------------------------------


def normalize(
    entity_type: EntityType, value: str, *, phone_region: str | None = None
) -> Normalized:
    """The canonical value and key of ``value`` as an entity of ``entity_type``.

    ``phone_region`` is only used for phone numbers (see :func:`normalize_phone`).
    """
    entity_type = EntityType(entity_type)
    match entity_type:
        case EntityType.DOMAIN:
            canonical = normalize_domain(value)
        case EntityType.IP_ADDRESS:
            canonical = normalize_ip(value)
        case EntityType.NETBLOCK:
            canonical = normalize_netblock(value)
        case EntityType.ASN:
            number = normalize_asn(value)
            return Normalized(entity_type, f"AS{number}", f"{entity_type.value}:{number}")
        case EntityType.CERTIFICATE:
            canonical = normalize_certificate(value)
        case EntityType.URL:
            canonical = normalize_url(value)
        case EntityType.SERVICE:
            canonical = normalize_service(value)
        case EntityType.EMAIL:
            canonical = normalize_email(value)
        case EntityType.PHONE:
            canonical = normalize_phone(value, default_region=phone_region)
        case EntityType.CRYPTO_WALLET:
            canonical = normalize_crypto_wallet(value)
        case _:
            display = normalize_text(value)
            return Normalized(entity_type, display, f"{entity_type.value}:{text_key(display)}")
    return Normalized(entity_type, canonical, f"{entity_type.value}:{canonical}")


MAX_KEY_LENGTH = 64 + MAX_INPUT_LENGTH
KEY_INVALID = "Not a canonical entity key."


def parse_key(key: str) -> tuple[EntityType, str]:
    """Check that ``key`` is a canonical entity key; return its type and normalized part.

    A key is canonical when normalizing its own normalized part gives the key
    back, so keys that merely look right (``domain:Example.org``) are refused.
    """
    if len(key) > MAX_KEY_LENGTH:
        raise NormalizationError(KEY_INVALID)
    prefix, colon, rest = key.partition(":")
    try:
        entity_type = EntityType(prefix)
    except ValueError as exc:
        raise NormalizationError(KEY_INVALID) from exc
    if not colon or not rest:
        raise NormalizationError(KEY_INVALID)
    try:
        canonical = normalize(entity_type, rest)
    except NormalizationError as exc:
        raise NormalizationError(KEY_INVALID) from exc
    if canonical.key != key:
        raise NormalizationError(KEY_INVALID)
    return entity_type, rest


__all__ = [
    "NormalizationError",
    "Normalized",
    "normalize",
    "normalize_asn",
    "normalize_certificate",
    "normalize_crypto_wallet",
    "normalize_domain",
    "normalize_email",
    "normalize_ip",
    "normalize_netblock",
    "normalize_phone",
    "normalize_service",
    "normalize_text",
    "normalize_url",
    "parse_key",
    "registrable_domain",
    "text_key",
]
