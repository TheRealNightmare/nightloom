"""The GLEIF Shuttle against a fake GLEIF API: no network.

The fixtures (``fixtures/``) are GLEIF-shaped JSON:API records with fictional
companies and LEIs (with valid check digits): Example Holdings Limited with
a direct parent, an ultimate parent and two subsidiaries, two unrelated
companies called Example Trading Ltd, and a sole proprietor, Pat Example.
Their addresses carry street names and postal codes on purpose, so the
tests can check that none of them is kept.
"""

import asyncio
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
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
from nightloom_shuttle_gleif import GleifShuttle
from nightloom_shuttle_gleif.client import ACCEPT, BASE_URL, HOST, is_valid_lei
from nightloom_shuttle_gleif.shuttle import (
    BOTH_PARENTS,
    DIRECT_PARENT,
    MAX_CHILDREN,
    MAX_MATCHES,
    NOTE_BAD_LEI,
    NOTE_BY_LEI,
    NOTE_CHILDREN_CAPPED,
    NOTE_MALFORMED,
    NOTE_NO_MATCH,
    NOTE_SKIPPED_PEOPLE,
    NOTE_SOLE_PROPRIETOR,
    NOTE_UNKNOWN_LEI,
    REQUEST_BUDGET_SECONDS,
    ULTIMATE_PARENT,
    USAGE_NOTE,
    lei_of,
)

ORG = EntityType.ORGANIZATION
R = RelationType
META = GleifShuttle.meta
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)

HOLD = "5493000EXHOLD0000105"
SUBA = "5493000EXSUBA0000284"
SUBB = "5493000EXSUBB0000305"
PRNT = "5493000EXPRNT0000432"
ULTM = "5493000EXULTM0000521"
TWIN = "5493000EXTWIN0000682"
SOLE = "5493000EXSOLE0000737"
MIXD = "5493000EXMIXD0000816"

# Parts of the fixtures' addresses and the sole proprietor that must never be kept.
PRIVATE = ("Example Street", "Exampleton", "EX1 2MP", "Beispielstraße", "Private Lane", "Pat")


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


Reply = dict[str, Any] | bytes | httpx.Response
NOT_FOUND = httpx.Response(404, json=fixture("not_found"))


def search(name: str) -> str:
    return f"search:{name}"


def default_routes() -> dict[str, Reply]:
    return {
        search("Example Holdings Limited"): fixture("search_holdings"),
        search("Example Trading Ltd"): fixture("search_twins"),
        f"lei-records/{HOLD}": fixture("record_holdings"),
        f"lei-records/{HOLD}/direct-parent": fixture("parent_direct"),
        f"lei-records/{HOLD}/ultimate-parent": fixture("parent_ultimate"),
        f"lei-records/{HOLD}/direct-children": fixture("children_holdings"),
        f"lei-records/{SOLE}": fixture("record_sole"),
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


class FakeGleif:
    """Answers from ``routes`` by path below ``/api/v1/`` (searches: ``search:<name>``).

    A list replies in turn; anything unknown is a 404. Every request must go
    to api.gleif.org over https.
    """

    def __init__(self, routes: dict[str, Reply | list[Reply]] | None = None) -> None:
        self.routes: dict[str, Reply | list[Reply]] = dict(default_routes())
        self.routes.update(routes or {})
        self.requests: list[httpx.Request] = []

    @staticmethod
    def route(request: httpx.Request) -> str:
        path = request.url.path.removeprefix("/api/v1/")
        name = request.url.params.get("filter[entity.legalName]")
        return search(name) if path == "lei-records" and name is not None else path

    @property
    def calls(self) -> list[str]:
        return [self.route(request) for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == HOST, f"request to {request.url.host}"
        assert request.headers["Accept"] == ACCEPT
        reply = self.routes.get(self.route(request), NOT_FOUND)
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)


async def run_shuttle(
    fake: FakeGleif,
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
    shuttle = GleifShuttle(sleep=time.sleep, clock=time, now=lambda: NOW)
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, found, entity)
    assert_valid(found)
    assert_only_gleif(fake)
    assert_nothing_private(found, ctx)
    return found, ctx, time


def organization(value: str, **properties: Any) -> EntityInput:
    entity = entity_input(ORG, value)
    return entity.model_copy(update={"properties": properties}) if properties else entity


def assert_valid(found: list[Finding]) -> None:
    """Every entity finding is valid as the worker would store it (value and properties)."""
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)


