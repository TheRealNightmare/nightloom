"""The favicon Shuttle: the icon a site shows, as a hash that links sites sharing it.

**Visits the site** (``mode=visit``): ``GET https://<domain>/`` (or
``http://`` if HTTPS does not answer) to find the icon the page declares
(``<link rel="icon">``, ``shortcut icon``, then ``apple-touch-icon``), then
one request for that icon if it is on the same host, and ``/favicon.ico``
if there is none or it cannot be read: at most three requests. An icon
embedded in the page as a ``data:`` address needs no request. Same-host
redirects only; at most :data:`MAX_PAGE_BYTES` of the page and
:data:`MAX_ICON_BYTES` of the icon are read.

It yields the domain with ``favicon_checked_at`` and, when it found an icon,
a Favicon named by its MurmurHash3 in Shodan's form (``http.favicon.hash``),
with the file's ``sha256``, ``md5`` (older tools and VirusTotal search by it), ``mime_type``
and ``size_bytes``, which the domain ``HAS_FAVICON``. Sites that serve the
same icon share that Favicon node, which is the pivot: a phishing copy, a
staging server or a forgotten site of the same owner shows up next to it.

Provenance **A2** (confidence 0.95): read from the site itself, and the hash
is exact; a shared icon is a strong hint, not proof, of a shared owner
(default icons of popular software are shared by many unrelated sites).
"""

import base64
import binascii
import hashlib
import html
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote_to_bytes, urljoin, urlsplit

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    FailureReason,
    Finding,
    Mode,
    RateLimit,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleFailedError,
    ShuttleMeta,
)
from nightloom_shuttle_web.fetch import Page, VisitError, fetch
from nightloom_shuttle_web.murmur import favicon_hash

DOMAIN = EntityType.DOMAIN
FAVICON = EntityType.FAVICON

MAX_PAGE_BYTES = 512 * 1024
MAX_ICON_BYTES = 256 * 1024
TIMEOUT_SECONDS = 45
CACHE_TTL_SECONDS = 24 * 60 * 60
ADMIRALTY, CONFIDENCE = "A2", 0.95

NOTE_NOT_PUBLIC = (
    "{host} resolves to an address Shuttles may not reach (not public). Nothing was sent."
)
NOTE_HTTP_ONLY = "{host} did not answer over HTTPS; the plain HTTP site was read."
NOTE_TLS = "The HTTPS certificate of {host} could not be verified; the plain HTTP site was read."
NOTE_OTHER_HOST = "The page's icon is on another host ({host}); it was not fetched."
NOTE_ICON_UNREADABLE = "The icon at {path} could not be read ({why})."
NOTE_HASHED = "Hashed the icon at {where}."
NOTE_NONE = "The site has no favicon Nightloom could read."

_MIME = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
_LINK = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(
    r"""([a-z][a-z0-9_:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""", re.IGNORECASE
)
_HEAD_END = re.compile(r"</head\s*>|<body\b", re.IGNORECASE)


Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Icon:
    """An icon file and where it came from (an address, or ``data:`` for an embedded one)."""

    where: str
    body: bytes
    mime_type: str | None


