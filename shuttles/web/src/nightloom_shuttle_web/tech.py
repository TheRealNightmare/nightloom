"""The web technologies Shuttle: what a domain's home page is built with.

**Visits the site** (``mode=visit``): one ``GET https://<domain>/`` (or
``http://`` if HTTPS does not answer), same-host redirects only, at most
:data:`MAX_PAGE_BYTES` of the page read, like a browser opening it. It runs
only where the case's owners allow website visits, or on a verified domain.

From the response it recognizes software with :data:`RULES`: response
headers (``Server``, ``X-Powered-By``, platform headers), the *names* of
cookies the site sets (never their values), the ``generator`` meta tag and
tell-tale paths and markers in the HTML (``/wp-content/``, ``/_next/``,
``ng-version``, ...). It also reads the page's ``<title>`` and the web
analytics and advertising IDs it embeds (Google Analytics ``UA-``/``G-``,
Tag Manager ``GTM-``, Ads ``AW-``, AdSense ``ca-pub-``), which often link
sites run by the same owner.

It yields the domain with ``web_checked_at``, ``web_title``,
``web_generator`` and ``analytics_ids``; the page it read as a URL the
domain ``HOSTS`` (``status_code``, ``title``, ``content_type``); and each
recognized technology (lower case, no version) with ``USES`` from the domain
and from the page.

Provenance **B2** (confidence 0.8): read from the site itself, now, but
recognized by patterns that can be wrong.
"""

import html
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from nightloom_core.entities import ANALYTICS_IDS_MAX, EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, normalize_url
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

DOMAIN = EntityType.DOMAIN
URL = EntityType.URL
TECHNOLOGY = EntityType.TECHNOLOGY

MAX_PAGE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 45
CACHE_TTL_SECONDS = 24 * 60 * 60
ADMIRALTY, CONFIDENCE = "B2", 0.8
MAX_TITLE_LENGTH = 300

NOTE_NOT_PUBLIC = (
    "{host} resolves to an address Shuttles may not reach (not public). Nothing was sent."
)
NOTE_HTTP_ONLY = "{host} did not answer over HTTPS; the plain HTTP page was read."
NOTE_TLS = "The HTTPS certificate of {host} could not be verified; the plain HTTP page was read."
NOTE_REDIRECTED = "The home page redirects to another host ({target}); it was not followed."
NOTE_NOT_HTML = "The home page is not HTML ({kind}); only its headers were read."
NOTE_STATUS = "The home page answered {status}."

Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Rule:
    """``technology`` is used when ``pattern`` matches ``where``.

    ``where`` is ``header:<name>`` (that header's value), ``cookie`` (a cookie
    name the site sets) or ``html`` (the page).
    """

    technology: str
    where: str
    pattern: re.Pattern[str]


def _r(technology: str, where: str, pattern: str) -> Rule:
    return Rule(technology, where, re.compile(pattern, re.IGNORECASE))


