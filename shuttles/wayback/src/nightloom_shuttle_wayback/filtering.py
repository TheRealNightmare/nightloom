"""Which archived URLs the Wayback Shuttle keeps, and in what form.

The Wayback Machine's CDX index lists every address it captured under a
domain: pages, but also images and scripts, other hosts' URLs that only
look similar, addresses with session tokens or email addresses in them, and
junk. Each archived address goes through :func:`classify`, in this order:

1. **Scope.** Its host, normalized with ``nightloom_core``, must be the
   searched domain or a name under it (the crt.sh Shuttle's rule), so a
   lookalike such as ``evilexample.org`` is dropped.
2. **Static assets.** Images, stylesheets, scripts, fonts, media and
   archives are dropped, by file extension or by the media type the archive
   recorded (one list: :data:`STATIC_ASSETS`).
3. **Privacy.** The query string and the fragment are removed, and so are
   path parameters (``;jsessionid=...``): their values can be session
   tokens, password-reset keys or email addresses. Only the query
   parameters' *names* are kept (:func:`query_param_names`). An address
   whose path contains ``@`` (also percent-encoded) usually names an email
   address and is dropped entirely.
4. **Normalization.** What is left is normalized as a URL with
   ``nightloom_core``; an address that does not normalize (embedded
   credentials included) is dropped.

Each drop is counted by :class:`DropReason`, never quoted. Kept addresses
that end up as the same URL (several query-string variants of one page, http
and https, ``:80``) become one :class:`ArchivedUrl` (:func:`collect`), with
the earliest capture and the number of addresses merged.
"""

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


class DropReason(StrEnum):
    """Why an archived address was not kept."""

    OUT_OF_SCOPE = "out_of_scope"
    STATIC_ASSET = "static_asset"
    EMAIL_IN_PATH = "email_in_path"
    INVALID = "invalid"


DROP_LABELS: dict[DropReason, str] = {
    DropReason.OUT_OF_SCOPE: "outside the domain",
    DropReason.STATIC_ASSET: "static assets",
    DropReason.EMAIL_IN_PATH: "email in path",
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


def has_email_in_path(path: str) -> bool:
    """Whether the path has an ``@``, also percent-encoded (``%40``, or twice: ``%2540``)."""
    return "@" in unquote(unquote(path))


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
    if not parts.scheme or not hostname:
        return DropReason.INVALID
    try:
        host = normalize_domain(hostname)
    except NormalizationError:
        # An IP address or junk: either way not a name under the domain.
        return DropReason.INVALID if not _looks_like_ip(hostname) else DropReason.OUT_OF_SCOPE
    if not in_scope(host, scope):
        return DropReason.OUT_OF_SCOPE
    if is_static_asset(_strip_path_parameters(parts.path), capture.mimetype):
        return DropReason.STATIC_ASSET
    if has_email_in_path(parts.path):
        return DropReason.EMAIL_IN_PATH
    path = _strip_path_parameters(parts.path)
    stripped = bool(parts.query or parts.fragment or path != parts.path)
    try:
        url = normalize_url(urlunsplit((parts.scheme, parts.netloc, path, "", "")))
    except NormalizationError:
        return DropReason.INVALID
    return Kept(url=url, host=host, query_params=query_param_names(parts.query), stripped=stripped)


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
            if kept in (DropReason.STATIC_ASSET, DropReason.EMAIL_IN_PATH):
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
    "FIELDS",
    "MAX_RAW_URL_LENGTH",
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
    "has_email_in_path",
    "in_scope",
    "is_static_asset",
    "parse_rows",
    "parse_timestamp",
    "query_param_names",
    "select",
]