def _attributes(tag: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _ATTR.finditer(tag):
        name = match.group(1).lower()
        value = next((g for g in match.groups()[1:] if g is not None), "")
        found.setdefault(name, html.unescape(value).strip())
    return found


def icon_links(page_html: str) -> list[str]:
    """The icon addresses a page declares, best first: ``icon`` before ``apple-touch-icon``."""
    head_end = _HEAD_END.search(page_html)
    head = page_html[: head_end.start()] if head_end else page_html
    ranked: list[tuple[int, int, str]] = []
    for order, match in enumerate(_LINK.finditer(head)):
        attrs = _attributes(match.group(0))
        rel = set(attrs.get("rel", "").lower().split())
        href = attrs.get("href", "")
        if not href:
            continue
        if "icon" in rel:
            rank = 0
        elif rel & {"apple-touch-icon", "apple-touch-icon-precomposed"}:
            rank = 1
        else:
            continue
        ranked.append((rank, order, href))
    seen: set[str] = set()
    links: list[str] = []
    for _, _, href in sorted(ranked):
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links


def _mime(value: str | None) -> str | None:
    if not value:
        return None
    kind = value.split(";", 1)[0].strip().lower()
    return kind if _MIME.fullmatch(kind) and len(kind) <= 127 else None


def decode_data_url(address: str, *, max_bytes: int = MAX_ICON_BYTES) -> Icon | None:
    """The icon in a ``data:`` address, or ``None`` if it is not a readable image."""
    header, comma, payload = address[5:].partition(",")
    if not comma:
        return None
    params = [part.strip() for part in header.split(";")]
    mime = _mime(params[0]) if params and params[0] else None
    if mime is not None and not mime.startswith("image/"):
        return None
    try:
        if "base64" in (part.lower() for part in params[1:]):
            if len(payload) > max_bytes * 4 // 3 + 4:
                return None
            body = base64.b64decode(payload, validate=False)
        else:
            body = unquote_to_bytes(payload)
    except (binascii.Error, ValueError):
        return None
    if not body or len(body) > max_bytes:
        return None
    return Icon(where="data:", body=body, mime_type=mime)


def _icon_problem(page: Page) -> str | None:
    """Why ``page`` is not an icon file, or ``None`` if it is one."""
    if page.redirected_elsewhere is not None:
        return f"redirects to {page.redirected_elsewhere}"
    if page.status != 200:
        return f"status {page.status}"
    if not page.body:
        return "empty"
    if page.truncated:
        return f"larger than {MAX_ICON_BYTES // 1024} KB"
    kind = page.content_type
    # A "200 OK" page of HTML is a site's catch-all page, not an icon.
    if kind in ("text/html", "application/xhtml+xml") or (
        kind in ("", "text/plain") and page.body.lstrip()[:1] == b"<"
    ):
        return "not an image"
    return None


class FaviconShuttle(Shuttle):
    """The icon a site shows, as a hash that links sites sharing it. See the module docstring."""

    meta = ShuttleMeta(
        name="favicon",
        version="1.0.0",
        title="Favicon hash",
        description=(
            "Opens the domain's home page like a browser to find its icon, reads the icon "
            "(at most three requests, same host only) and adds it as a Favicon named by its "
            "MurmurHash3, the hash Shodan and FOFA search for. Sites that serve the same icon "
            "meet on one node. Visits the site, so it runs where this case allows website "
            "visits, or on a verified domain."
        ),
        input_types=frozenset({DOMAIN}),
        output_entity_types=frozenset({DOMAIN, FAVICON}),
        output_relationship_types=frozenset({RelationType.HAS_FAVICON}),
        mode=Mode.VISIT,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        cache_ttl_seconds=CACHE_TTL_SECONDS,
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
    )

    def __init__(self, *, http: httpx.AsyncClient | None = None, now: Now = _utcnow) -> None:
        """``http`` and ``now`` replace the real ones in tests (a given client stays open)."""
        self._http = http
        self._now = now

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        host = entity.value
        client = self._http or ctx.http
        ctx.progress(f"Opening https://{host}/.")
        scheme = "https"
        try:
            page = await fetch(client, f"https://{host}/", max_bytes=MAX_PAGE_BYTES)
        except VisitError as error:
            if error.kind == "not_public":
                ctx.note(NOTE_NOT_PUBLIC.format(host=host))
                return
            ctx.raise_if_cancelled()
            try:
                page = await fetch(client, f"http://{host}/", max_bytes=MAX_PAGE_BYTES)
            except VisitError as second:
                if second.kind == "not_public":
                    ctx.note(NOTE_NOT_PUBLIC.format(host=host))
                    return
                raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from second
            scheme = "http"
            ctx.note((NOTE_TLS if error.kind == "tls" else NOTE_HTTP_ONLY).format(host=host))

        icon = await self._find_icon(ctx, client, host, scheme, page)
        yield EntityFinding(type=DOMAIN, value=host, properties={"favicon_checked_at": self._now()})
        if icon is None:
            ctx.note(NOTE_NONE)
            return
        number = favicon_hash(icon.body)
        ctx.note(NOTE_HASHED.format(where=icon.where))
        properties: dict[str, Any] = {
            "sha256": hashlib.sha256(icon.body).hexdigest(),
            "md5": hashlib.md5(icon.body, usedforsecurity=False).hexdigest(),
            "size_bytes": len(icon.body),
        }
        if icon.mime_type is not None:
            properties["mime_type"] = icon.mime_type
        yield EntityFinding(type=FAVICON, value=str(number), properties=properties)
        yield RelationshipFinding(
            type=RelationType.HAS_FAVICON,
            source=entity.ref,
            target=EntityRef(type=FAVICON, value=str(number)),
        )

    async def _find_icon(
        self,
        ctx: ShuttleContext,
        client: httpx.AsyncClient,
        host: str,
        scheme: str,
        page: Page,
    ) -> Icon | None:
        base = page.url
        candidates: list[str] = []
        if page.redirected_elsewhere is None and page.content_type in (
            "text/html",
            "application/xhtml+xml",
            "",
        ):
            for href in icon_links(page.text()):
                if href.lower().startswith("data:"):
                    embedded = decode_data_url(href)
                    if embedded is not None:
                        return embedded
                    continue
                target = urljoin(base, href)
                parts = urlsplit(target)
                if parts.scheme not in ("http", "https"):
                    continue
                if (parts.hostname or "").lower() != host:
                    ctx.note(NOTE_OTHER_HOST.format(host=parts.hostname or "?"))
                    continue
                # One declared icon is tried: it is the one browsers show.
                candidates.append(target)
                break
        fallback = f"{scheme}://{host}/favicon.ico"
        if fallback not in {self._without_fragment(c) for c in candidates}:
            candidates.append(fallback)
        for address in candidates:
            ctx.raise_if_cancelled()
            path = urlsplit(address).path or "/"
            ctx.progress(f"Reading {path}.")
            try:
                reply = await fetch(client, address, max_bytes=MAX_ICON_BYTES)
            except VisitError as error:
                if error.kind == "not_public":
                    ctx.note(NOTE_NOT_PUBLIC.format(host=host))
                    return None
                ctx.note(NOTE_ICON_UNREADABLE.format(path=path, why="no answer"))
                continue
            problem = _icon_problem(reply)
            if problem is not None:
                ctx.note(NOTE_ICON_UNREADABLE.format(path=path, why=problem))
                continue
            return Icon(where=path, body=reply.body, mime_type=_mime(reply.content_type))
        return None

    @staticmethod
    def _without_fragment(address: str) -> str:
        return address.split("#", 1)[0]


__all__ = [
    "MAX_ICON_BYTES",
    "FaviconShuttle",
    "decode_data_url",
    "icon_links",
]