RULES: tuple[Rule, ...] = (
    # Servers, languages and platforms, from headers.
    _r("nginx", "header:server", r"^nginx"),
    _r("apache", "header:server", r"^apache"),
    _r("microsoft-iis", "header:server", r"^microsoft-iis"),
    _r("litespeed", "header:server", r"^litespeed"),
    _r("caddy", "header:server", r"^caddy"),
    _r("openresty", "header:server", r"^openresty"),
    _r("cloudflare", "header:server", r"^cloudflare"),
    _r("cloudflare", "header:cf-ray", r"."),
    _r("php", "header:x-powered-by", r"\bphp\b"),
    _r("asp.net", "header:x-powered-by", r"asp\.net"),
    _r("asp.net", "header:x-aspnet-version", r"."),
    _r("express", "header:x-powered-by", r"^express"),
    _r("next.js", "header:x-powered-by", r"next\.js"),
    _r("drupal", "header:x-drupal-cache", r"."),
    _r("drupal", "header:x-generator", r"drupal"),
    _r("shopify", "header:x-shopify-stage", r"."),
    _r("wix", "header:x-wix-request-id", r"."),
    _r("vercel", "header:x-vercel-id", r"."),
    _r("netlify", "header:x-nf-request-id", r"."),
    _r("amazon-cloudfront", "header:x-amz-cf-id", r"."),
    _r("fastly", "header:x-served-by", r"cache-"),
    _r("varnish", "header:via", r"varnish"),
    _r("github-pages", "header:server", r"^github\.com"),
    # Cookie names only.
    _r("php", "cookie", r"^PHPSESSID$"),
    _r("laravel", "cookie", r"^laravel_session$"),
    _r("asp.net", "cookie", r"^(ASP\.NET_SessionId|\.ASPXAUTH)$"),
    _r("java", "cookie", r"^JSESSIONID$"),
    _r("wordpress", "cookie", r"^(wordpress_|wp-settings-)"),
    _r("shopify", "cookie", r"^_shopify_"),
    _r("codeigniter", "cookie", r"^ci_session$"),
    # The page.
    _r("wordpress", "html", r"/wp-(content|includes)/"),
    _r("drupal", "html", r"/sites/(default|all)/(files|themes|modules)/"),
    _r("joomla", "html", r"/media/jui/|/components/com_"),
    _r("next.js", "html", r"/_next/static/"),
    _r("nuxt", "html", r"/_nuxt/|window\.__NUXT__"),
    _r("gatsby", "html", r"id=\"___gatsby\""),
    _r("angular", "html", r"\bng-version=\""),
    _r("react", "html", r"data-reactroot|react-dom(\.production)?(\.min)?\.js"),
    _r("vue.js", "html", r"\bdata-v-[0-9a-f]{8}\b|vue(\.runtime)?(\.global)?(\.prod)?(\.min)?\.js"),
    _r("jquery", "html", r"jquery[.-]?(\d+(\.\d+)*)?(\.min)?\.js"),
    _r("bootstrap", "html", r"bootstrap(\.bundle)?(\.min)?\.(css|js)"),
    _r("tailwind-css", "html", r"tailwind(css)?(\.min)?\.css"),
    _r("shopify", "html", r"cdn\.shopify\.com"),
    _r("wix", "html", r"static\.wixstatic\.com|static\.parastorage\.com"),
    _r("squarespace", "html", r"static1\.squarespace\.com|squarespace-cdn\.com"),
    _r("webflow", "html", r"assets\.website-files\.com|data-wf-page="),
    _r("google-tag-manager", "html", r"googletagmanager\.com/(gtm|gtag)/js"),
    _r("google-analytics", "html", r"google-analytics\.com/(analytics|ga)\.js|gtag\('config'"),
    _r("cloudflare-web-analytics", "html", r"static\.cloudflareinsights\.com/beacon"),
    _r("hotjar", "html", r"static\.hotjar\.com"),
    _r("recaptcha", "html", r"www\.google\.com/recaptcha/|recaptcha/api\.js"),
    _r("hcaptcha", "html", r"hcaptcha\.com/1/api\.js"),
    _r("cloudflare-turnstile", "html", r"challenges\.cloudflare\.com/turnstile"),
    _r("google-fonts", "html", r"fonts\.googleapis\.com"),
    _r("font-awesome", "html", r"font-?awesome(\.min)?\.(css|js)|kit\.fontawesome\.com"),
)

_GENERATOR = re.compile(
    r"<meta\s+[^>]*?name\s*=\s*[\"']generator[\"'][^>]*?content\s*=\s*[\"']([^\"']{1,200})[\"']"
    r"|<meta\s+[^>]*?content\s*=\s*[\"']([^\"']{1,200})[\"'][^>]*?name\s*=\s*[\"']generator[\"']",
    re.IGNORECASE,
)
_GENERATORS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("wordpress", r"^wordpress"),
        ("drupal", r"^drupal"),
        ("joomla", r"^joomla"),
        ("ghost", r"^ghost"),
        ("hugo", r"^hugo"),
        ("jekyll", r"^jekyll"),
        ("wix", r"wix\.com"),
        ("squarespace", r"^squarespace"),
        ("webflow", r"^webflow"),
        ("typo3", r"^typo3"),
        ("docusaurus", r"^docusaurus"),
        ("gatsby", r"^gatsby"),
        ("astro", r"^astro"),
        ("mediawiki", r"^mediawiki"),
        ("hexo", r"^hexo"),
    )
)
_TITLE = re.compile(r"<title[^>]*>(.{0,2000}?)</title>", re.IGNORECASE | re.DOTALL)
_ANALYTICS = re.compile(
    r"\b(UA-\d{4,10}-\d{1,4}|G-[A-Z0-9]{6,12}|GTM-[A-Z0-9]{4,9}|AW-\d{6,12}|ca-pub-\d{10,20})\b"
)


def _clean(text: str, limit: int) -> str | None:
    value = " ".join(html.unescape(text).split())
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value[:limit]


def cookie_names(headers: httpx.Headers) -> list[str]:
    """The names of the cookies ``Set-Cookie`` headers set (values are never kept)."""
    names: list[str] = []
    for value in headers.get_list("set-cookie"):
        name = value.split("=", 1)[0].strip()
        if name and len(name) <= 100:
            names.append(name)
    return names


