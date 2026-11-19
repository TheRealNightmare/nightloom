"""The Wayback Shuttle's filter on its own: rows, scope, assets, privacy, merging, caps."""

from datetime import UTC, datetime

import pytest

from nightloom_shuttle_wayback.filtering import (
    DROP_LABELS,
    FIELDS,
    STATIC_ASSETS,
    STATIC_EXTENSIONS,
    ArchivedUrl,
    Capture,
    DropReason,
    Kept,
    classify,
    collect,
    has_email_in_path,
    in_scope,
    is_static_asset,
    parse_rows,
    parse_timestamp,
    query_param_names,
    select,
)

SCOPE = "example.org"
T0 = datetime(2020, 1, 1, tzinfo=UTC)


def capture(original: str, *, at: datetime = T0, mimetype: str | None = "text/html") -> Capture:
    return Capture(timestamp=at, original=original, status=200, mimetype=mimetype)


def kept(original: str, **options: object) -> Kept:
    result = classify(capture(original, **options), SCOPE)  # type: ignore[arg-type]
    assert isinstance(result, Kept), result
    return result


# --- rows -----------------------------------------------------------------------------------


def test_parse_rows() -> None:
    captures, malformed = parse_rows(
        [
            list(FIELDS),
            ["20200101120000", " http://example.org/ ", "200", "Text/HTML"],
            ["20200101120000", "http://example.org/a", "-", "unk"],
            ["20200101120000", "http://example.org/b", "999", "text/html"],
            ["2020010112000", "http://example.org/c", "200", "text/html"],  # 13 digits
            ["20200101120000", "x" * 5000, "200", "text/html"],
            ["20200101120000", "http://example.org/d", "200"],
            {"timestamp": "20200101120000"},
        ]
    )
    assert captures == [
        Capture(datetime(2020, 1, 1, 12, tzinfo=UTC), "http://example.org/", 200, "text/html"),
        Capture(datetime(2020, 1, 1, 12, tzinfo=UTC), "http://example.org/a", None, None),
        Capture(datetime(2020, 1, 1, 12, tzinfo=UTC), "http://example.org/b", None, "text/html"),
    ]
    assert malformed == 4


def test_parse_timestamp() -> None:
    assert parse_timestamp("19961231235959") == datetime(1996, 12, 31, 23, 59, 59, tzinfo=UTC)
    for bad in ("2020", "2020010112000a", "20201301000000", ""):
        with pytest.raises(ValueError):
            parse_timestamp(bad)


# --- scope ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "inside"),
    [
        ("example.org", True),
        ("www.example.org", True),
        ("a.b.example.org", True),
        ("evilexample.org", False),
        ("example.org.attacker.example", False),
        ("example.com", False),
    ],
)
def test_in_scope(name: str, inside: bool) -> None:
    assert in_scope(name, SCOPE) is inside


@pytest.mark.parametrize(
    ("original", "reason"),
    [
        ("http://evilexample.org/", DropReason.OUT_OF_SCOPE),
        ("http://192.0.2.1/", DropReason.OUT_OF_SCOPE),
        ("http://[2001:db8::1]/x", DropReason.OUT_OF_SCOPE),
        ("mailto:someone@example.org", DropReason.INVALID),
        ("example.org/no-scheme", DropReason.INVALID),
        ("http://exa mple.org/", DropReason.INVALID),
        ("http://example.org:99999/", DropReason.INVALID),
        ("http://[::1/", DropReason.INVALID),
        ("dns:example.org", DropReason.INVALID),
        ("gopher://example.org/", DropReason.INVALID),  # only http, https and ftp
        ("http://user:pw@example.org/", DropReason.INVALID),  # credentials
    ],
)
def test_classify_drops(original: str, reason: DropReason) -> None:
    assert classify(capture(original), SCOPE) is reason


# --- static assets --------------------------------------------------------------------------


