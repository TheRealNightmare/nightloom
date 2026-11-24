"""The Wikidata Shuttle against a fake query service: no network.

The fake answers each of the Shuttle's queries (recognized by their text)
with SPARQL JSON results about fictional items: Example Holdings (Q900001),
its parent Example Group (Q900002), its subsidiaries, two unrelated items
called Example Trading, and a person, Pat Example (Q900009).
"""

import asyncio
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from nightloom_core.entities import EntityType, Sensitivity, build_entity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    FailureReason,
    Finding,
    JobCancelledError,
    Mode,
    RelationshipFinding,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.http import create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_wikidata import WikidataShuttle, queries
from nightloom_shuttle_wikidata.client import ACCEPT, ENDPOINT, HOST
from nightloom_shuttle_wikidata.shuttle import (
    MAX_SUBSIDIARIES,
    NOTE_BAD_ID,
    NOTE_BY_ID,
    NOTE_CAPPED,
    NOTE_NO_MATCH,
    NOTE_NO_WEBSITE,
    NOTE_NOT_ORGANIZATION,
    NOTE_PERSON,
    NOTE_SKIPPED,
    PARENT_NOTE,
    USAGE_NOTE,
    WEBSITE_NOTE,
    is_valid_lei,
    item_of,
)

ORG = EntityType.ORGANIZATION
DOMAIN = EntityType.DOMAIN
R = RelationType
META = WikidataShuttle.meta
NOW = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)
ENTITY = queries.ENTITY_PREFIX
LEI = "5493000EXHOLD0000105"


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": f"{ENTITY}{qid}"}


def text(value: str) -> dict[str, str]:
    return {"type": "literal", "xml:lang": "en", "value": value}


def boolean(value: bool) -> dict[str, str]:
    return {
        "type": "literal",
        "datatype": "http://www.w3.org/2001/XMLSchema#boolean",
        "value": "true" if value else "false",
    }


def results(*rows: dict[str, Any]) -> dict[str, Any]:
    variables = sorted({name for row in rows for name in row})
    return {"head": {"vars": variables}, "results": {"bindings": list(rows)}}


def kind_row(*, human: bool = False, organization: bool = True, **extra: Any) -> dict[str, Any]:
    return {"human": boolean(human), "organization": boolean(organization), **extra}


HOLDINGS_DETAILS = results(
    kind_row(
        label=text("Example Holdings"),
        site={"type": "uri", "value": "https://www.example.com/en/"},
        country=text("GB"),
        lei=text(LEI),
        industry=text("financial services"),
    ),
    kind_row(
        label=text("Example Holdings"),
        site={"type": "uri", "value": "http://example.net"},
        country=text("GB"),
        lei=text(LEI),
        industry=text("Banking"),
    ),
    kind_row(label=text("Example Holdings"), site={"type": "uri", "value": "ftp://files.example"}),
)
HOLDINGS_RELATIVES = results(
    {"relation": text("parent"), "other": uri("Q900002"), "label": text("Example Group")},
    {"relation": text("subsidiary"), "other": uri("Q900003"), "label": text("Example Bank")},
    {"relation": text("subsidiary"), "other": uri("Q900004"), "label": text("Example Insurance")},
    # Stated on both items: one subsidiary.
    {"relation": text("subsidiary"), "other": uri("Q900004"), "label": text("Example Insurance")},
    # The same name as the organization: never linked to itself.
    {"relation": text("subsidiary"), "other": uri("Q900005"), "label": text("Example Holdings")},
)

Reply = dict[str, Any] | bytes | httpx.Response


def default_routes() -> dict[str, Reply]:
    return {
        "label:Example Holdings": results({"item": uri("Q900001")}),
        "label:Example Trading": results({"item": uri("Q900007")}, {"item": uri("Q900006")}),
        "details:Q900001": HOLDINGS_DETAILS,
        "relatives:Q900001": HOLDINGS_RELATIVES,
        "details:Q900009": results(kind_row(human=True, organization=False, label=text("Pat"))),
        "details:Q900010": results(kind_row(organization=False, label=text("Example River"))),
        "website:example.com": results(
            {"item": uri("Q900001"), "label": text("Example Holdings")},
            {"item": uri("Q900002"), "label": text("Example Group")},
        ),
        f"lei:{LEI}": results({"item": uri("Q900001")}),
    }


