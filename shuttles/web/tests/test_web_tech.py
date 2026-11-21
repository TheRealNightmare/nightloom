"""The web technologies Shuttle against a fake site: no network."""

import ssl
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from nightloom_core.entities import EntityType, build_entity
from nightloom_sdk import (
    EntityFinding,
    FailureReason,
    Finding,
    Mode,
    RelationshipFinding,
    ShuttleFailedError,
)
from nightloom_sdk.http import DestinationNotAllowedError
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_web import WebTechShuttle
from nightloom_shuttle_web.fetch import Page
from nightloom_shuttle_web.tech import RULES, cookie_names, fingerprint

D = EntityType.DOMAIN
META = WebTechShuttle.meta
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
HOST = "example.org"

WORDPRESS = """<!doctype html><html><head>
<title>  Example &amp; Co —
  Home </title>
<meta name="generator" content="WordPress 6.4.2" />
<link rel="stylesheet" href="/wp-content/themes/x/bootstrap.min.css">
<script src="/wp-includes/js/jquery/jquery.min.js?ver=3.7.1"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABC123XYZ"></script>
<script>gtag('config', 'G-ABC123XYZ'); gtag('config', 'UA-12345678-1');</script>
<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">
</head><body>ca-pub-1234567890123456</body></html>"""


def html_page(body: str = WORDPRESS, **headers: str) -> httpx.Response:
    base = {
        "content-type": "text/html; charset=utf-8",
        "server": "nginx/1.25.3",
        "x-powered-by": "PHP/8.2.12",
        **headers,
    }
    pairs = [
        *base.items(),
        ("set-cookie", "PHPSESSID=secretvalue; path=/"),
        ("set-cookie", "wp-settings-1=x"),
    ]
    return httpx.Response(200, content=body.encode(), headers=pairs)


class FakeSite:
    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        reply = self.replies.get(url)
        if reply is None:
            return httpx.Response(404)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, httpx.Response)
        return reply


async def run(replies: dict[str, Any]) -> tuple[list[Finding], tuple[str, ...], FakeSite]:
    site = FakeSite(replies)
    async with httpx.AsyncClient(transport=httpx.MockTransport(site.handler)) as client:
        ctx = fake_context(META)
        entity = entity_input(D, HOST)
        findings = await collect(WebTechShuttle(http=client, now=lambda: NOW), entity, ctx=ctx)
        assert_declared(META, findings, entity)
        for finding in findings:
            if isinstance(finding, EntityFinding):
                build_entity(finding.type, finding.value, finding.properties)
    return findings, ctx.notes, site


def of(findings: list[Finding], kind: EntityType) -> dict[str, dict[str, Any]]:
    return {
        f.value: f.properties or {}
        for f in findings
        if isinstance(f, EntityFinding) and f.type == kind
    }


def test_meta() -> None:
    assert META.mode == Mode.VISIT
    assert META.cache_ttl_seconds == 24 * 3600
    assert len({(r.technology, r.where) for r in RULES}) == len(RULES)


async def test_a_wordpress_site() -> None:
    findings, notes, site = await run({f"https://{HOST}/": html_page()})
    assert site.requests == [f"https://{HOST}/"]
    assert notes == ()
    assert of(findings, D)[HOST] == {
        "web_checked_at": NOW,
        "web_title": "Example & Co — Home",
        "web_generator": "WordPress 6.4.2",
        "analytics_ids": ["G-ABC123XYZ", "UA-12345678-1", "ca-pub-1234567890123456"],
    }
    assert set(of(findings, EntityType.TECHNOLOGY)) == {
        "nginx",
        "php",
        "wordpress",
        "jquery",
        "bootstrap",
        "google-tag-manager",
        "google-analytics",
        "google-fonts",
    }
    assert of(findings, EntityType.URL) == {
        "https://example.org/": {
            "status_code": 200,
            "title": "Example & Co — Home",
            "content_type": "text/html",
        }
    }
    uses = {
        (f.source.value, f.target.value)
        for f in findings
        if isinstance(f, RelationshipFinding) and f.type.value == "USES"
    }
    assert (HOST, "wordpress") in uses and ("https://example.org/", "wordpress") in uses
    # Cookie values are never kept.
    assert "secretvalue" not in repr(findings)


