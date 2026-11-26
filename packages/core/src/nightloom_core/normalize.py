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


def is_public_suffix(value: str) -> bool:
    """Whether the domain is itself a public suffix (``co.uk``), from the bundled list.

    A name under a suffix the list does not know (``nightloom-test.example``)
    is not one.
    """
    domain = normalize_domain(value).removeprefix("*.")
    return _SUFFIXES(domain).suffix == domain


# --- addresses and networks -------------------------------------------------------------


# Canonical text for addresses and networks is built here from the integer
# value, never from ``ipaddress``'s ``str()``/``.compressed``: how those print
# IPv6 addresses with an embedded IPv4 address changed between Python patch
# releases (``::ffff:c000:201`` on 3.12.3, ``::ffff:192.0.2.1`` later), and a
# key must be byte-identical wherever it is computed (API, worker, Shuttles).
#
# - IPv4: dotted decimal.
# - IPv4-mapped IPv6 (``::ffff:0:0/96``) is the IPv4 host itself, so it becomes
#   the plain IPv4 address (one node per host).
# - Every other IPv6 address, including IPv4-compatible (``::a.b.c.d``,
#   deprecated), 6to4 (``2002::/16``) and NAT64 (``64:ff9b::/96``), stays IPv6
#   in pure hex: lower-case, no leading zeros, the longest run of two or more
#   zero groups (the first one on a tie) replaced by ``::`` (RFC 5952).
#   These are distinct addresses on the wire, so they are not folded into IPv4.

_IPV4_MAPPED_PREFIX = 0xFFFF << 32
_IPV4_MAPPED_MASK = ((1 << 96) - 1) << 32


def format_ipv4(number: int) -> str:
    """Dotted decimal for a 32-bit integer."""
    return ".".join(str(number >> shift & 0xFF) for shift in (24, 16, 8, 0))


def format_ipv6(number: int) -> str:
    """RFC 5952 text for a 128-bit integer, always in pure hex (no dotted IPv4 tail)."""
    groups = [number >> shift & 0xFFFF for shift in range(112, -16, -16)]
    best_start, best_length, start = -1, 0, -1
    for index, group in enumerate([*groups, 1]):  # the sentinel closes a trailing run
        if group == 0:
            start = index if start < 0 else start
            continue
        if start >= 0 and index - start > best_length:
            best_start, best_length = start, index - start
        start = -1
    hexes = [f"{group:x}" for group in groups]
    if best_length < 2:
        return ":".join(hexes)
    head = ":".join(hexes[:best_start])
    tail = ":".join(hexes[best_start + best_length :])
    return f"{head}::{tail}"


def _ipv4_mapped(number: int) -> int | None:
    """The IPv4 address inside an IPv4-mapped IPv6 address, else ``None``."""
    if number & _IPV4_MAPPED_MASK == _IPV4_MAPPED_PREFIX:
        return number & 0xFFFFFFFF
    return None


def canonical_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """The canonical text of an address (see the rules above)."""
    number = int(address)
    if address.version == 4:
        return format_ipv4(number)
    mapped = _ipv4_mapped(number)
    return format_ipv4(mapped) if mapped is not None else format_ipv6(number)


def canonical_network(network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> str:
    """The canonical CIDR text of a network.

    An IPv6 network entirely inside ``::ffff:0:0/96`` (prefix 96 or longer)
    becomes the equivalent IPv4 network: ``::ffff:192.0.2.0/120`` → ``192.0.2.0/24``.
    """
    number, prefix = int(network.network_address), network.prefixlen
    if network.version == 4:
        return f"{format_ipv4(number)}/{prefix}"
    mapped = _ipv4_mapped(number)
    if mapped is not None and prefix >= 96:
        return f"{format_ipv4(mapped)}/{prefix - 96}"
    return f"{format_ipv6(number)}/{prefix}"


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if "%" in text:
        raise NormalizationError("IPv6 zone identifiers (%...) are not accepted.")
    try:
        return ipaddress.ip_address(text)
    except ValueError as exc:
        raise NormalizationError("Not a valid IP address.") from exc


def normalize_ip(value: str) -> str:
    """An IP address in canonical form (``2001:db8::1``; ``::ffff:192.0.2.1`` → ``192.0.2.1``)."""
    return canonical_ip(_ip(_token(value, "IP address")))


def normalize_netblock(value: str) -> str:
    """A network in CIDR form; host bits are cleared (``192.0.2.7/24`` → ``192.0.2.0/24``)."""
    text = _token(value, "Netblock")
    if "%" in text:
        raise NormalizationError("IPv6 zone identifiers (%...) are not accepted.")
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise NormalizationError("Not a valid network (expected CIDR, e.g. 192.0.2.0/24).") from exc
    return canonical_network(network)


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
    """A URL or service host: an IP address (IPv6 in brackets) or a domain name.

    An IPv4-mapped IPv6 host becomes its plain IPv4 address, without brackets.
    """
    if bracketed:
        address = _ip(hostname)
        if address.version != 6:
            raise NormalizationError("Only IPv6 addresses go in brackets.")
        canonical = canonical_ip(address)
        return canonical if ":" not in canonical else f"[{canonical}]"
    try:
        return canonical_ip(ipaddress.IPv4Address(hostname))
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
        host = canonical_ip(_ip(raw_host))
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


FAVICON_INVALID = (
    "A favicon hash is a 32-bit MurmurHash3 number, such as -1010568750 "
    "(Shodan's http.favicon.hash)."
)
_FAVICON = re.compile(r"[+-]?[0-9]{1,10}")


def normalize_favicon(value: str) -> str:
    """A favicon's MurmurHash3 as a signed 32-bit decimal number (Shodan's form).

    An unsigned value (2**31 to 2**32 - 1, as some tools print it) is turned
    into the same signed number, so both spellings name one favicon.
    """
    text = _token(value, "Favicon hash")
    if text.lower().startswith("mmh3:"):
        text = text[5:].strip()
    if not _FAVICON.fullmatch(text):
        raise NormalizationError(FAVICON_INVALID)
    number = int(text)
    if not -(2**31) <= number < 2**32:
        raise NormalizationError(FAVICON_INVALID)
    if number >= 2**31:
        number -= 2**32
    return str(number)


# --- repositories ------------------------------------------------------------------------

# The code forges a Repository can be on (owner/name paths with these hosts).
REPOSITORY_HOSTS = frozenset({"github.com", "gitlab.com", "codeberg.org", "bitbucket.org"})
REPOSITORY_INVALID = (
    "A repository is a public repository's address, such as github.com/example-org/widgets "
    "(GitHub, GitLab, Codeberg or Bitbucket)."
)
_REPOSITORY_OWNER = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,98}[a-z0-9_])?")
_REPOSITORY_NAME = re.compile(r"[a-z0-9._-]{1,100}")
_SCP_REMOTE = re.compile(r"git@([^:/]+):(.+)")