@dataclass(frozen=True)
class Fingerprint:
    technologies: list[str]
    title: str | None
    generator: str | None
    analytics_ids: list[str]


def fingerprint(page: Page) -> Fingerprint:
    """What :data:`RULES`, the generator tag, the title and the analytics IDs say about a page."""
    is_html = page.content_type in ("text/html", "application/xhtml+xml", "")
    text = page.text() if is_html else ""
    cookies = cookie_names(page.headers)
    found: dict[str, None] = {}
    for rule in RULES:
        if rule.where == "html":
            hit = bool(text) and rule.pattern.search(text) is not None
        elif rule.where == "cookie":
            hit = any(rule.pattern.search(name) for name in cookies)
        else:
            value = page.headers.get(rule.where.removeprefix("header:"))
            hit = value is not None and rule.pattern.search(value) is not None
        if hit:
            found[rule.technology] = None
    generator: str | None = None
    match = _GENERATOR.search(text) if text else None
    if match is not None:
        generator = _clean(match.group(1) or match.group(2), 200)
        for name, pattern in _GENERATORS:
            if generator is not None and pattern.search(generator):
                found[name] = None
    title_match = _TITLE.search(text) if text else None
    title = _clean(title_match.group(1), MAX_TITLE_LENGTH) if title_match else None
    ids = list(dict.fromkeys(_ANALYTICS.findall(text)))[:ANALYTICS_IDS_MAX] if text else []
    return Fingerprint(sorted(found), title, generator, ids)


class WebTechShuttle(Shuttle):
    """What a domain's home page is built with. See the module docstring."""

    meta = ShuttleMeta(
        name="web-tech",
        version="1.0.0",
        title="Web technologies (home page)",
        description=(
            "Opens the domain's home page like a browser (one request, same-host redirects "
            "only) and recognizes the software behind it from headers, cookie names, the "
            "generator tag and markers in the page: web server, CMS, frameworks, analytics. "
            "Also records the page title and the analytics IDs it embeds, which often link "
            "sites of the same owner. Visits the site, so it runs where this case allows "
            "website visits, or on a verified domain."
        ),
        input_types=frozenset({DOMAIN}),
        output_entity_types=frozenset({DOMAIN, URL, TECHNOLOGY}),
        output_relationship_types=frozenset({RelationType.HOSTS, RelationType.USES}),
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
            ctx.note((NOTE_TLS if error.kind == "tls" else NOTE_HTTP_ONLY).format(host=host))
        if page.redirected_elsewhere is not None:
            ctx.note(NOTE_REDIRECTED.format(target=page.redirected_elsewhere))
        elif page.status >= 400:
            ctx.note(NOTE_STATUS.format(status=page.status))
        if page.content_type not in ("text/html", "application/xhtml+xml", ""):
            ctx.note(NOTE_NOT_HTML.format(kind=page.content_type))
        found = fingerprint(page)
        properties: dict[str, Any] = {
            "web_checked_at": self._now(),
            "web_title": found.title,
            "web_generator": found.generator,
            "analytics_ids": found.analytics_ids,
        }
        yield EntityFinding(
            type=DOMAIN,
            value=host,
            properties={k: v for k, v in properties.items() if v is not None},
        )
        url = self._page_url(page.url)
        if url is not None:
            yield EntityFinding(
                type=URL,
                value=url,
                properties={
                    k: v
                    for k, v in {
                        "status_code": page.status,
                        "title": found.title,
                        "content_type": page.content_type or None,
                    }.items()
                    if v is not None
                },
            )
            yield RelationshipFinding(
                type=RelationType.HOSTS, source=entity.ref, target=EntityRef(type=URL, value=url)
            )
        for name in found.technologies:
            yield EntityFinding(type=TECHNOLOGY, value=name)
            tech = EntityRef(type=TECHNOLOGY, value=name)
            yield RelationshipFinding(type=RelationType.USES, source=entity.ref, target=tech)
            if url is not None:
                yield RelationshipFinding(
                    type=RelationType.USES, source=EntityRef(type=URL, value=url), target=tech
                )

    @staticmethod
    def _page_url(address: str) -> str | None:
        parts = urlsplit(address)
        try:
            # The query string and fragment never: they can hold tokens.
            return normalize_url(urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")))
        except NormalizationError:
            return None


__all__ = ["RULES", "Fingerprint", "WebTechShuttle", "cookie_names", "fingerprint"]