async def test_same_host_redirect_and_query_dropped() -> None:
    findings, _, site = await run(
        {
            f"https://{HOST}/": httpx.Response(302, headers={"location": "/en/?ref=home"}),
            f"https://{HOST}/en/?ref=home": html_page('<title>Hi</title><div id="___gatsby">'),
        }
    )
    assert site.requests[-1] == f"https://{HOST}/en/?ref=home"
    assert set(of(findings, EntityType.URL)) == {"https://example.org/en/"}
    assert "gatsby" in of(findings, EntityType.TECHNOLOGY)


async def test_falls_back_to_http() -> None:
    def tls(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLCertVerificationError("bad cert")
        except ssl.SSLError as error:
            raise httpx.ConnectError("TLS", request=request) from error

    site = {
        f"https://{HOST}/": "tls",
        f"http://{HOST}/": html_page("<title>Plain</title>", server="Apache/2.4"),
    }

    class TlsSite(FakeSite):
        def handler(self, request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith("https://"):
                self.requests.append(str(request.url))
                return tls(request)
            return super().handler(request)

    fake = TlsSite(site)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
        ctx = fake_context(META)
        findings = await collect(
            WebTechShuttle(http=client, now=lambda: NOW), entity_input(D, HOST), ctx=ctx
        )
    assert "could not be verified" in ctx.notes[0]
    assert "apache" in of(findings, EntityType.TECHNOLOGY)
    assert set(of(findings, EntityType.URL)) == {"http://example.org/"}


async def test_failures() -> None:
    with pytest.raises(ShuttleFailedError) as raised:
        await run(
            {
                f"https://{HOST}/": httpx.ConnectError("down"),
                f"http://{HOST}/": httpx.ConnectError("down"),
            }
        )
    assert raised.value.reason == FailureReason.SOURCE_UNAVAILABLE
    findings, notes, _ = await run({f"https://{HOST}/": DestinationNotAllowedError("private")})
    assert findings == [] and "not public" in notes[0]
    findings, notes, _ = await run(
        {f"https://{HOST}/": httpx.Response(301, headers={"location": "https://x.example.net/"})}
    )
    assert "another host (x.example.net)" in notes[0]
    assert of(findings, EntityType.TECHNOLOGY) == {}


async def test_errors_and_non_html() -> None:
    findings, notes, _ = await run(
        {
            f"https://{HOST}/": httpx.Response(
                503, text="{}", headers={"content-type": "application/json", "cf-ray": "abc"}
            )
        }
    )
    assert "The home page answered 503." in notes
    assert any("not HTML (application/json)" in note for note in notes)
    assert set(of(findings, EntityType.TECHNOLOGY)) == {"cloudflare"}
    assert of(findings, D)[HOST] == {"web_checked_at": NOW, "analytics_ids": []}


def test_fingerprint_details() -> None:
    page = Page(
        url="https://example.org/",
        status=200,
        headers=httpx.Headers(
            [
                ("content-type", "text/html"),
                ("set-cookie", "laravel_session=abc"),
                ("set-cookie", "=broken"),
            ]
        ),
        body=(
            b'<meta content="Hugo 0.120" name="generator"><title>\x01</title><app ng-version="17">'
        ),
        truncated=False,
    )
    found = fingerprint(page)
    assert found.generator == "Hugo 0.120"
    assert found.title is None
    assert found.technologies == ["angular", "hugo", "laravel"]
    assert cookie_names(page.headers) == ["laravel_session"]
    latin = Page(
        "u",
        200,
        httpx.Headers({"content-type": "text/html; charset=latin-1"}),
        "é".encode("latin-1"),
        False,
    )
    assert latin.text() == "é"
    unknown = Page(
        "u", 200, httpx.Headers({"content-type": "text/html; charset=nope"}), b"ok", False
    )
    assert unknown.text() == "ok"
