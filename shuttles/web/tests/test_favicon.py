"""The favicon Shuttle against a fake site, and the hash it computes: no network."""

import base64
import hashlib
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
from nightloom_shuttle_web import FaviconShuttle
from nightloom_shuttle_web.favicon import MAX_ICON_BYTES, decode_data_url, icon_links
from nightloom_shuttle_web.murmur import favicon_hash, murmur3_32

D = EntityType.DOMAIN
F = EntityType.FAVICON
META = FaviconShuttle.meta
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
HOST = "example.org"
ICON = bytes(range(256)) * 4
# mmh3.hash(codecs.encode(ICON, "base64")), from the reference implementation.
ICON_HASH = -1082603952


# --- the hash ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", 0),
        (b"foo", -156908512),
        (b"Hello, world!", -1070186941),
        (b"The quick brown fox jumps over the lazy dog", 776992547),
    ],
)
def test_murmur3_matches_the_reference(data: bytes, expected: int) -> None:
    assert murmur3_32(data) == expected


def test_the_favicon_hash_is_shodans() -> None:
    assert favicon_hash(ICON) == ICON_HASH
    # It hashes the base64 text with its line breaks, not the raw file.
    assert favicon_hash(ICON) == murmur3_32(base64.encodebytes(ICON))
    assert favicon_hash(ICON) != murmur3_32(ICON)


# --- finding the icon --------------------------------------------------------------------


def test_icon_links_prefers_icon_over_apple_touch_icon() -> None:
    page = """<html><head>
    <link rel="apple-touch-icon" href="/apple.png">
    <link rel="stylesheet" href="/site.css">
    <LINK REL="Shortcut Icon" HREF='/static/fav.ico?v=2'>
    <link rel="icon" href="/static/fav.ico?v=2">
    <link rel=icon href=/second.png>
    </head><body><link rel="icon" href="/in-body.png"></body></html>"""
    assert icon_links(page) == ["/static/fav.ico?v=2", "/second.png", "/apple.png"]


def test_icon_links_unescapes_and_skips_empty() -> None:
    page = '<link rel="icon" href=""><link rel="icon" href="/a.ico?x=1&amp;y=2">'
    assert icon_links(page) == ["/a.ico?x=1&y=2"]


def test_decode_data_url() -> None:
    encoded = base64.b64encode(ICON).decode()
    icon = decode_data_url(f"data:image/png;base64,{encoded}")
    assert icon is not None
    assert icon.body == ICON
    assert icon.mime_type == "image/png"
    svg = decode_data_url("data:image/svg+xml,%3Csvg%3E%3C/svg%3E")
    assert svg is not None
    assert svg.body == b"<svg></svg>"
    assert decode_data_url("data:text/html,<b>") is None
    assert decode_data_url("data:image/png;base64") is None
    assert decode_data_url("data:image/png,") is None
    too_big = base64.b64encode(b"x" * (MAX_ICON_BYTES + 1)).decode()
    assert decode_data_url(f"data:image/png;base64,{too_big}") is None


# --- the Shuttle -------------------------------------------------------------------------


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


def page(head: str = "") -> httpx.Response:
    body = f"<!doctype html><html><head><title>x</title>{head}</head><body></body></html>"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/html"})


def icon(body: bytes = ICON, kind: str = "image/x-icon") -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": kind})


async def run(replies: dict[str, Any]) -> tuple[list[Finding], tuple[str, ...], FakeSite]:
    site = FakeSite(replies)
    async with httpx.AsyncClient(transport=httpx.MockTransport(site.handler)) as client:
        ctx = fake_context(META)
        entity = entity_input(D, HOST)
        findings = await collect(FaviconShuttle(http=client, now=lambda: NOW), entity, ctx=ctx)
        assert_declared(META, findings, entity)
        for finding in findings:
            if isinstance(finding, EntityFinding):
                build_entity(finding.type, finding.value, finding.properties)
    return findings, ctx.notes, site


def favicons(findings: list[Finding]) -> dict[str, dict[str, Any]]:
    return {
        f.value: f.properties or {}
        for f in findings
        if isinstance(f, EntityFinding) and f.type == F
    }


def test_meta_visits_the_site() -> None:
    assert META.name == "favicon"
    assert META.mode == Mode.VISIT
    assert META.input_types == {D}
    assert META.cache_ttl_seconds == 24 * 60 * 60
    assert META.default_admiralty == "A2"


