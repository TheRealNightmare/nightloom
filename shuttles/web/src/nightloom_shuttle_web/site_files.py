"""The site files Shuttle: a domain's robots.txt, security.txt and sitemap.

**Visits the site** (``mode=visit``): it reads three public files the way a
browser or a search engine would, so it runs only where the case's owners
allow website visits, or on a verified domain. At most four ``GET``
requests, one per second, each through :func:`nightloom_shuttle_web.fetch.fetch`
(same-host redirects only, at most :data:`MAX_FILE_BYTES` read):

1. ``https://<domain>/robots.txt``: its ``Disallow`` and ``Allow`` paths
   (without wildcards) become URLs the domain ``HOSTS``, and its ``Sitemap``
   lines say where the sitemap is;
2. ``https://<domain>/.well-known/security.txt`` (RFC 9116), or
   ``/security.txt`` if that is missing: whether it exists with a
   ``Contact``, its ``Expires`` date, and the *kinds* of contact it gives
   (email, URL, phone). The contacts themselves are never stored: an email
   address is person data, even a role address;
3. the first sitemap ``robots.txt`` names on the same host, or
   ``https://<domain>/sitemap.xml``: the page addresses it lists (``<loc>``)
   become URLs the domain ``HOSTS``. A sitemap index's nested sitemaps are
   counted, never fetched.

Every address goes through the Wayback Shuttle's rules
(:func:`nightloom_shuttle_wayback.filtering.classify`): only the domain and
names under it, no static assets, no query strings or fragments, nothing
with an email address or what looks like a secret in its path. At most
:data:`MAX_URLS` URLs are kept. The domain gets ``site_files_checked_at``,
``robots_txt``, ``robots_disallow_count``, ``sitemap_url_count``,
``security_txt``, ``security_txt_expires`` and ``security_txt_contact_kinds``.

Provenance **A2** (confidence 0.9): read from the site itself, now.
"""

import html
import re
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

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
from nightloom_shuttle_wayback.filtering import Capture, DropReason, classify
from nightloom_shuttle_web.fetch import Page, VisitError, fetch

DOMAIN = EntityType.DOMAIN
URL = EntityType.URL

MAX_FILE_BYTES = 512 * 1024
MAX_URLS = 100
MAX_DISALLOW_URLS = 50
TIMEOUT_SECONDS = 60
CACHE_TTL_SECONDS = 24 * 60 * 60
ADMIRALTY, CONFIDENCE = "A2", 0.9

NOTE_NOT_PUBLIC = (
    "{host} resolves to an address Shuttles may not reach (not public). Nothing was sent."
)
NOTE_UNREACHABLE = "{host} did not answer over HTTPS."
NOTE_NO_ROBOTS = "No robots.txt."
NOTE_NO_SECURITY_TXT = "No security.txt."
NOTE_NO_SITEMAP = "No sitemap."
NOTE_SITEMAP_INDEX = "The sitemap is an index of {count} sitemaps; they were not fetched."
NOTE_DROPPED = "Left out {count} addresses: other hosts, static files or private-looking paths."
NOTE_TRUNCATED = "{name} was longer than {limit} KB; only the start was read."
NOTE_REDIRECTED = "{name} redirects to another host ({target}); it was not followed."

_LOC = re.compile(r"<loc>\s*([^<\s]{1,2048})\s*</loc>", re.IGNORECASE)
_FIELD = re.compile(r"^\s*([A-Za-z-]{1,40})\s*:\s*(.*?)\s*$")

Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Robots:
    paths: list[str] = field(default_factory=list)
    disallow_count: int = 0
    sitemaps: list[str] = field(default_factory=list)


def parse_robots(text: str) -> Robots:
    """``Disallow``/``Allow`` paths without wildcards, the ``Disallow`` count, ``Sitemap`` lines."""
    robots = Robots()
    for line in text.splitlines():
        match = _FIELD.match(line.split("#", 1)[0])
        if match is None:
            continue
        name, value = match.group(1).lower(), match.group(2)
        if name == "disallow" and value:
            robots.disallow_count += 1
        if (
            name in ("disallow", "allow")
            and value.startswith("/")
            and not any(c in value for c in "*$")
        ):
            robots.paths.append(value)
        elif name == "sitemap" and value:
            robots.sitemaps.append(value)
    robots.paths = list(dict.fromkeys(robots.paths))
    return robots


@dataclass
class SecurityTxt:
    valid: bool = False
    expires: datetime | None = None
    contact_kinds: list[str] = field(default_factory=list)


def parse_security_txt(text: str) -> SecurityTxt:
    """RFC 9116: valid with at least one ``Contact``; the kinds of contact and ``Expires``."""
    found = SecurityTxt()
    kinds: dict[str, None] = {}
    for line in text.splitlines():
        match = _FIELD.match(line)
        if match is None or line.lstrip().startswith("#"):
            continue
        name, value = match.group(1).lower(), match.group(2)
        if name == "contact" and value:
            lowered = value.lower()
            if lowered.startswith("mailto:") or ("@" in value and "://" not in value):
                kinds["email"] = None
            elif lowered.startswith(("https://", "http://")):
                kinds["url"] = None
            elif lowered.startswith("tel:"):
                kinds["phone"] = None
        elif name == "expires" and found.expires is None:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is not None:
                found.expires = parsed.astimezone(UTC)
    found.contact_kinds = list(kinds)
    found.valid = bool(kinds)
    return found