def normalize_repository(value: str) -> str:
    """A repository as ``<host>/<owner>/<name>`` in lower case.

    Accepts ``github.com/owner/name``, web addresses (``https://github.com/
    owner/name/tree/main``: anything after the name is dropped), clone
    addresses (``https://.../name.git``, ``git@github.com:owner/name.git``)
    and ``owner/name``, which means GitHub.
    """
    text = _token(value, "Repository").lower()
    scp = _SCP_REMOTE.fullmatch(text)
    if scp is not None:
        text = f"{scp.group(1)}/{scp.group(2)}"
    for prefix in ("https://", "http://", "ssh://git@", "git://"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.split("?", 1)[0].split("#", 1)[0]
    parts = [part for part in text.split("/") if part]
    if parts and parts[0].startswith("www."):
        parts[0] = parts[0][4:]
    if len(parts) == 2 and parts[0] not in REPOSITORY_HOSTS:
        parts = ["github.com", *parts]
    if len(parts) < 3 or parts[0] not in REPOSITORY_HOSTS:
        raise NormalizationError(REPOSITORY_INVALID)
    host, owner, name = parts[0], parts[1], parts[2].removesuffix(".git")
    if (
        not _REPOSITORY_OWNER.fullmatch(owner)
        or not _REPOSITORY_NAME.fullmatch(name)
        or name in (".", "..")
    ):
        raise NormalizationError(REPOSITORY_INVALID)
    return f"{host}/{owner}/{name}"


# --- secret exposures ---------------------------------------------------------------------

SECRET_EXPOSURE_INVALID = (
    "An exposed secret is located as <repository>@<40-character commit>/<path>#L<line>."  # noqa: S105
)
_EXPOSURE = re.compile(
    r"(?P<repository>[^@]+)@(?P<sha>[0-9a-fA-F]{40})/(?P<path>.+)#L(?P<line>[1-9][0-9]{0,6})"
)
MAX_EXPOSURE_PATH = 500


def normalize_secret_exposure(value: str) -> str:
    """``<repository>@<sha>/<path>#L<line>``: the repository normalized, the commit in lower
    case, the path kept as it is (paths are case-sensitive) but checked."""
    # Not _token(): a file path may contain spaces (control characters are refused below).
    _check_length(value)
    match = _EXPOSURE.fullmatch(value.strip())
    if match is None:
        raise NormalizationError(SECRET_EXPOSURE_INVALID)
    repository = normalize_repository(match["repository"])
    path = match["path"]
    parts = path.split("/")
    if (
        len(path) > MAX_EXPOSURE_PATH
        or any(part in ("", ".", "..") for part in parts)
        or _has_control(path)
        or path != path.strip()
    ):
        raise NormalizationError(SECRET_EXPOSURE_INVALID)
    return f"{repository}@{match['sha'].lower()}/{path}#L{match['line']}"


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
        case EntityType.FAVICON:
            canonical = normalize_favicon(value)
        case EntityType.REPOSITORY:
            canonical = normalize_repository(value)
        case EntityType.SECRET_EXPOSURE:
            canonical = normalize_secret_exposure(value)
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
    "canonical_ip",
    "canonical_network",
    "format_ipv4",
    "format_ipv6",
    "is_public_suffix",
    "normalize",
    "normalize_asn",
    "normalize_certificate",
    "normalize_crypto_wallet",
    "normalize_domain",
    "normalize_email",
    "normalize_favicon",
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