class FakeTime:
    """Fake monotonic time: sleeping (backoff, rate limit, Retry-After) moves it forward."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def route(query: str) -> str:
    """Which of the Shuttle's queries this is, and its argument."""
    if "skos:altLabel" in query:
        match = re.search(r'rdfs:label "((?:[^"\\]|\\.)*)"@en', query)
        assert match is not None
        return "label:" + match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    if 'wdt:P1278 "' in query:
        match = re.search(r'wdt:P1278 "([A-Z0-9]{20})"', query)
        assert match is not None
        return f"lei:{match.group(1)}"
    if "VALUES ?site" in query:
        match = re.search(r"<https://([^/>]+)/>", query)
        assert match is not None
        return f"website:{match.group(1)}"
    match = re.search(r"wd:(Q[0-9]+)", query.replace("wd:Q43229", "").replace("wd:Q5 ", ""))
    assert match is not None
    if "BIND(EXISTS" in query:
        return f"details:{match.group(1)}"
    assert "wdt:P749" in query
    return f"relatives:{match.group(1)}"


class FakeWikidata:
    """Answers from ``routes`` by :func:`route`; a list replies in turn; else no results."""

    def __init__(self, routes: dict[str, Reply | list[Reply]] | None = None) -> None:
        self.routes: dict[str, Reply | list[Reply]] = dict(default_routes())
        self.routes.update(routes or {})
        self.requests: list[httpx.Request] = []

    @property
    def calls(self) -> list[str]:
        return [route(request.url.params["query"]) for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert str(request.url).startswith(ENDPOINT + "?")
        assert request.url.host == HOST
        assert request.headers["Accept"] == ACCEPT
        assert request.headers["User-Agent"].startswith("Nightloom/")
        assert request.url.params["format"] == "json"
        reply = self.routes.get(route(request.url.params["query"]), results())
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)


async def run_shuttle(
    fake: FakeWikidata,
    entity: EntityInput,
    *,
    time: FakeTime | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[list[Finding], ShuttleContext, FakeTime]:
    time = time or FakeTime()
    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=time, sleep=time.sleep)
    http = create_http_client(META, transport=httpx.MockTransport(fake.handler), limiter=limiter)
    ctx = ShuttleContext(META, http=http, cancel_event=cancel_event)
    shuttle = WikidataShuttle(sleep=time.sleep, clock=time, now=lambda: NOW)
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, found, entity)
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    assert "Pat" not in repr(found) + repr(ctx.notes)
    return found, ctx, time


def organization(value: str, **properties: Any) -> EntityInput:
    entity = entity_input(ORG, value)
    return entity.model_copy(update={"properties": properties}) if properties else entity


def entities(found: list[Finding], entity_type: EntityType) -> dict[str, dict[str, Any]]:
    return {
        f.value: dict(f.properties or {})
        for f in found
        if isinstance(f, EntityFinding) and f.type == entity_type
    }


def edges(found: Iterable[Finding], relation: RelationType) -> set[tuple[str, str, str | None]]:
    return {
        (f.source.value, f.target.value, f.note)
        for f in found
        if isinstance(f, RelationshipFinding) and f.type == relation
    }


HOLDINGS = {
    "wikidata_id": "Q900001",
    "country": "GB",
    "lei": LEI,
    "industry": "Banking; financial services",
    "wikidata_checked_at": NOW,
}


# --- metadata and the queries -------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "wikidata"
    assert META.title == "Organization facts (Wikidata)"
    assert META.input_types == {ORG, DOMAIN}
    assert META.output_entity_types == {ORG, DOMAIN}
    assert META.output_relationship_types == {R.OWNS, R.SUBSIDIARY_OF}
    assert (META.mode, META.network, META.pivots) == (Mode.PASSIVE, True, False)
    assert META.sensitivity is Sensitivity.ORGANIZATION
    assert META.rate_limit is not None and META.rate_limit.per_second == pytest.approx(1)
    assert META.cache_ttl_seconds == 7 * 24 * 3600
    assert (META.default_admiralty, META.default_confidence) == ("C2", 0.7)
    assert META.usage_note == USAGE_NOTE
    assert META.required_keys == frozenset()
    registry = ShuttleRegistry.discover()
    assert registry.get("wikidata") is WikidataShuttle
    assert WikidataShuttle in registry.list(ORG)
    assert WikidataShuttle in registry.list(DOMAIN)
    WikidataShuttle.check_configuration()


def test_queries_escape_and_check_their_values() -> None:
    query = queries.by_label('Evil" } ; DROP ?x \\ \n')
    assert '"Evil\\" } ; DROP ?x \\\\ \\n"@en' in query
    assert query.count('"@en') == 2
    with pytest.raises(ValueError):
        queries.details("Q1 } UNION { ?x ?y ?z")
    with pytest.raises(ValueError):
        queries.relatives("P31")
    with pytest.raises(ValueError):
        queries.by_lei('5493000EXHOLD0000105" }')
    with pytest.raises(ValueError):
        queries.by_website("example.org> } <x")
    # Every query that returns items to keep excludes people.
    for query in (
        queries.by_label("x"),
        queries.by_lei(LEI),
        queries.relatives("Q1"),
        queries.by_website("example.org"),
    ):
        assert "FILTER NOT EXISTS {" in query and "wdt:P31 wd:Q5 }" in query
    assert queries.website_variants("www.example.org") == [
        "https://www.example.org/",
        "https://www.example.org",
        "http://www.example.org/",
        "http://www.example.org",
    ]
    assert len(queries.website_variants("example.org")) == 8
    assert queries.qid_of(f"{ENTITY}Q95") == "Q95"
    assert queries.qid_of(f"{ENTITY}P31") is None
    assert queries.qid_of("https://example.org/Q95") is None
    assert item_of(" q95 ") == "Q95" and item_of("Q0") is None and item_of("Apple") is None
    assert is_valid_lei(LEI) and not is_valid_lei("5493000EXHOLD0000106")