def assert_only_gleif(fake: FakeGleif) -> None:
    for request in fake.requests:
        assert str(request.url).startswith(BASE_URL)
        assert request.url.port in (None, 443)


def assert_nothing_private(found: list[Finding], ctx: ShuttleContext) -> None:
    """No street address, postal code or sole proprietor's name in a finding or a note."""
    text = repr(found) + repr(ctx.notes)
    for private in PRIVATE:
        assert private not in text, private


def organizations(found: list[Finding]) -> dict[str, dict[str, Any]]:
    return {
        f.value: dict(f.properties or {})
        for f in found
        if isinstance(f, EntityFinding) and f.type == ORG
    }


def subsidiaries(found: Iterable[Finding]) -> set[tuple[str, str, str | None]]:
    return {
        (f.source.value, f.target.value, f.note)
        for f in found
        if isinstance(f, RelationshipFinding) and f.type == R.SUBSIDIARY_OF
    }


HOLDINGS = {
    "lei": HOLD,
    "legal_name": "EXAMPLE HOLDINGS LIMITED",
    "jurisdiction": "GB",
    "legal_form": "H0PO",
    "registered_as": "01234567",
    "entity_status": "ACTIVE",
    "lei_status": "ISSUED",
    "country": "GB",
    "gleif_checked_at": NOW,
}


# --- metadata and registration ------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "gleif"
    assert META.title == "Legal entity record (GLEIF)"
    assert META.input_types == {ORG}
    assert META.output_entity_types == {ORG}
    assert META.output_relationship_types == {R.SUBSIDIARY_OF}
    assert (META.mode, META.network, META.pivots) == (Mode.PASSIVE, True, False)
    assert META.sensitivity is Sensitivity.ORGANIZATION
    assert META.rate_limit is not None and META.rate_limit.per_second == pytest.approx(1)
    assert META.timeout_seconds == 60
    assert META.cache_ttl_seconds == 24 * 3600
    assert (META.default_admiralty, META.default_confidence) == ("A2", 0.9)
    assert META.homepage == "https://www.gleif.org/en/lei-data/gleif-api"
    assert META.terms_url == "https://www.gleif.org/en/meta/lei-data-terms-of-use"
    assert META.usage_note == USAGE_NOTE
    assert META.required_keys == frozenset()
    registry = ShuttleRegistry.discover()
    assert registry.get("gleif") is GleifShuttle
    assert GleifShuttle in registry.list(ORG)
    assert GleifShuttle not in registry.list(EntityType.DOMAIN)
    GleifShuttle.check_configuration()  # nothing to configure


def test_lei_check_digits() -> None:
    for lei in (HOLD, SUBA, SUBB, PRNT, ULTM, TWIN, SOLE, MIXD):
        assert is_valid_lei(lei)
    assert is_valid_lei("HWUPKR0MPOU8FGXBT394")  # a real one (Apple Inc.)
    assert not is_valid_lei("HWUPKR0MPOU8FGXBT395")  # one digit off
    assert not is_valid_lei("hwupkr0mpou8fgxbt394")  # lei_of upper-cases first
    assert not is_valid_lei("HWUPKR0MPOU8FGXBT39")
    assert not is_valid_lei("HWUPKR0MPOU8FGXBT3/4")
    assert lei_of(" hwupkr0mpou8fgxbt394 ") == "HWUPKR0MPOU8FGXBT394"
    assert lei_of("Apple Inc.") is None
    assert lei_of(None) is None


# --- finding the record -------------------------------------------------------------------


