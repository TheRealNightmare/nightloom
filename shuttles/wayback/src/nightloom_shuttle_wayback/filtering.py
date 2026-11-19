"""Which archived URLs the Wayback Shuttle keeps, and in what form.

The Wayback Machine's CDX index lists every address it captured under a
domain: pages, but also images and scripts, other hosts' URLs that only
look similar, addresses with session tokens or email addresses in them, and
junk. Each archived address goes through :func:`classify`, in this order:

1. **Credentials.** An address with user information before its host
   (``user@`` or ``user:password@``, also percent-encoded) is dropped.
2. **Scope.** Its host, normalized with ``nightloom_core``, must be the
   searched domain or a name under it (the crt.sh Shuttle's rule), so a
   lookalike such as ``evilexample.org`` is dropped.
3. **Hidden queries.** The path is percent-decoded up to twice
   (:func:`split_encoded_query`). If that reveals a ``?`` or ``#``
   (``/reset%3Ftoken=...``, ``%23``, ``%253F``), the path is cut before the
   encoded sequence and what follows is treated as a query string or a
   fragment. A path still encoded after two rounds is dropped as invalid.
4. **Static assets.** Images, stylesheets, scripts, fonts, media and
   archives are dropped, by file extension or by the media type the archive
   recorded (one list: :data:`STATIC_ASSETS`).
5. **Privacy.** The query string and the fragment are removed (the real
   ones and the hidden ones), and so are path parameters
   (``;jsessionid=...``): their values can be session tokens,
   password-reset keys or email addresses. Only the query parameters'
   *names* are kept (:func:`query_param_names`). An address whose path
   contains ``@`` (also percent-encoded) usually names an email address and
   is dropped entirely, and so is one whose path looks like it holds a
   secret (:func:`has_secret_in_path`: :data:`SECRET_PATTERNS` and a
   conservative entropy rule).
6. **Normalization.** What is left is normalized as a URL with
   ``nightloom_core``; an address that does not normalize is dropped.

Each drop is counted by :class:`DropReason`, never quoted. Kept addresses
that end up as the same URL (several query-string variants of one page, http
and https, ``:80``) become one :class:`ArchivedUrl` (:func:`collect`), with
the earliest capture and the number of addresses merged.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

from nightloom_core.entities import ARCHIVE_MAX_QUERY_PARAMS, ARCHIVE_QUERY_PARAM_MAX_LENGTH
from nightloom_core.normalize import NormalizationError, normalize_domain, normalize_url

# The fields asked for, in this order (``fl=``).
FIELDS = ("timestamp", "original", "statuscode", "mimetype")
# Longer raw addresses are refused without looking further.
MAX_RAW_URL_LENGTH = 4096

# One list of what counts as a static asset: per kind, file extensions and
# media types (an entry ending in "/" is a prefix, such as every image/*).
STATIC_ASSETS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = {
    "images": (
        frozenset(
            {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".avif", ".tif",
             ".tiff", ".heic"}
        ),
        frozenset({"image/"}),
    ),
    "stylesheets": (frozenset({".css", ".scss", ".less"}), frozenset({"text/css"})),
    "scripts": (
        frozenset({".js", ".mjs", ".map"}),
        frozenset(
            {"application/javascript", "application/x-javascript", "application/ecmascript",
             "text/javascript", "text/ecmascript"}
        ),
    ),
    "fonts": (
        frozenset({".woff", ".woff2", ".ttf", ".otf", ".eot"}),
        frozenset(
            {"font/", "application/font-woff", "application/font-woff2", "application/x-font-ttf",
             "application/x-font-otf", "application/x-font-woff", "application/vnd.ms-fontobject"}
        ),
    ),
    "media": (
        frozenset(
            {".mp3", ".mp4", ".m4a", ".m4v", ".wav", ".ogg", ".oga", ".ogv", ".webm", ".avi",
             ".mov", ".flac", ".aac", ".mkv", ".wmv", ".flv", ".swf"}
        ),
        frozenset({"audio/", "video/", "application/ogg", "application/x-shockwave-flash"}),
    ),
    "archives": (
        frozenset({".zip", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".7z", ".rar"}),
        frozenset(
            {"application/zip", "application/x-zip-compressed", "application/gzip",
             "application/x-gzip", "application/x-tar", "application/x-bzip2", "application/x-xz",
             "application/x-7z-compressed", "application/x-rar-compressed", "application/vnd.rar"}
        ),
    ),
}  # fmt: skip
STATIC_EXTENSIONS = frozenset(ext for exts, _ in STATIC_ASSETS.values() for ext in exts)
_STATIC_MIMETYPES = frozenset(m for _, types in STATIC_ASSETS.values() for m in types)
STATIC_MIMETYPE_PREFIXES = tuple(sorted(m for m in _STATIC_MIMETYPES if m.endswith("/")))
STATIC_MIMETYPES = frozenset(m for m in _STATIC_MIMETYPES if not m.endswith("/"))

# A query parameter name worth keeping: a plain identifier, nothing that
# looks like a value (no "@", "=", spaces, slashes or other punctuation).
_PARAM_NAME = re.compile(rf"[A-Za-z0-9_.\-\[\]]{{1,{ARCHIVE_QUERY_PARAM_MAX_LENGTH}}}")
_TIMESTAMP = re.compile(r"[0-9]{14}")
# The core's media type rule (``MimeType``), applied to the lower-cased text.
_MIMETYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")


# --- secrets in paths ---
#
# What looks like a credential in a path, on the percent-decoded path. Every
# pattern is anchored: a "segment" pattern must match a whole path segment
# (or the segment without its file extension), a "path" pattern starts at a
# segment boundary or is a fixed marker. None has nested quantifiers, so each
# runs in linear time. Matching is deliberately prefix-based: a token in the
# middle of a segment (``/download-ghp_...``) is not recognized.
SEGMENT, PATH = "segment", "path"
# Path segment names that announce a credential in the next segment.
SECRET_SEGMENT_NAMES = (
    "token", "access_token", "accesstoken", "auth", "auth_token", "apikey", "api_key", "api-key",
    "secret", "client_secret", "password", "passwd", "private_key",
)  # fmt: skip
SECRET_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", SEGMENT, re.compile(r"\A(?:AKIA|ASIA)[A-Z0-9]{16}\Z")),
    ("google_api_key", SEGMENT, re.compile(r"\AAIza[0-9A-Za-z_-]{35}\Z")),
    (
        "github_token",
        SEGMENT,
        re.compile(r"\A(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,255})\Z"),
    ),
    ("slack_token", SEGMENT, re.compile(r"\Axox[abposr]-[A-Za-z0-9-]{10,255}\Z")),
    ("stripe_key", SEGMENT, re.compile(r"\A(?:sk|rk|pk)_live_[A-Za-z0-9]{1,255}\Z")),
    ("twilio_key", SEGMENT, re.compile(r"\ASK[0-9a-f]{32}\Z")),
    (
        "jwt",
        SEGMENT,
        re.compile(r"\AeyJ[A-Za-z0-9_-]{1,4096}\.[A-Za-z0-9_-]{1,4096}\.[A-Za-z0-9_-]{0,4096}\Z"),
    ),
    ("private_key", PATH, re.compile(r"-----BEGIN")),
    (
        "named_secret",
        PATH,
        re.compile(
            rf"(?:\A|/)(?i:{'|'.join(map(re.escape, SECRET_SEGMENT_NAMES))})"
            r"/(?P<value>[A-Za-z0-9_-]{16,4096})(?=/|\Z)"
        ),
    ),
)  # fmt: skip
# The entropy rule: one segment of at least this many characters from the
# token alphabet, with letters and digits, and at least this much Shannon
# entropy per character, is taken for a random token.
ENTROPY_MIN_LENGTH = 32
ENTROPY_MIN_BITS = 4.0
_TOKEN_ALPHABET = re.compile(r"[A-Za-z0-9+/=_-]+")
# Lower-case hex of an MD5, SHA-1 or SHA-256 length: usually a content hash.
_CONTENT_HASH = re.compile(r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}")
# A "word slug": parts separated by "-" or "_", each a word (lower case,
# Capitalized or UPPER) with at most a short number after it, or a number.
_SLUG_PART = re.compile(r"[0-9]+|(?:[a-z]+|[A-Z][a-z]+|[A-Z]+)[0-9]{0,4}")


class DropReason(StrEnum):
    """Why an archived address was not kept."""

    CREDENTIALS_IN_URL = "credentials_in_url"
    OUT_OF_SCOPE = "out_of_scope"
    STATIC_ASSET = "static_asset"
    EMAIL_IN_PATH = "email_in_path"
    SECRET_IN_PATH = "secret_in_path"  # noqa: S105 - a reason's name, not a secret
    INVALID = "invalid"


DROP_LABELS: dict[DropReason, str] = {
    DropReason.CREDENTIALS_IN_URL: "credentials in URL",
    DropReason.OUT_OF_SCOPE: "outside the domain",
    DropReason.STATIC_ASSET: "static assets",
    DropReason.EMAIL_IN_PATH: "email in path",
    DropReason.SECRET_IN_PATH: "possible secret in path",
    DropReason.INVALID: "invalid",
}


@dataclass(frozen=True)
class Capture:
    """One row of the CDX index: a capture of ``original`` at ``timestamp``."""

    timestamp: datetime
    original: str
    status: int | None
    mimetype: str | None


@dataclass(frozen=True)
class Kept:
    """An archived address that passed every rule, as it will be stored."""

    url: str
    host: str
    query_params: frozenset[str]
    stripped: bool


@dataclass
class ArchivedUrl:
    """One stored URL and what the archive said about the addresses merged into it.

    ``latest`` is the latest capture *seen* (the CDX listing reports the
    first capture of each archived address); ``last_known`` says whether it
    is known to be the URL's latest capture in the archive.
    """

    url: str
    host: str
    first: datetime
    latest: datetime
    status: int | None
    mimetype: str | None
    count: int = 0
    query_params: set[str] = field(default_factory=set)
    last_known: bool = False

    def observe(self, capture: Capture, kept: Kept, *, counted: bool = True) -> None:
        """Merge another capture; ``counted`` adds it to the archived addresses merged."""
        if counted:
            self.count += 1
        self.query_params |= kept.query_params
        self.first = min(self.first, capture.timestamp)
        if capture.timestamp >= self.latest:
            self.latest = capture.timestamp
            self.status = capture.status if capture.status is not None else self.status
            self.mimetype = capture.mimetype or self.mimetype

    def sorted_params(self) -> list[str]:
        """The parameter names, sorted, at most ``ARCHIVE_MAX_QUERY_PARAMS``."""
        return sorted(self.query_params)[:ARCHIVE_MAX_QUERY_PARAMS]


# --- rows ------------------------------------------------------------------------------------


def parse_timestamp(text: str) -> datetime:
    """A CDX timestamp (``yyyyMMddhhmmss``, UTC). ``ValueError`` if it is not one."""
    if not _TIMESTAMP.fullmatch(text):
        raise ValueError("not a 14-digit timestamp")
    return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def _status(text: str) -> int | None:
    """The HTTP status, or ``None`` for ``-`` (revisits and other records without one)."""
    if text.isdigit() and 100 <= int(text) <= 599:
        return int(text)
    return None


def _mimetype(text: str) -> str | None:
    value = text.strip().lower()
    return value if len(value) <= 127 and _MIMETYPE.fullmatch(value) else None


def parse_rows(rows: Iterable[Any]) -> tuple[list[Capture], int]:
    """The captures in a CDX JSON answer, and how many rows were malformed.

    The answer's first row names the fields (``["timestamp", "original",
    ...]``) and is skipped. Every other row must be a list of four strings.
    """
    captures: list[Capture] = []
    malformed = 0
    for row in rows:
        if isinstance(row, list) and tuple(row) == FIELDS:
            continue
        if not (
            isinstance(row, list)
            and len(row) == len(FIELDS)
            and all(isinstance(cell, str) for cell in row)
        ):
            malformed += 1
            continue
        timestamp, original, status, mimetype = row
        try:
            when = parse_timestamp(timestamp)
        except ValueError:
            malformed += 1
            continue
        if not original.strip() or len(original) > MAX_RAW_URL_LENGTH:
            malformed += 1
            continue
        captures.append(Capture(when, original.strip(), _status(status), _mimetype(mimetype)))
    return captures, malformed


# --- one address -----------------------------------------------------------------------------


def in_scope(name: str, scope: str) -> bool:
    """Whether the normalized ``name`` is ``scope`` or a name under it."""
    return name == scope or name.endswith(f".{scope}")


def is_static_asset(path: str, mimetype: str | None) -> bool:
    """Whether the path's extension or the recorded media type is a static asset's."""
    segment = path.rsplit("/", 1)[-1].lower()
    if "." in segment and f".{segment.rsplit('.', 1)[1]}" in STATIC_EXTENSIONS:
        return True
    if mimetype is None:
        return False
    return mimetype in STATIC_MIMETYPES or mimetype.startswith(STATIC_MIMETYPE_PREFIXES)


def has_credentials(netloc: str) -> bool:
    """Whether the authority has user information (``user@``, ``user:pw@``, also encoded)."""
    return "@" in unquote(unquote(netloc))


def has_email_in_path(path: str) -> bool:
    """Whether the path has an ``@``, also percent-encoded (``%40``, or twice: ``%2540``)."""
    return "@" in unquote(unquote(path))


# --- percent-encoding ---

_HEX_DIGITS = frozenset(b"0123456789abcdefABCDEF")
# What a path still holds after two rounds of decoding if it was encoded
# three times or more: an encoded "?", "#" or "%".
_STILL_ENCODED = re.compile(r"%(?:3f|23|25)", re.IGNORECASE)
# Paths are handled as UTF-8 bytes; "surrogatepass" makes any str round-trip.
_ENCODING, _ERRORS = "utf-8", "surrogatepass"


def _percent_decode(data: bytes) -> tuple[bytes, list[int]]:
    """``data`` with ``%XX`` decoded, and for each byte out the index in ``data`` it came from."""
    out = bytearray()
    origin: list[int] = []
    i = 0
    while i < len(data):
        if (
            data[i] == 0x25  # "%"
            and i + 2 < len(data)
            and data[i + 1] in _HEX_DIGITS
            and data[i + 2] in _HEX_DIGITS
        ):
            out.append(int(data[i + 1 : i + 3], 16))
            origin.append(i)
            i += 3
        else:
            out.append(data[i])
            origin.append(i)
            i += 1
    return bytes(out), origin


def _decode_twice(path: str) -> tuple[bytes, list[int]]:
    """``path`` percent-decoded up to twice (stopping when nothing changes).

    Also returns, for each decoded byte, its index in ``path``'s UTF-8 bytes.
    """
    data = path.encode(_ENCODING, _ERRORS)
    origin = list(range(len(data)))
    for _ in range(2):
        decoded, step = _percent_decode(data)
        if decoded == data:
            break
        origin = [origin[j] for j in step]
        data = decoded
    return data, origin


def decoded_path(path: str) -> str:
    """``path`` percent-decoded up to twice, for inspection only (never stored)."""
    return _decode_twice(path)[0].decode(_ENCODING, "replace")


def split_encoded_query(path: str) -> tuple[str, frozenset[str]]:
    """Cut a query string or fragment hidden by percent-encoding out of ``path``.

    ``/reset%3Ftoken=X`` (also ``%3f``, ``%23``, or encoded twice:
    ``%253F``) decodes to a path with a ``?`` or ``#`` in it. Everything from
    the first of them is a query string or a fragment: the path kept is the
    *original* one, cut before the encoded sequence, and of the query only
    the parameter names are returned. A path without one comes back
    unchanged, with no names.
    """
    data, origin = _decode_twice(path)
    marks = [i for i in (data.find(b"?"), data.find(b"#")) if i >= 0]
    if not marks:
        return path, frozenset()
    cut = min(marks)
    kept = path.encode(_ENCODING, _ERRORS)[: origin[cut]].decode(_ENCODING, _ERRORS)
    names: frozenset[str] = frozenset()
    if data[cut : cut + 1] == b"?":
        query = data[cut + 1 :].split(b"#", 1)[0].decode(_ENCODING, "replace")
        names = query_param_names(query)
    return kept, names


# --- secrets ---


def shannon_entropy(text: str) -> float:
    """Bits per character of ``text``'s own character distribution."""
    if not text:
        return 0.0
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _is_word_slug(text: str) -> bool:
    """``how-we-built-it-2020``, ``oauth2_callback``, ``Annual-Report``: words, not a token."""
    return all(_SLUG_PART.fullmatch(part) for part in re.split(r"[-_]", text))


