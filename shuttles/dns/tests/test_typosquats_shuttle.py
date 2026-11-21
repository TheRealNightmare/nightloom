"""The typosquat Shuttle with a fake resolver: no network."""

import asyncio
import time
from collections.abc import Callable, Set
from datetime import UTC, datetime

import dns.rdata
import dns.rdataclass
import dns.rdatatype
import pytest

from nightloom_core.entities import EntityType, build_entity
from nightloom_core.relationships import RelationType, validate_relationship
from nightloom_sdk import (
    EntityFinding,
    FailureReason,
    Finding,
    InputRefusedError,
    JobCancelledError,
    Mode,
    RefusalReason,
    RelationshipFinding,
    ShuttleFailedError,
)
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_dns import TyposquatShuttle
from nightloom_shuttle_dns.client import Answer, DnsQueryError
from nightloom_shuttle_dns.config import DnsConfig
from nightloom_shuttle_dns.permutations import lookalikes
from nightloom_shuttle_dns.typosquats import (
    BUDGET_SECONDS,
    MAX_CONSECUTIVE_FAILURES,
    lookalike_note,
)

R = dns.rdatatype.RdataType
D = EntityType.DOMAIN
META = TyposquatShuttle.meta
CONFIG = DnsConfig()
NOW = datetime(2026, 11, 22, 6, 0, tzinfo=UTC)


def ns(name: str) -> Answer:
    record = dns.rdata.from_text(dns.rdataclass.IN, R.NS, "ns1.parking.example.net.")
    return Answer(name=name, records=(record,))


class FakeDns:
    """NS answers for the registered names; every other name does not exist."""

    def __init__(self, registered: Set[str] = frozenset(), failing: set[str] | None = None) -> None:
        self.registered = registered
        self.failing = failing
        self.asked: list[tuple[str, R]] = []

    async def query(self, name: str, rdtype: R) -> Answer | None:
        self.asked.append((name, rdtype))
        await asyncio.sleep(0)
        if self.failing is not None and (not self.failing or name in self.failing):
            raise DnsQueryError("Timeout")
        return ns(name) if name in self.registered else None


async def run(
    value: str,
    client: FakeDns,
    *,
    clock: Callable[[], float] = time.monotonic,
    limit: int = 500,
) -> tuple[list[Finding], tuple[str, ...]]:
    ctx = fake_context(META)
    shuttle = TyposquatShuttle(
        client=client,
        config=CONFIG,
        now=lambda: NOW,
        limit=limit,
        clock=clock,
    )
    entity = entity_input(D, value)
    findings = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, findings, entity)
    for finding in findings:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
        else:
            validate_relationship(finding.type, finding.source.type, finding.target.type)
    return findings, ctx.notes


def test_meta() -> None:
    assert META.name == "typosquats"
    assert META.mode == Mode.PASSIVE
    assert META.pivots is False
    assert META.cache_ttl_seconds == 24 * 60 * 60
    assert META.output_relationship_types == {RelationType.LOOKALIKE_OF}


async def test_registered_lookalikes_are_linked() -> None:
    client = FakeDns({"exmple.org", "example.net", "examp1e.org"})
    findings, notes = await run("example.org", client)
    total = len(lookalikes("example.org"))
    # One NS query per candidate, nothing else.
    assert len(client.asked) == total
    assert {rdtype for _, rdtype in client.asked} == {R.NS}
    links = [f for f in findings if isinstance(f, RelationshipFinding)]
    assert {(r.source.value, r.target.value, r.note) for r in links} == {
        ("example.net", "example.org", lookalike_note("suffix")),
        ("exmple.org", "example.org", lookalike_note("omission")),
        ("examp1e.org", "example.org", lookalike_note("homoglyph")),
    }
    original = findings[0]
    assert isinstance(original, EntityFinding)
    assert original.value == "example.org"
    assert original.properties == {
        "typosquats_checked_at": NOW,
        "typosquat_candidates": total,
        "typosquats_registered": 3,
    }
    assert f"Tried {total} lookalike name(s): 3 registered, 0 quer(ies) failed." in notes[-1]


async def test_a_subdomain_is_checked_as_its_registrable_domain() -> None:
    client = FakeDns({"exmple.co.uk"})
    findings, notes = await run("shop.example.co.uk", client)
    assert "Checked lookalikes of the registrable domain example.co.uk." in notes
    links = [f for f in findings if isinstance(f, RelationshipFinding)]
    assert [(r.source.value, r.target.value) for r in links] == [("exmple.co.uk", "example.co.uk")]


@pytest.mark.parametrize(
    ("value", "reason"),
    [("printer.local", RefusalReason.INTERNAL_NAME), ("co.uk", RefusalReason.NOT_REGISTRABLE)],
)
async def test_refused_before_any_query(value: str, reason: RefusalReason) -> None:
    client = FakeDns()
    with pytest.raises(InputRefusedError) as info:
        await run(value, client)
    assert info.value.reason == reason
    assert client.asked == []


async def test_failed_queries_are_counted() -> None:
    client = FakeDns({"example.net"}, failing={"exmple.org"})
    findings, notes = await run("example.org", client)
    assert any(isinstance(f, RelationshipFinding) for f in findings)
    assert "1 quer(ies) failed" in notes[-1]


async def test_no_answer_at_all_is_source_unavailable() -> None:
    with pytest.raises(ShuttleFailedError) as info:
        await run("example.org", FakeDns(failing=set()))
    assert info.value.reason == FailureReason.SOURCE_UNAVAILABLE


async def test_stops_after_many_failures_in_a_row() -> None:
    candidates = [c.name for c in lookalikes("example.org")]
    # The first answers, then everything fails.
    client = FakeDns(failing=set(candidates[1:]))
    findings, notes = await run("example.org", client)
    assert any(
        f"Stopped after {MAX_CONSECUTIVE_FAILURES} failed queries in a row" in n for n in notes
    )
    assert len(client.asked) < len(candidates)
    assert isinstance(findings[0], EntityFinding)


async def test_time_limit() -> None:
    ticks = iter([0.0] + [0.0] * 10 + [BUDGET_SECONDS + 1] * 10_000)
    client = FakeDns({"example.net"})
    _, notes = await run("example.org", client, clock=lambda: next(ticks))
    assert any(n.startswith("Reached the time limit after") for n in notes)
    assert len(client.asked) <= 10


async def test_cancelled_before_querying() -> None:
    ctx = fake_context(META, cancelled=True)
    shuttle = TyposquatShuttle(client=FakeDns(), config=CONFIG, now=lambda: NOW)
    with pytest.raises(JobCancelledError):
        await collect(shuttle, entity_input(D, "example.org"), ctx=ctx)


async def test_the_limit_caps_the_queries() -> None:
    client = FakeDns()
    findings, _ = await run("example.org", client, limit=20)
    assert len(client.asked) == 20
    original = findings[0]
    assert isinstance(original, EntityFinding)
    assert original.properties is not None
    assert original.properties["typosquat_candidates"] == 20
