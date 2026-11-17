"""ShuttleContext, the Shuttle base class and the testing helpers."""

import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    Finding,
    JobCancelledError,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleMeta,
)
from nightloom_sdk.http import NetworkNotDeclaredError, ShuttleHttpClient
from nightloom_sdk.meta import RateLimit
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context

OFFLINE = ShuttleMeta(
    name="offline",
    version="1.0.0",
    title="Offline",
    description="A Shuttle that never uses the network.",
    input_types=frozenset({EntityType.DOMAIN}),
    output_entity_types=frozenset({EntityType.DOMAIN}),
    output_relationship_types=frozenset({RelationType.HAS_SUBDOMAIN}),
    sensitivity=Sensitivity.INFRASTRUCTURE,
    network=False,
)
ONLINE = OFFLINE.model_copy(
    update={
        "name": "online",
        "network": True,
        "rate_limit": RateLimit(requests=1, period_seconds=1),
    }
)


class Fetcher(Shuttle):
    meta = ONLINE

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        ctx.progress("fetching")
        reply = await ctx.http.get(f"https://{entity.value}/")
        yield EntityFinding(type=EntityType.DOMAIN, value=reply.text)


async def test_offline_shuttles_cannot_use_http() -> None:
    ctx = ShuttleContext(OFFLINE)
    with pytest.raises(NetworkNotDeclaredError):
        _ = ctx.http
    await ctx.aclose()


async def test_online_context_creates_a_guarded_client_lazily() -> None:
    ctx = ShuttleContext(ONLINE)
    client = ctx.http
    assert isinstance(client, ShuttleHttpClient)
    assert ctx.http is client
    await ctx.aclose()
    await ctx.aclose()  # idempotent


async def test_keys_are_read_only() -> None:
    ctx = ShuttleContext(OFFLINE, keys={"api_key": "k"})
    assert ctx.keys["api_key"] == "k"
    with pytest.raises(TypeError):
        ctx.keys["api_key"] = "other"  # type: ignore[index]
    assert ShuttleContext(OFFLINE).keys == {}


def test_cancellation() -> None:
    ctx = fake_context(OFFLINE)
    assert not ctx.cancelled
    ctx.raise_if_cancelled()
    cancelled = fake_context(OFFLINE, cancelled=True)
    assert cancelled.cancelled
    with pytest.raises(JobCancelledError):
        cancelled.raise_if_cancelled()


def test_progress_is_one_short_line(caplog: pytest.LogCaptureFixture) -> None:
    messages: list[str] = []
    ctx = ShuttleContext(OFFLINE, on_progress=messages.append)
    with caplog.at_level(logging.INFO, logger="nightloom.shuttle.offline"):
        ctx.progress("step\n one  " + "x" * 500)
    assert messages == [ctx.last_progress]
    assert ctx.last_progress is not None
    assert ctx.last_progress.startswith("step one x")
    assert len(ctx.last_progress) == 200
    assert caplog.messages[0].startswith("[offline] progress: step one")


async def test_collect_with_a_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="example.org")

    ctx = fake_context(ONLINE, transport=httpx.MockTransport(handler))
    findings = await collect(Fetcher(), entity_input(EntityType.DOMAIN, "WWW.Example.org"), ctx=ctx)
    assert findings == [EntityFinding(type=EntityType.DOMAIN, value="example.org")]
    assert ctx.last_progress == "fetching"


async def test_fake_context_refuses_unplanned_requests() -> None:
    with pytest.raises(AssertionError, match="Unexpected request"):
        await collect(Fetcher(), entity_input(EntityType.DOMAIN, "example.org"))


async def test_collect_stops_at_the_limit() -> None:
    class Endless(Shuttle):
        meta = OFFLINE

        async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
            while True:
                yield EntityFinding(type=EntityType.DOMAIN, value="example.org")

    findings = await collect(Endless(), entity_input(EntityType.DOMAIN, "a.example.org"), limit=3)
    assert len(findings) == 3


def test_entity_input_is_normalized() -> None:
    entity = entity_input(EntityType.DOMAIN, "Bücher.Example.")
    assert (entity.value, entity.key) == ("xn--bcher-kva.example", "domain:xn--bcher-kva.example")
    assert entity.ref == EntityRef(type=EntityType.DOMAIN, value="xn--bcher-kva.example")


def test_assert_declared() -> None:
    entity = entity_input(EntityType.DOMAIN, "www.example.org")
    parent = EntityFinding(type=EntityType.DOMAIN, value="example.org")
    link = RelationshipFinding(
        type=RelationType.HAS_SUBDOMAIN, source=parent.ref, target=entity.ref
    )
    assert_declared(OFFLINE, [parent, link], entity)
    with pytest.raises(AssertionError, match="ip_address"):
        assert_declared(
            OFFLINE, [EntityFinding(type=EntityType.IP_ADDRESS, value="192.0.2.1")], entity
        )
    with pytest.raises(AssertionError, match="RESOLVES_TO"):
        assert_declared(
            OFFLINE,
            [
                RelationshipFinding(
                    type=RelationType.RESOLVES_TO,
                    source=entity.ref,
                    target=EntityRef(type=EntityType.IP_ADDRESS, value="192.0.2.1"),
                )
            ],
            entity,
        )
    with pytest.raises(AssertionError, match="email"):
        assert_declared(
            OFFLINE,
            [
                RelationshipFinding(
                    type=RelationType.HAS_SUBDOMAIN,
                    source=entity.ref,
                    target=EntityRef(type=EntityType.EMAIL, value="a@example.org"),
                )
            ],
            entity,
        )
    with pytest.raises(AssertionError, match="not a Finding"):
        assert_declared(OFFLINE, ["example.org"], entity)  # type: ignore[list-item]


def test_findings_validate_their_shape() -> None:
    with pytest.raises(ValueError):
        EntityFinding(type=EntityType.DOMAIN, value="")
    with pytest.raises(ValueError):
        EntityFinding(type=EntityType.DOMAIN, value="example.org", confidence=1.5)
    with pytest.raises(ValueError):
        EntityFinding(type=EntityType.DOMAIN, value="example.org", admiralty="Z9")
    with pytest.raises(ValueError):
        EntityFinding(type="no_such_type", value="x")  # type: ignore[arg-type]