def _looks_random(segment: str) -> bool:
    """The entropy rule (see :data:`ENTROPY_MIN_BITS`), conservative on purpose.

    Long words joined by hyphens and plain lower-case hex hashes (MD5,
    SHA-1, SHA-256 lengths, common in asset and download names) are kept;
    so is anything without both letters and digits. A random token that is
    all letters, or a camel-case name with digits in the middle, can still
    be misjudged either way.
    """
    return (
        len(segment) >= ENTROPY_MIN_LENGTH
        and _TOKEN_ALPHABET.fullmatch(segment) is not None
        and any(c.isdigit() for c in segment)
        and any(c.isalpha() for c in segment)
        and not _CONTENT_HASH.fullmatch(segment)
        and not _is_word_slug(segment)
        and shannon_entropy(segment) >= ENTROPY_MIN_BITS
    )


def _segments(path: str) -> list[str]:
    """The decoded path's segments, without path parameters, each also without its extension."""
    candidates: list[str] = []
    for segment in path.split("/"):
        segment = segment.split(";", 1)[0]
        if not segment:
            continue
        candidates.append(segment)
        stem, dot, extension = segment.rpartition(".")
        if dot and stem and extension.isalnum() and len(extension) <= 10:
            candidates.append(stem)
    return candidates


def has_secret_in_path(path: str) -> bool:
    """Whether the (raw) ``path`` looks like it holds a credential, once decoded.

    Tests :data:`SECRET_PATTERNS` one by one, then the entropy rule. Says
    only *whether*: what matched is never returned, logged or kept.
    """
    decoded = decoded_path(path)
    segments = _segments(decoded)
    for _name, scope, pattern in SECRET_PATTERNS:
        if scope == SEGMENT:
            if any(pattern.match(segment) for segment in segments):
                return True
            continue
        for match in pattern.finditer(decoded):
            value = match.groupdict().get("value")
            if value is None or not _is_word_slug(value):
                return True
    return any(_looks_random(segment) for segment in segments)