def test_static_asset_list_is_one_constant() -> None:
    assert set(STATIC_ASSETS) == {"images", "stylesheets", "scripts", "fonts", "media", "archives"}
    assert {".png", ".css", ".js", ".woff2", ".mp4", ".zip"} <= STATIC_EXTENSIONS
    assert ".pdf" not in STATIC_EXTENSIONS and ".html" not in STATIC_EXTENSIONS


@pytest.mark.parametrize(
    ("path", "mimetype", "static"),
    [
        ("/logo.PNG", None, True),
        ("/a/b.min.js", "text/html", True),
        ("/style.css;v=2", None, False),  # the caller strips path parameters first
        ("/font", "font/woff2", True),
        ("/x", "image/svg+xml", True),
        ("/x", "video/mp4", True),
        ("/x", "application/x-gzip", True),
        ("/x", "application/javascript", True),
        ("/report.pdf", "application/pdf", False),
        ("/index.html", "text/html", False),
        ("/", None, False),
        ("/v1.2/docs", "text/html", False),  # a dot in a directory, not an extension
        ("/x", "warc/revisit", False),
    ],
)
def test_is_static_asset(path: str, mimetype: str | None, static: bool) -> None:
    assert is_static_asset(path, mimetype) is static


def test_static_assets_are_dropped_by_extension_or_mimetype() -> None:
    assert classify(capture("http://example.org/style.css;v=2"), SCOPE) is DropReason.STATIC_ASSET
    assert classify(capture("http://example.org/a.js?v=3"), SCOPE) is DropReason.STATIC_ASSET
    assert (
        classify(capture("http://example.org/i", mimetype="image/png"), SCOPE)
        is DropReason.STATIC_ASSET
    )
    # Scope comes first.
    assert classify(capture("http://other.example/a.png"), SCOPE) is DropReason.OUT_OF_SCOPE


# --- privacy --------------------------------------------------------------------------------


def test_query_and_fragment_are_removed_and_names_kept() -> None:
    result = kept("http://Example.org:80/login?token=SECRET&email=a@b.example&next=%2F#frag")
    assert result == Kept(
        url="http://example.org/login",
        host="example.org",
        query_params=frozenset({"email", "next", "token"}),
        stripped=True,
    )
    assert kept("https://www.example.org/").stripped is False
    assert kept("https://www.example.org/#top").stripped is True


def test_path_parameters_are_removed() -> None:
    result = kept("http://example.org/cart;jsessionid=ABC123/view;v=1")
    assert result.url == "http://example.org/cart/view"
    assert result.stripped is True
    assert result.query_params == frozenset()


@pytest.mark.parametrize(
    ("query", "names"),
    [
        ("", set()),
        ("a=1&b=2&a=3", {"a", "b"}),
        ("a=1;b=2", {"a", "b"}),
        ("utm_source=x&page%5B%5D=2&ids[]=1", {"utm_source", "page[]", "ids[]"}),
        ("q+x=1", set()),  # a space in the name
        ("3f9a8c7e6d5b4a39", set()),  # a bare item may be a value itself
        ("a%40b.example=1", set()),  # "@" in the name
        ("=1&=2", set()),
        (f"{'n' * 64}=1&{'m' * 65}=1", {"n" * 64}),
        ("a/b=1&c:d=2", set()),
    ],
)
def test_query_param_names(query: str, names: set[str]) -> None:
    assert query_param_names(query) == names


@pytest.mark.parametrize(
    ("path", "email"),
    [
        ("/users/alice@example.net", True),
        ("/u/alice%40example.net", True),
        ("/u/alice%2540example.net", True),  # encoded twice
        ("/u/alice%2540", True),
        ("/u/alice", False),
        ("/u/40", False),
    ],
)
def test_has_email_in_path(path: str, email: bool) -> None:
    assert has_email_in_path(path) is email


def test_email_in_path_is_dropped_after_scope_and_assets() -> None:
    assert classify(capture("http://example.org/a@b.example"), SCOPE) is DropReason.EMAIL_IN_PATH
    assert classify(capture("http://example.org/a%40b"), SCOPE) is DropReason.EMAIL_IN_PATH
    # An email address only in the query is not a path: the query is removed.
    assert kept("http://example.org/contact?to=a@b.example").url == "http://example.org/contact"
    assert classify(capture("http://example.org/a@b.png"), SCOPE) is DropReason.STATIC_ASSET