async def test_by_legal_name_with_parents_and_subsidiaries() -> None:
    fake = FakeGleif()
    entity = organization("Example Holdings Limited")
    found, ctx, _ = await run_shuttle(fake, entity)

    assert fake.calls == [
        search("Example Holdings Limited"),
        f"lei-records/{HOLD}/direct-parent",
        f"lei-records/{HOLD}/ultimate-parent",
        f"lei-records/{HOLD}/direct-children",
    ]
    assert fake.requests[0].url.params["page[size]"] == str(MAX_MATCHES)
    assert fake.requests[-1].url.params["page[size]"] == str(MAX_CHILDREN)

    orgs = organizations(found)
    # The input keeps its own name; GLEIF's legal name is a property.
    assert orgs["Example Holdings Limited"] == HOLDINGS
    # "EXAMPLE HOLDINGS GROUP PLC" was in GLEIF's answer but is another name.
    assert "EXAMPLE HOLDINGS GROUP PLC" not in orgs
    assert orgs["EXAMPLE PARENT PLC"]["lei"] == PRNT
    assert orgs["Example Group AG"] == {
        "lei": ULTM,
        "legal_name": "Example Group AG",
        "jurisdiction": "DE",
        "legal_form": "Aktiengesellschaft",  # ELF 8888: the form given as text
        "registered_as": "HRB 12345",
        "entity_status": "ACTIVE",
        "lei_status": "ISSUED",
        "country": "DE",
        "gleif_checked_at": NOW,
    }
    subsidiary_b = orgs["Example Subsidiary B, Inc."]
    assert subsidiary_b["jurisdiction"] == "US-DE"
    assert "registered_as" not in subsidiary_b  # "N/A"
    assert set(orgs) == {
        "Example Holdings Limited",
        "EXAMPLE PARENT PLC",
        "Example Group AG",
        "EXAMPLE SUBSIDIARY A LTD",
        "Example Subsidiary B, Inc.",
    }

    assert subsidiaries(found) == {
        ("Example Holdings Limited", "EXAMPLE PARENT PLC", DIRECT_PARENT),
        ("Example Holdings Limited", "Example Group AG", ULTIMATE_PARENT),
        ("EXAMPLE SUBSIDIARY A LTD", "Example Holdings Limited", DIRECT_PARENT),
        ("Example Subsidiary B, Inc.", "Example Holdings Limited", DIRECT_PARENT),
    }
    for finding in found:
        if isinstance(finding, RelationshipFinding):
            assert (finding.admiralty, finding.confidence) == ("B2", 0.8)
        else:
            assert (finding.admiralty, finding.confidence) == (None, None)  # A2 by default

    # The child that is a sole proprietor and the malformed one are skipped and counted.
    assert NOTE_SKIPPED_PEOPLE.format(count=1) in ctx.notes
    assert NOTE_MALFORMED.format(count=1) in ctx.notes


async def test_names_match_ignoring_case_and_other_names() -> None:
    fake = FakeGleif(
        {
            search("example holdings limited"): fixture("search_holdings"),
            search("Example Holdings"): fixture("search_holdings"),
        }
    )
    found, _, _ = await run_shuttle(fake, organization("example holdings limited"))
    assert organizations(found)["example holdings limited"]["lei"] == HOLD
    # A trading name listed in the record matches too.
    found, _, _ = await run_shuttle(fake, organization("Example Holdings"))
    assert organizations(found)["Example Holdings"]["lei"] == HOLD


async def test_by_lei_property() -> None:
    fake = FakeGleif()
    found, _, _ = await run_shuttle(fake, organization("Example Holdings", lei=HOLD.lower()))
    assert fake.calls[0] == f"lei-records/{HOLD}"
    assert organizations(found)["Example Holdings"] == HOLDINGS


async def test_by_lei_as_the_name() -> None:
    fake = FakeGleif()
    found, ctx, _ = await run_shuttle(fake, organization(HOLD))
    assert fake.calls[0] == f"lei-records/{HOLD}"
    orgs = organizations(found)
    # The LEI-named input keeps only its LEI; the organization goes in under its name.
    assert orgs[HOLD] == {"lei": HOLD}
    assert orgs["EXAMPLE HOLDINGS LIMITED"] == HOLDINGS
    assert ("EXAMPLE HOLDINGS LIMITED", "EXAMPLE PARENT PLC", DIRECT_PARENT) in subsidiaries(found)
    assert all(HOLD not in (source, target) for source, target, _ in subsidiaries(found))
    assert NOTE_BY_LEI.format(lei=HOLD, name="EXAMPLE HOLDINGS LIMITED") in ctx.notes


async def test_invalid_lei_property_falls_back_to_the_name() -> None:
    fake = FakeGleif()
    found, ctx, _ = await run_shuttle(
        fake, organization("Example Holdings Limited", lei="5493000EXHOLD0000106")
    )
    assert NOTE_BAD_LEI in ctx.notes
    assert fake.calls[0] == search("Example Holdings Limited")
    assert organizations(found)["Example Holdings Limited"]["lei"] == HOLD


async def test_unknown_lei() -> None:
    fake = FakeGleif()
    found, ctx, _ = await run_shuttle(fake, organization(TWIN))
    assert found == []
    assert ctx.notes == (NOTE_UNKNOWN_LEI.format(lei=TWIN),)
    assert fake.calls == [f"lei-records/{TWIN}"]