# --- organizations ------------------------------------------------------------------------


async def test_organization_by_name() -> None:
    fake = FakeWikidata()
    found, ctx, _ = await run_shuttle(fake, organization("Example Holdings"))
    assert fake.calls == ["label:Example Holdings", "details:Q900001", "relatives:Q900001"]

    orgs = entities(found, ORG)
    assert orgs["Example Holdings"] == HOLDINGS
    assert orgs["Example Group"] == {"wikidata_id": "Q900002", "wikidata_checked_at": NOW}
    assert set(orgs) == {"Example Holdings", "Example Group", "Example Bank", "Example Insurance"}
    # Official websites as registrable domains; the ftp one is not a website.
    assert set(entities(found, DOMAIN)) == {"example.com", "example.net"}
    assert edges(found, R.OWNS) == {
        ("Example Holdings", "example.com", WEBSITE_NOTE),
        ("Example Holdings", "example.net", WEBSITE_NOTE),
    }
    assert edges(found, R.SUBSIDIARY_OF) == {
        ("Example Holdings", "Example Group", PARENT_NOTE),
        ("Example Bank", "Example Holdings", PARENT_NOTE),
        ("Example Insurance", "Example Holdings", PARENT_NOTE),
    }
    for finding in found:
        assert (finding.admiralty, finding.confidence) == (None, None)  # C2/0.7 by default
    assert NOTE_SKIPPED.format(count=1) in ctx.notes  # the ftp address


async def test_organization_by_property_lei_and_id_as_name() -> None:
    fake = FakeWikidata()
    found, _, _ = await run_shuttle(fake, organization("Holdings", wikidata_id="q900001"))
    assert fake.calls[0] == "details:Q900001"
    assert entities(found, ORG)["Holdings"]["wikidata_id"] == "Q900001"

    fake = FakeWikidata()
    found, _, _ = await run_shuttle(fake, organization("EXAMPLE HOLDINGS LIMITED", lei=LEI))
    assert fake.calls[0] == f"lei:{LEI}"
    assert entities(found, ORG)["EXAMPLE HOLDINGS LIMITED"]["wikidata_id"] == "Q900001"

    fake = FakeWikidata()
    found, ctx, _ = await run_shuttle(fake, organization("Q900001"))
    orgs = entities(found, ORG)
    assert orgs["Q900001"] == {"wikidata_id": "Q900001"}
    assert orgs["Example Holdings"] == HOLDINGS
    assert all("Q900001" not in edge[:2] for edge in edges(found, R.SUBSIDIARY_OF))
    assert NOTE_BY_ID.format(qid="Q900001", name="Example Holdings") in ctx.notes


async def test_invalid_id_property_falls_back() -> None:
    fake = FakeWikidata()
    found, ctx, _ = await run_shuttle(fake, organization("Example Holdings", wikidata_id="P31"))
    assert NOTE_BAD_ID in ctx.notes
    assert fake.calls[0] == "label:Example Holdings"
    assert entities(found, ORG)["Example Holdings"]["wikidata_id"] == "Q900001"


async def test_no_match_and_several_matches() -> None:
    found, ctx, _ = await run_shuttle(FakeWikidata(), organization("Nobody Ltd"))
    assert (found, ctx.notes) == ([], (NOTE_NO_MATCH,))
    fake = FakeWikidata()
    found, ctx, _ = await run_shuttle(fake, organization("Example Trading"))
    assert found == []
    assert fake.calls == ["label:Example Trading"]
    assert ctx.notes == (
        "2 Wikidata organizations have this name, so nothing was added: Q900006, Q900007. "
        "Add the item ID you mean as an organization and run this Shuttle on it.",
    )


async def test_people_and_other_items_are_never_kept() -> None:
    fake = FakeWikidata()
    found, ctx, _ = await run_shuttle(fake, organization("Q900009"))
    assert (found, ctx.notes) == ([], (NOTE_PERSON,))
    assert fake.calls == ["details:Q900009"]  # nothing more is asked
    found, ctx, _ = await run_shuttle(FakeWikidata(), organization("Q900010"))
    assert (found, ctx.notes) == ([], (NOTE_NOT_ORGANIZATION,))