def query_param_names(query: str) -> frozenset[str]:
    """The names of the query's ``name=value`` parameters, never a value.

    A bare item without ``=`` (``?3f9a...``) may itself be a value, so it is
    left out, and so is every name that is not a plain identifier of at most
    64 characters.
    """
    names: set[str] = set()
    for part in re.split(r"[&;]", query):
        if "=" not in part:
            continue
        name = unquote_plus(part.split("=", 1)[0]).strip()
        if _PARAM_NAME.fullmatch(name):
            names.add(name)
    return frozenset(names)


def _strip_path_parameters(path: str) -> str:
    """``/a;jsessionid=X/b;v=1`` → ``/a/b``: path parameters can hold session IDs."""
    return "/".join(segment.split(";", 1)[0] for segment in path.split("/"))


def classify(capture: Capture, scope: str) -> Kept | DropReason:
    """Keep ``capture``'s address under ``scope`` (a normalized domain), or say why not."""
    try:
        parts = urlsplit(capture.original)
        hostname = parts.hostname
    except ValueError:
        return DropReason.INVALID
    # Before anything else: the user information is the credential itself.
    if has_credentials(parts.netloc):
        return DropReason.CREDENTIALS_IN_URL
    if not parts.scheme or not hostname:
        return DropReason.INVALID
    try:
        host = normalize_domain(hostname)
    except NormalizationError:
        # An IP address or junk: either way not a name under the domain.
        return DropReason.INVALID if not _looks_like_ip(hostname) else DropReason.OUT_OF_SCOPE
    if not in_scope(host, scope):
        return DropReason.OUT_OF_SCOPE
    visible, hidden_params = split_encoded_query(parts.path)
    path = _strip_path_parameters(visible)
    if is_static_asset(path, capture.mimetype):
        return DropReason.STATIC_ASSET
    if has_email_in_path(parts.path):  # the whole path, hidden query included
        return DropReason.EMAIL_IN_PATH
    if has_secret_in_path(path):
        return DropReason.SECRET_IN_PATH
    if _STILL_ENCODED.search(decoded_path(path)):
        # Encoded three times or more: no real page, and a "?" may hide inside.
        return DropReason.INVALID
    stripped = bool(parts.query or parts.fragment or path != parts.path)
    try:
        url = normalize_url(urlunsplit((parts.scheme, parts.netloc, path, "", "")))
    except NormalizationError:
        return DropReason.INVALID
    return Kept(
        url=url,
        host=host,
        query_params=query_param_names(parts.query) | hidden_params,
        stripped=stripped,
    )