async def test_no_match() -> None:
    fake = FakeGleif({search("Example"): fixture("search_holdings")})
    found, ctx, _ = await run_shuttle(fake, organization("Example"))
    assert found == []
    assert ctx.notes == (NOTE_NO_MATCH,)
    found, ctx, _ = await run_shuttle(FakeGleif(), organization("Nobody Ltd"))  # 404
    assert (found, ctx.notes) == ([], (NOTE_NO_MATCH,))


async def test_several_matches_add_nothing() -> None:
    fake = FakeGleif()
    found, ctx, _ = await run_shuttle(fake, organization("Example Trading Ltd"))
    assert found == []
    assert fake.calls == [search("Example Trading Ltd")]
    (note,) = ctx.notes
    assert note.startswith("2 LEI records have this name")
    assert f"{TWIN} (GB)" in note and f"{MIXD} (IE)" in note
    assert note.index(MIXD) < note.index(TWIN)  # by LEI


async def test_sole_proprietor_with_the_same_name_is_never_listed() -> None:
    twins = fixture("search_twins")
    twins["data"].append(fixture("record_sole")["data"])
    twins["data"][-1]["attributes"]["entity"]["legalName"]["name"] = "Example Trading Ltd"
    fake = FakeGleif({search("Example Trading Ltd"): twins})
    found, ctx, _ = await run_shuttle(fake, organization("Example Trading Ltd"))
    assert found == []
    assert ctx.notes[0].startswith("2 LEI records have this name")
    assert SOLE not in repr(ctx.notes)
    assert NOTE_SKIPPED_PEOPLE.format(count=1) in ctx.notes
    # Alone, the person's record is a note and nothing else.
    fake = FakeGleif({search("Example Trading Ltd"): {**twins, "data": twins["data"][-1:]}})
    found, ctx, _ = await run_shuttle(fake, organization("Example Trading Ltd"))
    assert (found, ctx.notes) == ([], (NOTE_SOLE_PROPRIETOR,))


async def test_sole_proprietor_is_never_kept() -> None:
    fake = FakeGleif()
    found, ctx, _ = await run_shuttle(fake, organization(SOLE))
    assert found == []
    assert ctx.notes == (NOTE_SOLE_PROPRIETOR,)
    # No parents or subsidiaries are asked for either.
    assert fake.calls == [f"lei-records/{SOLE}"]


async def test_sole_proprietor_as_a_parent_is_skipped() -> None:
    fake = FakeGleif({f"lei-records/{HOLD}/direct-parent": fixture("record_sole")})
    found, ctx, _ = await run_shuttle(fake, organization("Example Holdings Limited"))
    targets = {target for source, target, _ in subsidiaries(found)}
    assert "Example Group AG" in targets and len(targets) == 2  # ultimate parent + the input
    assert NOTE_SKIPPED_PEOPLE.format(count=2) in ctx.notes  # the parent and one child


async def test_same_parent_both_ways_and_no_parents() -> None:
    fake = FakeGleif(
        {
            f"lei-records/{HOLD}/ultimate-parent": fixture("parent_direct"),
            f"lei-records/{HOLD}/direct-children": NOT_FOUND,
        }
    )
    found, _, _ = await run_shuttle(fake, organization("Example Holdings Limited"))
    assert subsidiaries(found) == {("Example Holdings Limited", "EXAMPLE PARENT PLC", BOTH_PARENTS)}
    fake = FakeGleif(
        {
            f"lei-records/{HOLD}/direct-parent": NOT_FOUND,
            f"lei-records/{HOLD}/ultimate-parent": NOT_FOUND,
            f"lei-records/{HOLD}/direct-children": NOT_FOUND,
        }
    )
    found, _, _ = await run_shuttle(fake, organization("Example Holdings Limited"))
    assert subsidiaries(found) == set()
    assert set(organizations(found)) == {"Example Holdings Limited"}


async def test_relative_with_the_same_name_is_not_linked_to_itself() -> None:
    parent = fixture("parent_direct")
    parent["data"]["attributes"]["entity"]["legalName"]["name"] = "Example Holdings Limited"
    fake = FakeGleif({f"lei-records/{HOLD}/direct-parent": parent})
    found, _, _ = await run_shuttle(fake, organization("Example Holdings Limited"))
    assert all(source != target for source, target, _ in subsidiaries(found))


