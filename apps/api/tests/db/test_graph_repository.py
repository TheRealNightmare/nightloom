"""The Neo4j schema and the graph repository against a real Neo4j (marker ``graph``)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from neo4j import AsyncTransaction
from neo4j.exceptions import ConstraintError, DriverError

from nightloom_api.config import Settings
from nightloom_api.graph import repository, schema
from nightloom_api.graph.repository import (
    EndpointNotFoundError,
    EntityRecord,
    GraphCommitError,
    GraphRepository,
    PersonEntitiesDisabledError,
    RelationshipNotAllowedError,
)
from nightloom_api.infra import graph
from nightloom_core.entities import EntityType, Sensitivity, build_entity
from nightloom_core.provenance import Observation, manual_observation
from nightloom_core.relationships import RelationType
from support import TrackingGraphRepository, graph_test_settings

pytestmark = pytest.mark.graph

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
R = RelationType
E = EntityType


def manual(at: datetime = T0, note: str | None = None) -> Observation:
    return manual_observation(at, note)


def shuttle(
    at: datetime, confidence: float, reliability: str = "A", credibility: int = 1
) -> Observation:
    return Observation.model_validate(
        {
            "source": "shuttle:dns@1.0.0",
            "observed_at": at,
            "confidence": confidence,
            "admiralty_reliability": reliability,
            "admiralty_credibility": credibility,
            "evidence_ref": "evidence/abc",
        }
    )


async def add(
    repo: GraphRepository, case_id: uuid.UUID, entity_type: EntityType, value: str
) -> EntityRecord:
    return await repo.upsert_entity(case_id, build_entity(entity_type, value), manual())


async def count_nodes(repo: GraphRepository, case_id: uuid.UUID) -> int:
    return len((await repo.get_graph(case_id, limit=10_000)).nodes)


# --- schema --------------------------------------------------------------------------


async def test_schema_apply_is_idempotent(graph_repo: TrackingGraphRepository) -> None:
    driver = graph_repo._driver
    assert await schema.apply_schema(driver) == [item.name for item in schema.SCHEMA]
    await schema.apply_schema(driver)

    rows = await schema.describe_schema(driver)
    constraints = [row for row in rows if row["kind"] == "constraint"]
    assert constraints == [
        {
            "kind": "constraint",
            "name": schema.CONSTRAINT_NAME,
            "type": "NODE_PROPERTY_UNIQUENESS",
            "labelsOrTypes": ["Entity"],
            "properties": ["case_id", "key"],
        }
    ]
    indexes = {row["name"]: row["properties"] for row in rows if row["kind"] == "index"}
    assert indexes[schema.CASE_INDEX_NAME] == ["case_id"]
    assert indexes[schema.CASE_TYPE_INDEX_NAME] == ["case_id", "type"]


async def test_constraint_rejects_a_second_node_with_the_same_case_and_key(
    graph_repo: TrackingGraphRepository,
) -> None:
    case_id = graph_repo.new_case()
    await add(graph_repo, case_id, E.DOMAIN, "example.org")
    async with graph_repo._driver.session() as session:
        with pytest.raises(ConstraintError):
            result = await session.run(
                "CREATE (:Entity {case_id: $case_id, key: 'domain:example.org'})",
                case_id=str(case_id),
            )
            await result.consume()


def test_schema_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], neo4j_ready: None
) -> None:
    monkeypatch.setattr(schema, "get_settings", graph_test_settings)
    assert schema.main(["apply"]) == 0
    assert "Applied the Neo4j schema: entity_case_key_unique" in capsys.readouterr().out
    assert schema.main(["show"]) == 0
    out = capsys.readouterr().out
    assert "constraint entity_case_key_unique: NODE_PROPERTY_UNIQUENESS" in out
    assert "index entity_case_id: RANGE" in out


def test_schema_cli_failure_is_short_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    closed_port: int,
    settings_for_port: object,
    fake_secrets: tuple[str, ...],
) -> None:
    settings: Settings = settings_for_port(closed_port)  # type: ignore[operator]
    monkeypatch.setattr(schema, "get_settings", lambda: settings)
    assert schema.main(["apply", "--wait", "0"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("Neo4j schema apply failed: ")
    for secret in fake_secrets:
        assert secret not in captured.err + captured.out


async def test_wait_for_neo4j_retries_until_the_deadline(
    closed_port: int, settings_for_port: object
) -> None:
    driver = graph.create_driver(settings_for_port(closed_port))  # type: ignore[operator]
    try:
        with pytest.raises((DriverError, OSError)):
            await schema.wait_for_neo4j(driver, seconds=0.2, interval=0.05)
    finally:
        await driver.close()


# --- upserts ---------------------------------------------------------------------------


async def test_different_spellings_merge_into_one_node(graph_repo: TrackingGraphRepository) -> None:
    case_id = graph_repo.new_case()
    first = await graph_repo.upsert_entity(
        case_id, build_entity(E.DOMAIN, "Example.ORG."), manual(T0)
    )
    second = await graph_repo.upsert_entity(
        case_id, build_entity(E.DOMAIN, "example.org"), manual(T0 + timedelta(hours=1))
    )

    assert first.created and not second.created
    assert first.key == second.key == "domain:example.org"
    assert second.value == "example.org"
    assert second.sensitivity == Sensitivity.INFRASTRUCTURE
    assert second.provenance.observation_count == 2
    assert second.provenance.first_seen == T0
    assert second.provenance.last_seen == T0 + timedelta(hours=1)
    assert second.provenance.sources == ["manual"]
    assert await count_nodes(graph_repo, case_id) == 1


async def test_provenance_merges_sources_confidence_and_order(
    graph_repo: TrackingGraphRepository,
) -> None:
    case_id = graph_repo.new_case()
    entity = build_entity(E.ORGANIZATION, "ACME Corp", {"country": "BD"})
    await graph_repo.upsert_entity(case_id, entity, manual(T0, note="from the brief"))
    await graph_repo.upsert_entity(case_id, entity, shuttle(T0 + timedelta(days=1), 0.95))
    # An older observation arriving late: moves first_seen back, nothing else.
    late = await graph_repo.upsert_entity(
        case_id,
        build_entity(E.ORGANIZATION, "acme corp", {"industry": "Retail"}),
        shuttle(T0 - timedelta(days=1), 0.3, "C", 3),
    )

    p = late.provenance
    assert late.value == "ACME Corp"  # the first value is kept
    assert p.observation_count == 3
    assert p.first_seen == T0 - timedelta(days=1)
    assert p.last_seen == T0 + timedelta(days=1)
    assert p.sources == ["manual", "shuttle:dns@1.0.0"]
    assert p.max_confidence == 0.95
    assert p.admiralty == "A1"  # of the most recent observation
    assert p.note == "from the brief"
    assert p.evidence_refs == ["evidence/abc"]
    assert late.properties == {"country": "BD", "industry": "Retail"}


async def test_source_list_is_bounded(
    graph_repo: TrackingGraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repository, "MAX_LIST_LENGTH", 2)
    case_id = graph_repo.new_case()
    entity = build_entity(E.DOMAIN, "example.org")
    for version in range(4):
        observation = shuttle(T0, 0.5).model_copy(update={"source": f"shuttle:dns@{version}"})
        record = await graph_repo.upsert_entity(case_id, entity, observation)
    assert record.provenance.sources == ["shuttle:dns@0", "shuttle:dns@1"]
    assert record.provenance.observation_count == 4


async def test_cases_are_isolated(graph_repo: TrackingGraphRepository) -> None:
    case_a, case_b = graph_repo.new_case(), graph_repo.new_case()
    a = await add(graph_repo, case_a, E.DOMAIN, "example.org")
    b = await add(graph_repo, case_b, E.DOMAIN, "example.org")
    await add(graph_repo, case_a, E.DOMAIN, "www.example.org")

    assert a.created and b.created  # same key, two cases: two nodes
    assert await count_nodes(graph_repo, case_a) == 2
    assert await count_nodes(graph_repo, case_b) == 1
    assert await graph_repo.get_entity(case_b, "domain:www.example.org") is None

    # Case B cannot connect, delete or read case A's nodes.
    with pytest.raises(EndpointNotFoundError, match="target_key"):
        await graph_repo.upsert_relationship(
            case_b, R.HAS_SUBDOMAIN, "domain:example.org", "domain:www.example.org", manual()
        )
    assert await graph_repo.delete_entity(case_b, "domain:www.example.org") is None
    assert await count_nodes(graph_repo, case_a) == 2

    await graph_repo.delete_entity(case_b, "domain:example.org")
    detail = await graph_repo.get_entity(case_a, "domain:example.org")
    assert detail is not None and detail.entity.provenance.observation_count == 1


# --- relationships ------------------------------------------------------------------------


async def test_relationship_upsert_and_merge(graph_repo: TrackingGraphRepository) -> None:
    case_id = graph_repo.new_case()
    parent = await add(graph_repo, case_id, E.DOMAIN, "example.org")
    child = await add(graph_repo, case_id, E.DOMAIN, "www.example.org")

    first = await graph_repo.upsert_relationship(
        case_id, R.HAS_SUBDOMAIN, parent.key, child.key, manual(T0)
    )
    second = await graph_repo.upsert_relationship(
        case_id, R.HAS_SUBDOMAIN, parent.key, child.key, shuttle(T0 + timedelta(1), 1.0)
    )

    assert first.created and not second.created
    assert second.id == "domain:example.org|HAS_SUBDOMAIN|domain:www.example.org"
    assert second.provenance.observation_count == 2
    assert second.provenance.max_confidence == 1.0
    assert second.source_sensitivity == second.target_sensitivity == Sensitivity.INFRASTRUCTURE
    snapshot = await graph_repo.get_graph(case_id)
    assert [edge.id for edge in snapshot.edges] == [second.id]
    assert snapshot.edges[0].provenance.observation_count == 2
    detail = await graph_repo.get_entity(case_id, parent.key)
    assert detail is not None and detail.neighbours == 1


async def test_relationship_endpoints_must_exist(graph_repo: TrackingGraphRepository) -> None:
    case_id = graph_repo.new_case()
    domain = await add(graph_repo, case_id, E.DOMAIN, "example.org")
    with pytest.raises(EndpointNotFoundError, match="source_key does not name"):
        await graph_repo.upsert_relationship(
            case_id, R.HAS_SUBDOMAIN, "domain:missing.org", domain.key, manual()
        )
    with pytest.raises(EndpointNotFoundError, match="target_key does not name"):
        await graph_repo.upsert_relationship(
            case_id, R.RESOLVES_TO, domain.key, "ip_address:192.0.2.1", manual()
        )
    assert (await graph_repo.get_graph(case_id)).edges == []


async def test_relationship_allow_list(graph_repo: TrackingGraphRepository) -> None:
    case_id = graph_repo.new_case()
    domain = await add(graph_repo, case_id, E.DOMAIN, "example.org")
    ip = await add(graph_repo, case_id, E.IP_ADDRESS, "192.0.2.1")
    org = await add(graph_repo, case_id, E.ORGANIZATION, "ACME")

    with pytest.raises(RelationshipNotAllowedError, match="RESOLVES_TO cannot connect"):
        await graph_repo.upsert_relationship(case_id, R.RESOLVES_TO, ip.key, domain.key, manual())
    with pytest.raises(RelationshipNotAllowedError, match="two different entities"):
        await graph_repo.upsert_relationship(
            case_id, R.HAS_SUBDOMAIN, domain.key, domain.key, manual()
        )
    with pytest.raises(RelationshipNotAllowedError, match="needs a note"):
        await graph_repo.upsert_relationship(case_id, R.RELATED_TO, org.key, ip.key, manual())
    related = await graph_repo.upsert_relationship(
        case_id, R.RELATED_TO, org.key, ip.key, manual(note="Named in the hosting contract.")
    )
    assert related.provenance.note == "Named in the hosting contract."
    assert len((await graph_repo.get_graph(case_id)).edges) == 1


async def test_relationship_person_gate(graph_repo: TrackingGraphRepository) -> None:
    case_id = graph_repo.new_case()
    email = await add(graph_repo, case_id, E.EMAIL, "jane@example.org")
    person = await add(graph_repo, case_id, E.PERSON, "Jane Doe")
    with pytest.raises(PersonEntitiesDisabledError):
        await graph_repo.upsert_relationship(
            case_id, R.ACCOUNT_OF, email.key, person.key, manual(), allow_person_entities=False
        )
    record = await graph_repo.upsert_relationship(
        case_id, R.ACCOUNT_OF, email.key, person.key, manual()
    )
    assert record.source_sensitivity == record.target_sensitivity == Sensitivity.PERSON


# --- reading -------------------------------------------------------------------------------


async def test_graph_limit_types_and_truncation(
    graph_repo: TrackingGraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = graph_repo.new_case()
    parent = await add(graph_repo, case_id, E.DOMAIN, "example.org")
    for name in ("a", "b", "c"):
        child = await add(graph_repo, case_id, E.DOMAIN, f"{name}.example.org")
        await graph_repo.upsert_relationship(
            case_id, R.HAS_SUBDOMAIN, parent.key, child.key, manual()
        )
    ip = await add(graph_repo, case_id, E.IP_ADDRESS, "192.0.2.1")
    await graph_repo.upsert_relationship(case_id, R.RESOLVES_TO, parent.key, ip.key, manual())

    full = await graph_repo.get_graph(case_id)
    assert (len(full.nodes), len(full.edges), full.truncated) == (5, 4, False)
    assert [node.key for node in full.nodes] == sorted(node.key for node in full.nodes)
    exact = await graph_repo.get_graph(case_id, limit=5)
    assert not exact.truncated

    cut = await graph_repo.get_graph(case_id, limit=2)
    assert cut.truncated
    assert [node.key for node in cut.nodes] == ["domain:a.example.org", "domain:b.example.org"]
    assert cut.edges == []  # neither returned node's edge has both ends returned

    only_ips = await graph_repo.get_graph(case_id, types=[E.IP_ADDRESS])
    assert [node.key for node in only_ips.nodes] == ["ip_address:192.0.2.1"]
    assert only_ips.edges == [] and not only_ips.truncated

    monkeypatch.setattr(repository, "EDGES_PER_NODE", 0)
    no_edges = await graph_repo.get_graph(case_id)
    assert no_edges.edges == [] and no_edges.truncated

    assert (await graph_repo.get_graph(graph_repo.new_case())).nodes == []


async def test_entity_properties_round_trip(graph_repo: TrackingGraphRepository) -> None:
    case_id = graph_repo.new_case()
    cert = build_entity(
        E.CERTIFICATE,
        "AB:" * 31 + "AB",
        {"subject": "CN=example.org", "not_after": "2027-01-01T00:00:00Z", "self_signed": False},
    )
    await graph_repo.upsert_entity(case_id, cert, manual())
    detail = await graph_repo.get_entity(case_id, cert.key)
    assert detail is not None
    assert detail.entity.properties == {
        "subject": "CN=example.org",
        "not_after": "2027-01-01T00:00:00Z",
        "self_signed": False,
    }
    assert detail.neighbours == 0


# --- deleting --------------------------------------------------------------------------------


async def test_delete_entity_detaches_it(graph_repo: TrackingGraphRepository) -> None:
    case_id = graph_repo.new_case()
    parent = await add(graph_repo, case_id, E.DOMAIN, "example.org")
    child = await add(graph_repo, case_id, E.DOMAIN, "www.example.org")
    await graph_repo.upsert_relationship(case_id, R.HAS_SUBDOMAIN, parent.key, child.key, manual())

    deleted = await graph_repo.delete_entity(case_id, child.key)

    assert deleted is not None
    assert (deleted.type, deleted.sensitivity) == (E.DOMAIN, Sensitivity.INFRASTRUCTURE)
    snapshot = await graph_repo.get_graph(case_id)
    assert [node.key for node in snapshot.nodes] == [parent.key]
    assert snapshot.edges == []
    assert await graph_repo.delete_entity(case_id, child.key) is None


async def test_delete_case_graph_removes_only_that_case(
    graph_repo: TrackingGraphRepository,
) -> None:
    doomed, kept = graph_repo.new_case(), graph_repo.new_case()
    for name in ("a", "b", "c", "d", "e"):
        await add(graph_repo, doomed, E.DOMAIN, f"{name}.example.org")
    await graph_repo.upsert_relationship(
        doomed, R.HAS_SUBDOMAIN, "domain:a.example.org", "domain:b.example.org", manual()
    )
    await add(graph_repo, kept, E.DOMAIN, "a.example.org")

    assert await graph_repo.delete_case_graph(doomed, batch_size=2) == 5
    assert await count_nodes(graph_repo, doomed) == 0
    assert await count_nodes(graph_repo, kept) == 1
    assert await graph_repo.delete_case_graph(doomed) == 0

    async with graph_repo._driver.session() as session:
        result = await session.run(
            "MATCH (n:Entity {case_id: $case_id}) RETURN count(n) AS n", case_id=str(doomed)
        )
        assert (await result.single(strict=True))["n"] == 0


# --- safety ---------------------------------------------------------------------------------

INJECTIONS = [
    "x'}) MATCH (n) DETACH DELETE n //",
    'x"}) MATCH (n) DETACH DELETE n //',
    "x` DETACH DELETE n //",
    "$case_id",
    "} RETURN 1 UNION MATCH (n) RETURN n //",
]


@pytest.mark.parametrize("payload", INJECTIONS)
async def test_cypher_injection_is_inert(graph_repo: TrackingGraphRepository, payload: str) -> None:
    case_id, bystander = graph_repo.new_case(), graph_repo.new_case()
    await add(graph_repo, bystander, E.DOMAIN, "example.org")

    org = await add(graph_repo, case_id, E.ORGANIZATION, payload)

    assert org.value == payload
    assert org.key == "organization:" + payload.casefold()
    detail = await graph_repo.get_entity(case_id, org.key)
    assert detail is not None and detail.entity.value == payload
    # Keys are parameters too: a hostile key matches nothing and changes nothing.
    assert await graph_repo.get_entity(case_id, payload) is None
    assert await graph_repo.delete_entity(case_id, payload) is None
    assert await count_nodes(graph_repo, case_id) == 1
    assert await count_nodes(graph_repo, bystander) == 1


def test_relationship_types_come_only_from_the_enum() -> None:
    with pytest.raises(ValueError):
        RelationType("RESOLVES_TO]->() DETACH DELETE n //")


async def test_failed_commit_raises_graph_commit_error(
    graph_repo: TrackingGraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = graph_repo.new_case()

    async def broken_commit(self: AsyncTransaction) -> None:
        raise DriverError("connection lost")

    monkeypatch.setattr(AsyncTransaction, "commit", broken_commit)
    with pytest.raises(GraphCommitError):
        await add(graph_repo, case_id, E.DOMAIN, "example.org")
    monkeypatch.undo()
    assert await count_nodes(graph_repo, case_id) == 0


async def test_errors_inside_a_write_roll_it_back(graph_repo: TrackingGraphRepository) -> None:
    case_id = graph_repo.new_case()
    with pytest.raises(RuntimeError):
        async with graph_repo.write() as ops:
            await ops.upsert_entity(case_id, build_entity(E.DOMAIN, "example.org"), manual())
            raise RuntimeError("caller failed")
    assert await count_nodes(graph_repo, case_id) == 0


async def test_unreachable_neo4j_raises_driver_errors(
    closed_port: int, settings_for_port: object
) -> None:
    driver = graph.create_driver(settings_for_port(closed_port))  # type: ignore[operator]
    try:
        with pytest.raises(DriverError):
            await GraphRepository(driver).get_graph(uuid.uuid4())
    finally:
        await driver.close()