def _looks_like_ip(hostname: str) -> bool:
    return hostname.replace(".", "").isdigit() or ":" in hostname


# --- many addresses --------------------------------------------------------------------------


@dataclass
class Collected:
    """What :func:`collect` made of the captures."""

    urls: dict[str, ArchivedUrl] = field(default_factory=dict)
    dropped: Counter[DropReason] = field(default_factory=Counter)
    stripped: int = 0
    # The earliest and latest capture under the domain (static assets included).
    first: datetime | None = None
    last: datetime | None = None

    def saw(self, when: datetime) -> None:
        self.first = when if self.first is None else min(self.first, when)
        self.last = when if self.last is None else max(self.last, when)

    def add(self, capture: Capture, scope: str, *, counted: bool = True) -> ArchivedUrl | None:
        """Classify and merge one capture; the URL it went into, or ``None`` if dropped."""
        kept = classify(capture, scope)
        if isinstance(kept, DropReason):
            if counted:
                self.dropped[kept] += 1
            if kept in (
                DropReason.STATIC_ASSET,
                DropReason.EMAIL_IN_PATH,
                DropReason.SECRET_IN_PATH,
            ):
                self.saw(capture.timestamp)  # still a capture under the domain
            return None
        self.saw(capture.timestamp)
        if kept.stripped and counted:
            self.stripped += 1
        entry = self.urls.get(kept.url)
        if entry is None:
            entry = ArchivedUrl(
                url=kept.url,
                host=kept.host,
                first=capture.timestamp,
                latest=capture.timestamp,
                status=capture.status,
                mimetype=capture.mimetype,
            )
            self.urls[kept.url] = entry
        entry.observe(capture, kept, counted=counted or entry.count == 0)
        return entry