async def test_children_are_capped() -> None:
    children = fixture("children_holdings")
    children["meta"]["pagination"]["total"] = 120
    fake = FakeGleif({f"lei-records/{HOLD}/direct-children": children})
    _, ctx, _ = await run_shuttle(fake, organization("Example Holdings Limited"))
    assert NOTE_CHILDREN_CAPPED.format(total=120, limit=MAX_CHILDREN) in ctx.notes


async def test_optional_fields_in_odd_forms_are_left_out() -> None:
    record = fixture("record_holdings")
    entity = record["data"]["attributes"]["entity"]
    entity.update(
        jurisdiction="Somewhere",
        status=None,
        legalForm={"id": "9999", "other": None},
        legalAddress={"country": "GBR"},
    )
    fake = FakeGleif({f"lei-records/{HOLD}": record})
    found, _, _ = await run_shuttle(fake, organization(HOLD))
    properties = organizations(found)["EXAMPLE HOLDINGS LIMITED"]
    for left_out in ("jurisdiction", "entity_status", "legal_form", "country"):
        assert left_out not in properties
    assert properties["lei"] == HOLD


# --- errors -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (httpx.Response(400, json={"errors": []}), FailureReason.SOURCE_INVALID_RESPONSE),
        (b"<html>not json</html>", FailureReason.SOURCE_INVALID_RESPONSE),
        ({"data": "nope"}, FailureReason.SOURCE_INVALID_RESPONSE),
        (httpx.Response(503), FailureReason.SOURCE_UNAVAILABLE),
    ],
)
async def test_bad_answers_fail_the_job(reply: Reply, reason: FailureReason) -> None:
    fake = FakeGleif({search("Example Holdings Limited"): reply})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake, organization("Example Holdings Limited"))
    assert info.value.reason is reason


async def test_lookup_answering_another_lei_fails() -> None:
    fake = FakeGleif({f"lei-records/{HOLD}": fixture("parent_direct")})
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake, organization(HOLD))
    assert info.value.reason is FailureReason.SOURCE_INVALID_RESPONSE


async def test_retries_server_errors_and_waits_for_rate_limits() -> None:
    fake = FakeGleif(
        {
            search("Example Holdings Limited"): [
                httpx.Response(502),
                httpx.Response(429, headers={"Retry-After": "7"}),
                fixture("search_holdings"),
            ]
        }
    )
    found, ctx, time = await run_shuttle(fake, organization("Example Holdings Limited"))
    assert organizations(found)["Example Holdings Limited"]["lei"] == HOLD
    assert 2.0 in time.sleeps and 7.0 in time.sleeps
    assert ctx.last_progress is not None and "1 retried, 1 rate-limited" in ctx.last_progress


async def test_too_long_retry_after_gives_up() -> None:
    fake = FakeGleif(
        {search("Example Holdings Limited"): httpx.Response(429, headers={"Retry-After": "600"})}
    )
    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(fake, organization("Example Holdings Limited"))
    assert info.value.reason is FailureReason.SOURCE_UNAVAILABLE
    assert len(fake.requests) == 1


async def test_no_request_after_the_budget() -> None:
    time = FakeTime()
    time.now = REQUEST_BUDGET_SECONDS  # the deadline is set at the start of the run

    class LateStart(FakeTime):
        def __call__(self) -> float:
            value = self.now
            self.now += REQUEST_BUDGET_SECONDS
            return value

    with pytest.raises(ShuttleFailedError) as info:
        await run_shuttle(FakeGleif(), organization("Example Holdings Limited"), time=LateStart())
    assert info.value.reason is FailureReason.SOURCE_UNAVAILABLE


async def test_cancellation_stops_before_a_request() -> None:
    cancelled = asyncio.Event()
    cancelled.set()
    fake = FakeGleif()
    with pytest.raises(JobCancelledError):
        await run_shuttle(fake, organization("Example Holdings Limited"), cancel_event=cancelled)
    assert fake.requests == []


# --- the real GLEIF -----------------------------------------------------------------------


@pytest.mark.network
async def test_real_gleif() -> None:
    """Apple Inc.'s LEI record, from the real GLEIF API. Only with ``make test-network``."""
    ctx = ShuttleContext(META)
    found = await collect(GleifShuttle(), organization("HWUPKR0MPOU8FGXBT394"), ctx=ctx)
    orgs = organizations(found)
    assert orgs["HWUPKR0MPOU8FGXBT394"] == {"lei": "HWUPKR0MPOU8FGXBT394"}
    (apple,) = [props for props in orgs.values() if props.get("legal_name") == "Apple Inc."]
    assert apple["jurisdiction"] == "US-CA"
