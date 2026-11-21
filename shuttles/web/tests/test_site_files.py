"""The site files Shuttle against a fake site: no network."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from nightloom_core.entities import EntityType, build_entity
from nightloom_sdk import EntityFinding, FailureReason, Finding, Mode, ShuttleFailedError
from nightloom_sdk.http import DestinationNotAllowedError
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_web import SiteFilesShuttle
from nightloom_shuttle_web.site_files import (
    MAX_URLS,
    parse_robots,
    parse_security_txt,
    sitemap_locations,
)

D = EntityType.DOMAIN
META = SiteFilesShuttle.meta
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
HOST = "example.org"

ROBOTS = """
# Robots
User-agent: *
Disallow: /admin/
Disallow: /private/*.pdf
Disallow: /cart?session=abc
Allow: /admin/help
Disallow:
Sitemap: https://example.org/sitemaps/main.xml
Sitemap: https://cdn.example.net/other.xml
"""

SECURITY = """# Our policy
Contact: mailto:security@example.org
Contact: https://example.org/security
Expires: 2027-01-01T00:00:00.000Z
Policy: https://example.org/disclosure
"""

SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.org/</loc></url>
  <url><loc>https://example.org/about?utm=1&amp;x=2</loc></url>
  <url><loc>https://shop.example.org/products</loc></url>
  <url><loc>https://elsewhere.test/page</loc></url>
  <url><loc>https://example.org/logo.png</loc></url>
  <url><loc>https://example.org/users/alex@example.org</loc></url>
</urlset>
"""


class FakeSite:
    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        reply = self.replies.get(url)
        if reply is None:
            return httpx.Response(404, text="not found")
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, text=reply, headers={"content-type": "text/plain"})


async def run(replies: dict[str, Any]) -> tuple[list[Finding], tuple[str, ...], FakeSite]:
    site = FakeSite(replies)
    async with httpx.AsyncClient(transport=httpx.MockTransport(site.handler)) as client:
        ctx = fake_context(META)
        entity = entity_input(D, HOST)
        findings = await collect(SiteFilesShuttle(http=client, now=lambda: NOW), entity, ctx=ctx)
        assert_declared(META, findings, entity)
        for finding in findings:
            if isinstance(finding, EntityFinding):
                build_entity(finding.type, finding.value, finding.properties)
    return findings, ctx.notes, site


def urls(findings: list[Finding]) -> list[str]:
    return [f.value for f in findings if isinstance(f, EntityFinding) and f.type == EntityType.URL]


def domain(findings: list[Finding]) -> dict[str, Any]:
    [found] = [f for f in findings if isinstance(f, EntityFinding) and f.type == D]
    return found.properties or {}


def test_meta() -> None:
    assert META.mode == Mode.VISIT
    assert META.cache_ttl_seconds == 24 * 3600


async def test_all_three_files() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/robots.txt": ROBOTS,
            f"https://{HOST}/.well-known/security.txt": SECURITY,
            f"https://{HOST}/sitemaps/main.xml": httpx.Response(
                200, text=SITEMAP, headers={"content-type": "application/xml"}
            ),
        }
    )
    assert site.requests == [
        f"https://{HOST}/robots.txt",
        f"https://{HOST}/.well-known/security.txt",
        f"https://{HOST}/sitemaps/main.xml",
    ]
    assert domain(findings) == {
        "site_files_checked_at": NOW,
        "robots_txt": True,
        "robots_disallow_count": 3,
        "security_txt": True,
        "security_txt_contact_kinds": ["email", "url"],
        "security_txt_expires": datetime(2027, 1, 1, tzinfo=UTC),
        "sitemap_url_count": 6,
    }
    assert urls(findings) == [
        "https://example.org/admin/",
        "https://example.org/cart",
        "https://example.org/admin/help",
        "https://example.org/",
        "https://example.org/about",
        "https://shop.example.org/products",
    ]
    # The contacts themselves are never recorded.
    assert "security@example.org" not in repr(findings)
    assert any("Left out 3 addresses" in note for note in notes)


async def test_fallbacks_and_missing_files() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/security.txt": "Contact: tel:+1-201-555-0123\n",
            f"https://{HOST}/sitemap.xml": "<sitemapindex><sitemap><loc>https://example.org/a.xml"
            "</loc></sitemap></sitemapindex>",
        }
    )
    assert site.requests == [
        f"https://{HOST}/robots.txt",
        f"https://{HOST}/.well-known/security.txt",
        f"https://{HOST}/security.txt",
        f"https://{HOST}/sitemap.xml",
    ]
    assert domain(findings) == {
        "site_files_checked_at": NOW,
        "robots_txt": False,
        "security_txt": True,
        "security_txt_contact_kinds": ["phone"],
        "sitemap_url_count": 0,
    }
    assert "No robots.txt." in notes
    assert "The sitemap is an index of 1 sitemaps; they were not fetched." in notes


async def test_nothing_there() -> None:
    findings, notes, _ = await run({})
    assert domain(findings) == {
        "site_files_checked_at": NOW,
        "robots_txt": False,
        "security_txt": False,
    }
    assert {"No robots.txt.", "No security.txt.", "No sitemap."} <= set(notes)


async def test_redirects_to_another_host_are_not_followed() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/robots.txt": httpx.Response(
                301, headers={"location": "https://www.example.org/robots.txt"}
            ),
            f"https://{HOST}/sitemap.xml": httpx.Response(302, headers={"location": "/map.xml"}),
            f"https://{HOST}/map.xml": "<urlset><url><loc>https://example.org/x</loc></url></urlset>",
        }
    )
    assert "https://www.example.org/robots.txt" not in site.requests
    assert "robots.txt redirects to another host (www.example.org); it was not followed." in notes
    assert urls(findings) == ["https://example.org/x"]


async def test_unreachable_and_not_public() -> None:
    with pytest.raises(ShuttleFailedError) as raised:
        await run({f"https://{HOST}/robots.txt": httpx.ConnectError("down")})
    assert raised.value.reason == FailureReason.SOURCE_UNAVAILABLE
    findings, notes, site = await run(
        {f"https://{HOST}/robots.txt": DestinationNotAllowedError("private")}
    )
    assert findings == []
    assert len(site.requests) == 1
    assert "not public" in notes[0]


async def test_url_cap_and_truncation() -> None:
    many = "".join(f"<url><loc>https://example.org/p{i}</loc></url>" for i in range(150))
    findings, _, _ = await run({f"https://{HOST}/sitemap.xml": f"<urlset>{many}</urlset>"})
    assert len(urls(findings)) == MAX_URLS
    huge = "Disallow: /a\n" * 60_000
    _, notes, _ = await run({f"https://{HOST}/robots.txt": huge})
    assert any("only the start was read" in note for note in notes)


def test_parsers() -> None:
    robots = parse_robots(ROBOTS)
    assert robots.paths == ["/admin/", "/cart?session=abc", "/admin/help"]
    assert robots.disallow_count == 3
    assert robots.sitemaps == [
        "https://example.org/sitemaps/main.xml",
        "https://cdn.example.net/other.xml",
    ]
    empty = parse_security_txt("Expires: soon\nPolicy: x\n")
    assert (empty.valid, empty.expires, empty.contact_kinds) == (False, None, [])
    naive = parse_security_txt("Contact: security@example.org\nExpires: 2027-01-01T00:00:00\n")
    assert (naive.contact_kinds, naive.expires) == (["email"], None)
    pages, nested = sitemap_locations(SITEMAP)
    assert nested == 0
    assert pages[1] == "https://example.org/about?utm=1&x=2"