def collect(captures: Iterable[Capture], scope: str) -> Collected:
    """Classify every capture and merge the kept ones by their stored URL."""
    collected = Collected()
    for capture in captures:
        collected.add(capture, scope)
    return collected


@dataclass(frozen=True)
class Selection:
    """The URLs to store, the subdomains they need, and what the caps left out."""

    urls: list[ArchivedUrl]
    subdomains: list[str]
    over_url_cap: int
    over_subdomain_cap: int


def select(
    urls: Iterable[ArchivedUrl], scope: str, *, max_urls: int, max_subdomains: int
) -> Selection:
    """The most recently captured URLs first, at most ``max_urls``.

    A URL on a subdomain needs that subdomain as a Domain; once
    ``max_subdomains`` are taken, URLs on further subdomains are left out
    (counted in ``over_subdomain_cap``), so every stored URL has its host.
    """
    ordered = sorted(urls, key=lambda u: (-u.latest.timestamp(), u.url))
    chosen: list[ArchivedUrl] = []
    subdomains: dict[str, None] = {}
    over_subdomains = 0
    for entry in ordered:
        if len(chosen) >= max_urls:
            break
        if entry.host != scope and entry.host not in subdomains:
            if len(subdomains) >= max_subdomains:
                over_subdomains += 1
                continue
            subdomains[entry.host] = None
        chosen.append(entry)
    over_urls = max(0, len(ordered) - len(chosen) - over_subdomains)
    return Selection(chosen, sorted(subdomains), over_urls, over_subdomains)


__all__ = [
    "DROP_LABELS",
    "ENTROPY_MIN_BITS",
    "ENTROPY_MIN_LENGTH",
    "FIELDS",
    "MAX_RAW_URL_LENGTH",
    "SECRET_PATTERNS",
    "SECRET_SEGMENT_NAMES",
    "STATIC_ASSETS",
    "STATIC_EXTENSIONS",
    "STATIC_MIMETYPES",
    "STATIC_MIMETYPE_PREFIXES",
    "ArchivedUrl",
    "Capture",
    "Collected",
    "DropReason",
    "Kept",
    "Selection",
    "classify",
    "collect",
    "decoded_path",
    "has_credentials",
    "has_email_in_path",
    "has_secret_in_path",
    "in_scope",
    "is_static_asset",
    "parse_rows",
    "parse_timestamp",
    "query_param_names",
    "select",
    "shannon_entropy",
    "split_encoded_query",
]