def sitemap_locations(text: str) -> tuple[list[str], int]:
    """The page addresses a sitemap lists, and how many nested sitemaps an index names."""
    pages: list[str] = []
    nested = 0
    for raw in _LOC.findall(text):
        location = html.unescape(raw)
        if urlsplit(location).path.lower().endswith((".xml", ".xml.gz")):
            nested += 1
        else:
            pages.append(location)
    return list(dict.fromkeys(pages)), nested


class SiteFilesShuttle(Shuttle):
    """A domain's robots.txt, security.txt and sitemap. See the module docstring."""

    meta = ShuttleMeta(
        name="site-files",
        version="1.0.0",
        title="Site files (robots.txt, security.txt, sitemap)",
        description=(
            "Reads the domain's robots.txt, security.txt and sitemap, like a search engine "
            "would (at most four requests, same-host redirects only). Adds the paths robots.txt "
            "mentions and the pages the sitemap lists as URLs, and notes whether a security.txt "
            "exists, when it expires and which kinds of contact it gives. Visits the site, so it "
            "runs where this case allows website visits, or on a verified domain."
        ),
        input_types=frozenset({DOMAIN}),
        output_entity_types=frozenset({DOMAIN, URL}),
        output_relationship_types=frozenset({RelationType.HOSTS}),
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
        base = f"https://{host}"
        checked_at = self._now()

        async def visit(path: str, name: str) -> Page | None:
            ctx.raise_if_cancelled()
            ctx.progress(f"Reading {name}.")
            page = await fetch(client, urljoin(base, path), max_bytes=MAX_FILE_BYTES)
            if page.redirected_elsewhere is not None:
                ctx.note(NOTE_REDIRECTED.format(name=name, target=page.redirected_elsewhere))
                return None
            if page.truncated:
                ctx.note(NOTE_TRUNCATED.format(name=name, limit=MAX_FILE_BYTES // 1024))
            return page if page.ok else None

        try:
            robots_page = await visit("/robots.txt", "robots.txt")
        except VisitError as error:
            if error.kind == "not_public":
                ctx.note(NOTE_NOT_PUBLIC.format(host=host))
                return
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error

        robots = parse_robots(robots_page.text()) if robots_page is not None else None
        if robots is None:
            ctx.note(NOTE_NO_ROBOTS)

        security: SecurityTxt | None = None
        for path in ("/.well-known/security.txt", "/security.txt"):
            page = await self._optional(visit, path, "security.txt")
            if page is not None:
                security = parse_security_txt(page.text())
                break
        if security is None or not security.valid:
            ctx.note(NOTE_NO_SECURITY_TXT)

        sitemap_path = "/sitemap.xml"
        for location in robots.sitemaps if robots is not None else []:
            parts = urlsplit(urljoin(base, location))
            if (parts.hostname or "").lower() == host:
                sitemap_path = parts.path or "/"
                break
        sitemap_page = await self._optional(visit, sitemap_path, "the sitemap")
        pages: list[str] = []
        if sitemap_page is not None:
            pages, nested = sitemap_locations(sitemap_page.text())
            if nested:
                ctx.note(NOTE_SITEMAP_INDEX.format(count=nested))
        else:
            ctx.note(NOTE_NO_SITEMAP)

        properties: dict[str, Any] = {
            "site_files_checked_at": checked_at,
            "robots_txt": robots is not None,
            "security_txt": security is not None and security.valid,
        }
        if robots is not None:
            properties["robots_disallow_count"] = robots.disallow_count
        if sitemap_page is not None:
            properties["sitemap_url_count"] = len(pages)
        if security is not None and security.valid:
            properties["security_txt_contact_kinds"] = security.contact_kinds
            if security.expires is not None:
                properties["security_txt_expires"] = security.expires
        yield EntityFinding(type=DOMAIN, value=host, properties=properties)

        candidates = [urljoin(base, path) for path in (robots.paths if robots else [])][
            :MAX_DISALLOW_URLS
        ] + pages
        kept, dropped = self._urls(candidates, host, checked_at)
        if dropped:
            ctx.note(NOTE_DROPPED.format(count=dropped))
        for url in kept[:MAX_URLS]:
            yield EntityFinding(type=URL, value=url)
            yield RelationshipFinding(
                type=RelationType.HOSTS, source=entity.ref, target=EntityRef(type=URL, value=url)
            )

    @staticmethod
    async def _optional(visit: Callable[[str, str], Any], path: str, name: str) -> Page | None:
        try:
            page: Page | None = await visit(path, name)
        except VisitError:
            return None
        return page

    @staticmethod
    def _urls(candidates: Iterable[str], host: str, when: datetime) -> tuple[list[str], int]:
        kept: dict[str, None] = {}
        dropped = 0
        for address in candidates:
            result = classify(Capture(when, address, None, None), host)
            if isinstance(result, DropReason):
                dropped += 1
            else:
                kept[result.url] = None
        return list(kept), dropped


__all__ = [
    "MAX_URLS",
    "SiteFilesShuttle",
    "parse_robots",
    "parse_security_txt",
    "sitemap_locations",
]