async def test_the_declared_icon_is_hashed() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/": page('<link rel="icon" href="/static/icon.png?v=3">'),
            f"https://{HOST}/static/icon.png?v=3": icon(kind="image/png"),
        }
    )
    assert site.requests == [f"https://{HOST}/", f"https://{HOST}/static/icon.png?v=3"]
    assert favicons(findings) == {
        str(ICON_HASH): {
            "sha256": hashlib.sha256(ICON).hexdigest(),
            "md5": hashlib.md5(ICON).hexdigest(),  # noqa: S324 - an identifier, not security
            "size_bytes": len(ICON),
            "mime_type": "image/png",
        }
    }
    domain = next(f for f in findings if isinstance(f, EntityFinding) and f.type == D)
    assert domain.value == HOST
    assert domain.properties == {"favicon_checked_at": NOW}
    links = [f for f in findings if isinstance(f, RelationshipFinding)]
    assert [(r.type, r.source.value, r.target.type, r.target.value) for r in links] == [
        ("HAS_FAVICON", HOST, F, str(ICON_HASH))
    ]
    assert "Hashed the icon at /static/icon.png." in notes


async def test_favicon_ico_when_the_page_declares_none() -> None:
    findings, notes, site = await run(
        {f"https://{HOST}/": page(), f"https://{HOST}/favicon.ico": icon()}
    )
    assert site.requests == [f"https://{HOST}/", f"https://{HOST}/favicon.ico"]
    assert list(favicons(findings)) == [str(ICON_HASH)]
    assert "Hashed the icon at /favicon.ico." in notes


async def test_favicon_ico_when_the_declared_icon_fails() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/": page('<link rel="icon" href="/missing.png">'),
            f"https://{HOST}/favicon.ico": icon(),
        }
    )
    assert len(site.requests) == 3
    assert list(favicons(findings)) == [str(ICON_HASH)]
    assert "The icon at /missing.png could not be read (status 404)." in notes


async def test_an_icon_on_another_host_is_not_fetched() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/": page('<link rel="icon" href="https://cdn.example.net/i.ico">'),
            f"https://{HOST}/favicon.ico": icon(),
        }
    )
    assert "https://cdn.example.net/i.ico" not in site.requests
    assert "The page's icon is on another host (cdn.example.net); it was not fetched." in notes
    assert list(favicons(findings)) == [str(ICON_HASH)]


async def test_an_embedded_icon_needs_no_request() -> None:
    encoded = base64.b64encode(ICON).decode()
    findings, _, site = await run(
        {f"https://{HOST}/": page(f'<link rel="icon" href="data:image/png;base64,{encoded}">')}
    )
    assert site.requests == [f"https://{HOST}/"]
    assert list(favicons(findings)) == [str(ICON_HASH)]


async def test_a_catch_all_html_page_is_not_an_icon() -> None:
    findings, notes, _ = await run(
        {
            f"https://{HOST}/": page(),
            f"https://{HOST}/favicon.ico": httpx.Response(
                200, content=b"<html>Not found</html>", headers={"content-type": "text/html"}
            ),
        }
    )
    assert favicons(findings) == {}
    assert "The icon at /favicon.ico could not be read (not an image)." in notes
    assert "The site has no favicon Nightloom could read." in notes
    # The domain still records that it was checked.
    domain = next(f for f in findings if isinstance(f, EntityFinding))
    assert domain.properties == {"favicon_checked_at": NOW}


async def test_an_oversized_icon_is_not_hashed() -> None:
    findings, notes, _ = await run(
        {
            f"https://{HOST}/": page(),
            f"https://{HOST}/favicon.ico": icon(b"x" * (MAX_ICON_BYTES + 10)),
        }
    )
    assert favicons(findings) == {}
    assert "The icon at /favicon.ico could not be read (larger than 256 KB)." in notes


async def test_plain_http_when_https_does_not_answer() -> None:
    findings, notes, site = await run(
        {
            f"https://{HOST}/": httpx.ConnectError("refused"),
            f"http://{HOST}/": page(),
            f"http://{HOST}/favicon.ico": icon(),
        }
    )
    assert site.requests[-1] == f"http://{HOST}/favicon.ico"
    assert list(favicons(findings)) == [str(ICON_HASH)]
    assert f"{HOST} did not answer over HTTPS; the plain HTTP site was read." in notes


async def test_nothing_answers() -> None:
    with pytest.raises(ShuttleFailedError) as info:
        await run(
            {
                f"https://{HOST}/": httpx.ConnectError("refused"),
                f"http://{HOST}/": httpx.ConnectError("refused"),
            }
        )
    assert info.value.reason == FailureReason.SOURCE_UNAVAILABLE


async def test_a_private_address_is_never_contacted() -> None:
    findings, notes, _ = await run({f"https://{HOST}/": DestinationNotAllowedError("private")})
    assert findings == []
    assert any("not public" in note for note in notes)