async def test_properties_need_one_value() -> None:
    details = results(
        kind_row(label=text("Example Holdings"), country=text("GB"), lei=text(LEI)),
        kind_row(label=text("Example Holdings"), country=text("IE"), lei=text("NOT-AN-LEI")),
    )
    fake = FakeWikidata({"details:Q900001": details, "relatives:Q900001": results()})
    found, _, _ = await run_shuttle(fake, organization("Example Holdings"))
    properties = entities(found, ORG)["Example Holdings"]
    assert "country" not in properties  # two countries
    assert properties["lei"] == LEI  # the other one is not an LEI
    assert "industry" not in properties


async def test_subsidiaries_are_capped() -> None:
    rows = [
        {"relation": text("subsidiary"), "other": uri(f"Q{9100000 + n}"), "label": text(f"Sub {n}")}
        for n in range(MAX_SUBSIDIARIES + 5)
    ]
    fake = FakeWikidata({"relatives:Q900001": results(*rows)})
    found, ctx, _ = await run_shuttle(fake, organization("Example Holdings"))
    assert len(edges(found, R.SUBSIDIARY_OF)) == MAX_SUBSIDIARIES
    assert (
        NOTE_CAPPED.format(count=MAX_SUBSIDIARIES + 5, what="subsidiaries", limit=MAX_SUBSIDIARIES)
        in ctx.notes
    )


# --- domains ------------------------------------------------------------------------------


async def test_domain_owners() -> None:
    fake = FakeWikidata()
    entity = entity_input(DOMAIN, "example.com")
    found, _, _ = await run_shuttle(fake, entity)
    assert fake.calls == ["website:example.com"]
    query = fake.requests[0].url.params["query"]
    assert "<http://www.example.com>" in query
    assert entities(found, ORG) == {
        "Example Holdings": {"wikidata_id": "Q900001", "wikidata_checked_at": NOW},
        "Example Group": {"wikidata_id": "Q900002", "wikidata_checked_at": NOW},
    }
    assert edges(found, R.OWNS) == {
        ("Example Holdings", "example.com", WEBSITE_NOTE),
        ("Example Group", "example.com", WEBSITE_NOTE),
    }
    found, ctx, _ = await run_shuttle(FakeWikidata(), entity_input(DOMAIN, "example.org"))
    assert (found, ctx.notes) == ([], (NOTE_NO_WEBSITE,))


# --- errors -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (httpx.Response(400, text="bad query"), FailureReason.SOURCE_INVALID_RESPONSE),
        (b"<html>not json</html>", FailureReason.SOURCE_INVALID_RESPONSE),
        ({"results": {"bindings": "nope"}}, FailureReason.SOURCE_INVALID_RESPONSE),
        (
            httpx.Response(500, text="java.util.concurrent.TimeoutException"),
            FailureReason.SOURCE_UNAVAILABLE,
        ),
    ],
)
async def test_bad_answers_fail_the_job(reply: Reply, reason: FailureReason) -> None:
    fake = FakeWikidata({"label:Example Holdings": reply})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake, organization("Example Holdings"))
    assert info.value.reason is reason


async def test_retries_and_rate_limits() -> None:
    fake = FakeWikidata(
        {
            "label:Example Holdings": [
                httpx.Response(503),
                httpx.Response(429, headers={"Retry-After": "3"}),
                results({"item": uri("Q900001")}),
            ]
        }
    )
    found, ctx, time = await run_shuttle(fake, organization("Example Holdings"))
    assert entities(found, ORG)["Example Holdings"]["wikidata_id"] == "Q900001"
    assert 2.0 in time.sleeps and 3.0 in time.sleeps
    assert ctx.last_progress is not None and "1 retried, 1 rate-limited" in ctx.last_progress


async def test_cancellation_stops_before_a_request() -> None:
    cancelled = asyncio.Event()
    cancelled.set()
    fake = FakeWikidata()
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, organization("Example Holdings"), cancel_event=cancelled)
    assert fake.requests == []


# --- the real Wikidata --------------------------------------------------------------------


@pytest.mark.network
async def test_real_wikidata() -> None:
    """Apple Inc. (Q312) from the real Wikidata. Only with ``make test-network``."""
    ctx = ShuttleContext(META)
    found = await collect(WikidataShuttle(), organization("Q312"), ctx=ctx)
    orgs = entities(found, ORG)
    (apple,) = [
        name
        for name, props in orgs.items()
        if props.get("wikidata_id") == "Q312" and name != "Q312"
    ]
    assert orgs[apple]["country"] == "US", ctx.notes
    assert "apple.com" in entities(found, DOMAIN), ctx.notes