def test_drop_labels_cover_every_reason_in_rule_order() -> None:
    assert list(DROP_LABELS) == [
        DropReason.OUT_OF_SCOPE,
        DropReason.STATIC_ASSET,
        DropReason.EMAIL_IN_PATH,
        DropReason.INVALID,
    ]
    assert set(DROP_LABELS) == set(DropReason)


# --- merging and caps -----------------------------------------------------------------------


def test_collect_merges_variants() -> None:
    later = datetime(2022, 6, 1, tzinfo=UTC)
    earlier = datetime(2010, 6, 1, tzinfo=UTC)
    collected = collect(
        [
            Capture(T0, "http://example.org/p?a=1", 200, "text/html"),
            Capture(later, "http://example.org:80/p?b=2", 404, None),
            Capture(earlier, "http://example.org/p#x", None, "text/plain"),
            Capture(T0, "https://example.org/p", 200, "text/html"),  # another scheme
            Capture(later, "http://example.org/p.css", 200, "text/css"),
        ],
        SCOPE,
    )
    assert set(collected.urls) == {"http://example.org/p", "https://example.org/p"}
    merged = collected.urls["http://example.org/p"]
    assert (merged.first, merged.latest, merged.count) == (earlier, later, 3)
    assert (merged.status, merged.mimetype) == (404, "text/html")  # latest; mimetype kept
    assert merged.sorted_params() == ["a", "b"]
    assert collected.stripped == 3
    assert collected.dropped == {DropReason.STATIC_ASSET: 1}
    assert (collected.first, collected.last) == (earlier, later)  # assets count as captures


def test_uncounted_captures_add_no_address() -> None:
    collected = collect([Capture(T0, "http://example.org/", 200, "text/html")], SCOPE)
    later = datetime(2026, 1, 1, tzinfo=UTC)
    entry = collected.add(Capture(later, "http://example.org/", 301, None), SCOPE, counted=False)
    assert entry is not None and entry.count == 1 and entry.latest == later
    assert collected.add(Capture(later, "http://evil.example/", 200, None), SCOPE) is None
    assert collected.add(capture("http://evil.example/"), SCOPE, counted=False) is None
    assert collected.dropped == {DropReason.OUT_OF_SCOPE: 1}


def test_sorted_params_are_capped() -> None:
    entry = ArchivedUrl("http://example.org/", "example.org", T0, T0, None, None)
    entry.query_params = {f"p{i:02d}" for i in range(40)}
    assert entry.sorted_params() == [f"p{i:02d}" for i in range(30)]


def entry(url: str, host: str, minute: int) -> ArchivedUrl:
    at = T0.replace(minute=minute)
    return ArchivedUrl(url, host, at, at, 200, "text/html", count=1)


def test_select_orders_by_latest_capture_and_caps() -> None:
    urls = [
        entry("http://example.org/old", "example.org", 1),
        entry("http://example.org/new", "example.org", 9),
        entry("http://a.example.org/", "a.example.org", 5),
        entry("http://b.example.org/", "b.example.org", 7),
        entry("http://b.example.org/2", "b.example.org", 3),
        entry("http://c.example.org/", "c.example.org", 2),
    ]
    selection = select(urls, SCOPE, max_urls=10, max_subdomains=2)
    assert [u.url for u in selection.urls] == [
        "http://example.org/new",
        "http://b.example.org/",
        "http://a.example.org/",
        "http://b.example.org/2",
        "http://example.org/old",
    ]
    assert selection.subdomains == ["a.example.org", "b.example.org"]
    assert (selection.over_url_cap, selection.over_subdomain_cap) == (0, 1)

    selection = select(urls, SCOPE, max_urls=2, max_subdomains=10)
    assert [u.url for u in selection.urls] == ["http://example.org/new", "http://b.example.org/"]
    assert selection.subdomains == ["b.example.org"]
    assert selection.over_url_cap == 4
