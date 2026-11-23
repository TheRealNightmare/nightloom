"""Reading and writing one case's graph.

Every query is parameterized: values, keys, case IDs, labels and relationship
types are all passed as parameters (labels and types through Cypher's
dynamic ``$(...)`` syntax, and only after they were checked against
:class:`~nightloom_core.entities.EntityType` and
:class:`~nightloom_core.relationships.RelationType`). No user-supplied text
is ever formatted into a query.

Every query is scoped to one case: nodes are matched on ``case_id`` as well
as ``key``, and relationships are only created between two nodes of the same
case, so one case can never read or change another's graph.

Writes run in explicit transactions (:meth:`GraphRepository.write`), so a
caller can record its audit event in PostgreSQL before the graph change
commits. They are not retried: a request should fail fast with 503 rather
than hang while Neo4j is down.
"""

import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, LiteralString

from neo4j import AsyncDriver, AsyncTransaction
from neo4j.time import DateTime as Neo4jDateTime

from nightloom_core.entities import EntityBase, EntityType, Sensitivity, label_of
from nightloom_core.provenance import Observation
from nightloom_core.relationships import RelationshipError, RelationType, validate_relationship

# Distinct sources and evidence references kept per node or relationship.
MAX_LIST_LENGTH = 100
# Prefix of stored entity properties, so they never clash with the fixed ones.
PROPERTY_PREFIX = "prop_"
DEFAULT_GRAPH_LIMIT = 2000
# Edges returned with a graph, per node limit.
EDGES_PER_NODE = 5
DELETE_BATCH_SIZE = 1000
# Server-side limit for one transaction (seconds).
TRANSACTION_TIMEOUT = 30.0


class GraphError(Exception):
    """Base class; ``str(error)`` is safe to return to the client."""


class EndpointNotFoundError(GraphError):
    """A relationship names an entity that does not exist in the case."""


class RelationshipNotAllowedError(GraphError):
    """The relationship is not in the allow-list (or lacks a required note)."""


class PersonEntitiesDisabledError(GraphError):
    """A relationship would touch a person-related entity in a case that does not allow them."""


class GraphCommitError(GraphError):
    """The graph transaction failed to commit after the caller's own work was done."""


# --- results ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Provenance:
    """The provenance summary every node and relationship carries."""

    first_seen: datetime
    last_seen: datetime
    observation_count: int
    sources: list[str]
    max_confidence: float
    admiralty: str | None
    evidence_refs: list[str]
    note: str | None


@dataclass(frozen=True)
class EntityRecord:
    key: str
    type: EntityType
    value: str
    sensitivity: Sensitivity
    provenance: Provenance
    properties: dict[str, Any] = field(default_factory=dict)
    # Set by upserts: True when this call created the node.
    created: bool = False


@dataclass(frozen=True)
class RelationshipRecord:
    type: RelationType
    source_key: str
    target_key: str
    provenance: Provenance
    created: bool = False
    # The endpoints' sensitivities, so callers can decide what to log.
    source_sensitivity: Sensitivity | None = None
    target_sensitivity: Sensitivity | None = None

    @property
    def id(self) -> str:
        """Stable within a case: there is at most one relationship per type and direction."""
        return f"{self.source_key}|{self.type.value}|{self.target_key}"


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: list[EntityRecord]
    edges: list[RelationshipRecord]
    truncated: bool


@dataclass(frozen=True)
class EntityDetail:
    entity: EntityRecord
    neighbours: int


@dataclass(frozen=True)
class GraphCounts:
    entities: int
    person_entities: int
    relationships: int


@dataclass(frozen=True)
class Screenshot:
    """An entity with a screenshot: its perceptual hash and evidence name."""

    key: str
    type: EntityType
    value: str
    phash: str
    screenshot: str | None


@dataclass(frozen=True)
class DeletedEntity:
    type: EntityType
    sensitivity: Sensitivity


def _native(value: Any) -> Any:
    return value.to_native() if isinstance(value, Neo4jDateTime) else value


def _provenance(props: Mapping[str, Any]) -> Provenance:
    return Provenance(
        first_seen=_native(props["first_seen"]),
        last_seen=_native(props["last_seen"]),
        observation_count=int(props["observation_count"]),
        sources=list(props.get("sources") or []),
        max_confidence=float(props["max_confidence"]),
        admiralty=props.get("last_admiralty"),
        evidence_refs=list(props.get("evidence_refs") or []),
        note=props.get("note"),
    )


def _entity(props: Mapping[str, Any], *, created: bool = False) -> EntityRecord:
    return EntityRecord(
        key=props["key"],
        type=EntityType(props["type"]),
        value=props["value"],
        sensitivity=Sensitivity(props["sensitivity"]),
        provenance=_provenance(props),
        properties={
            name.removeprefix(PROPERTY_PREFIX): value
            for name, value in props.items()
            if name.startswith(PROPERTY_PREFIX)
        },
        created=created,
    )


def _relationship(
    rel_type: str, source: str, target: str, props: Mapping[str, Any], **extra: Any
) -> RelationshipRecord:
    return RelationshipRecord(
        type=RelationType(rel_type),
        source_key=source,
        target_key=target,
        provenance=_provenance(props),
        **extra,
    )


def _observation_params(observation: Observation) -> dict[str, Any]:
    return {
        "observed_at": observation.observed_at,
        "source": observation.source,
        "confidence": observation.confidence,
        "admiralty": observation.admiralty,
        "evidence_ref": observation.evidence_ref,
        "note": observation.note,
        "max_list": MAX_LIST_LENGTH,
    }


# The provenance update shared by nodes and relationships (`x`), as an
# observation is merged in. The admiralty and note follow the most recent
# observation, so they are set before last_seen moves.
_MERGE_PROVENANCE: LiteralString = """
SET x.last_admiralty = CASE WHEN $observed_at >= x.last_seen
                            THEN $admiralty ELSE x.last_admiralty END,
    x.note = CASE WHEN $note IS NOT NULL AND $observed_at >= x.last_seen
                  THEN $note ELSE x.note END
SET x.first_seen = CASE WHEN $observed_at < x.first_seen THEN $observed_at ELSE x.first_seen END,
    x.last_seen = CASE WHEN $observed_at > x.last_seen THEN $observed_at ELSE x.last_seen END,
    x.observation_count = x.observation_count + 1,
    x.sources = CASE WHEN $source IN x.sources OR size(x.sources) >= $max_list
                     THEN x.sources ELSE x.sources + $source END,
    x.evidence_refs = CASE
        WHEN $evidence_ref IS NULL OR $evidence_ref IN x.evidence_refs
             OR size(x.evidence_refs) >= $max_list
        THEN x.evidence_refs ELSE x.evidence_refs + $evidence_ref END,
    x.max_confidence = CASE WHEN $confidence > x.max_confidence
                            THEN $confidence ELSE x.max_confidence END
"""

_NEW_PROVENANCE: LiteralString = """
    x.first_seen = $observed_at, x.last_seen = $observed_at, x.observation_count = 0,
    x.sources = [], x.evidence_refs = [], x.max_confidence = $confidence
"""

_UPSERT_ENTITY: LiteralString = (
    """
MERGE (x:Entity {case_id: $case_id, key: $key})
ON CREATE SET x.type = $type, x.value = $value, x.sensitivity = $sensitivity,"""
    + _NEW_PROVENANCE
    + """
SET x:$($label)"""
    + _MERGE_PROVENANCE
    + """
SET x += $properties
RETURN x {.*} AS node
"""
)

_ENDPOINTS: LiteralString = """
MATCH (n:Entity {case_id: $case_id})
WHERE n.key IN [$source_key, $target_key]
RETURN n.key AS key, n.type AS type, n.sensitivity AS sensitivity
"""

_UPSERT_RELATIONSHIP: LiteralString = (
    """
MATCH (s:Entity {case_id: $case_id, key: $source_key})
MATCH (t:Entity {case_id: $case_id, key: $target_key})
MERGE (s)-[x:$($rel_type)]->(t)
ON CREATE SET x.case_id = $case_id,"""
    + _NEW_PROVENANCE
    + _MERGE_PROVENANCE
    + """
RETURN x {.*} AS rel
"""
)

_GRAPH_NODES: LiteralString = """
MATCH (n:Entity {case_id: $case_id})
WHERE $types IS NULL OR n.type IN $types
RETURN n {.*} AS node
ORDER BY n.key
LIMIT $limit
"""

_GRAPH_EDGES: LiteralString = """
MATCH (s:Entity {case_id: $case_id})-[r]->(t:Entity {case_id: $case_id})
WHERE s.key IN $keys AND t.key IN $keys
RETURN type(r) AS type, s.key AS source, t.key AS target, r {.*} AS rel
ORDER BY source, type, target
LIMIT $limit
"""

_GET_ENTITY: LiteralString = """
MATCH (n:Entity {case_id: $case_id, key: $key})
OPTIONAL MATCH (n)--(m:Entity)
RETURN n {.*} AS node, count(DISTINCT m) AS neighbours
"""

_DELETE_ENTITY: LiteralString = """
MATCH (n:Entity {case_id: $case_id, key: $key})
WITH n, n.type AS type, n.sensitivity AS sensitivity
DETACH DELETE n
RETURN type, sensitivity
"""

_COUNT_GRAPH: LiteralString = """
MATCH (n:Entity {case_id: $case_id})
WITH count(n) AS entities, count(CASE WHEN n.sensitivity = 'person' THEN 1 END) AS person
CALL (*) {
  MATCH (:Entity {case_id: $case_id})-[r]->(:Entity {case_id: $case_id})
  RETURN count(r) AS relationships
}
RETURN entities, person, relationships
"""

# `$person`: null for every entity, true for person-related ones only, false for the others.
_EXPORT_ENTITIES: LiteralString = """
MATCH (n:Entity {case_id: $case_id})
WHERE $person IS NULL OR (n.sensitivity = 'person') = $person
RETURN n {.*} AS node
ORDER BY n.key
"""

# `$person`: null for every relationship, true for those touching a person-related
# entity, false for the others.
_EXPORT_RELATIONSHIPS: LiteralString = """
MATCH (s:Entity {case_id: $case_id})-[r]->(t:Entity {case_id: $case_id})
WITH s, r, t, (s.sensitivity = 'person' OR t.sensitivity = 'person') AS personal
WHERE $person IS NULL OR personal = $person
RETURN type(r) AS type, s.key AS source, t.key AS target,
       s.sensitivity AS source_sensitivity, t.sensitivity AS target_sensitivity, r {.*} AS rel
ORDER BY source, type, target
"""

_EXISTING_KEYS: LiteralString = """
UNWIND $keys AS key
MATCH (n:Entity {case_id: $case_id, key: key})
RETURN n.key AS key
"""

_SCREENSHOTS: LiteralString = """
MATCH (n:Entity {case_id: $case_id})
WHERE n.prop_screenshot_phash IS NOT NULL
RETURN n.key AS key, n.type AS type, n.value AS value,
       n.prop_screenshot_phash AS phash, n.prop_screenshot AS screenshot
ORDER BY n.key
LIMIT $limit
"""

_DELETE_CASE_BATCH: LiteralString = """
MATCH (n:Entity {case_id: $case_id})
WITH n LIMIT $batch
DETACH DELETE n
RETURN count(*) AS deleted
"""


def _case(case_id: uuid.UUID | str) -> str:
    return str(uuid.UUID(str(case_id)))


class GraphOperations:
    """Graph reads and writes inside one transaction."""

    def __init__(self, tx: AsyncTransaction) -> None:
        self._tx = tx

    async def upsert_entity(
        self, case_id: uuid.UUID | str, entity: EntityBase, observation: Observation
    ) -> EntityRecord:
        """Create the entity's node or merge the observation into the existing one.

        The first observation sets the stored value; later ones extend the
        provenance (first/last seen, count, sources, best confidence) and
        overwrite the properties they carry.
        """
        result = await self._tx.run(
            _UPSERT_ENTITY,
            case_id=_case(case_id),
            key=entity.key,
            type=entity.entity_type.value,
            label=label_of(entity.entity_type),
            value=entity.value,
            sensitivity=entity.sensitivity.value,
            properties={
                PROPERTY_PREFIX + name: value for name, value in entity.stored_properties().items()
            },
            **_observation_params(observation),
        )
        record = await result.single(strict=True)
        node = record["node"]
        return _entity(node, created=node["observation_count"] == 1)

    async def upsert_relationship(
        self,
        case_id: uuid.UUID | str,
        rel_type: RelationType,
        source_key: str,
        target_key: str,
        observation: Observation,
        *,
        allow_person_entities: bool = True,
    ) -> RelationshipRecord:
        """Create the relationship or merge the observation into it.

        Both endpoints must exist in this case (:class:`EndpointNotFoundError`),
        the pair of types must be allowed (:class:`RelationshipNotAllowedError`)
        and, unless ``allow_person_entities``, neither endpoint may be
        person-related (:class:`PersonEntitiesDisabledError`).
        """
        rel_type = RelationType(rel_type)
        if source_key == target_key:
            raise RelationshipNotAllowedError("A relationship needs two different entities.")
        case = _case(case_id)
        result = await self._tx.run(
            _ENDPOINTS, case_id=case, source_key=source_key, target_key=target_key
        )
        found = {record["key"]: record async for record in result}
        for name, key in (("source_key", source_key), ("target_key", target_key)):
            if key not in found:
                raise EndpointNotFoundError(f"{name} does not name an entity in this case.")
        source, target = found[source_key], found[target_key]
        sensitivities = (Sensitivity(source["sensitivity"]), Sensitivity(target["sensitivity"]))
        if not allow_person_entities and Sensitivity.PERSON in sensitivities:
            raise PersonEntitiesDisabledError("This case does not allow person-related entities.")
        try:
            validate_relationship(
                rel_type, EntityType(source["type"]), EntityType(target["type"]), observation.note
            )
        except RelationshipError as exc:
            raise RelationshipNotAllowedError(str(exc)) from exc

        result = await self._tx.run(
            _UPSERT_RELATIONSHIP,
            case_id=case,
            source_key=source_key,
            target_key=target_key,
            rel_type=rel_type.value,
            **_observation_params(observation),
        )
        rel = (await result.single(strict=True))["rel"]
        return _relationship(
            rel_type.value,
            source_key,
            target_key,
            rel,
            created=rel["observation_count"] == 1,
            source_sensitivity=sensitivities[0],
            target_sensitivity=sensitivities[1],
        )

    async def get_graph(
        self,
        case_id: uuid.UUID | str,
        types: Sequence[EntityType] | None = None,
        limit: int = DEFAULT_GRAPH_LIMIT,
    ) -> GraphSnapshot:
        """Up to ``limit`` nodes (by key) and the edges between them (up to 5 per node).

        ``truncated`` is set when either limit cut something off.
        """
        case = _case(case_id)
        type_values = [EntityType(t).value for t in types] if types else None
        result = await self._tx.run(_GRAPH_NODES, case_id=case, types=type_values, limit=limit + 1)
        nodes = [_entity(record["node"]) async for record in result]
        truncated = len(nodes) > limit
        nodes = nodes[:limit]
        edges: list[RelationshipRecord] = []
        if nodes:
            edge_limit = limit * EDGES_PER_NODE
            result = await self._tx.run(
                _GRAPH_EDGES,
                case_id=case,
                keys=[node.key for node in nodes],
                limit=edge_limit + 1,
            )
            edges = [
                _relationship(record["type"], record["source"], record["target"], record["rel"])
                async for record in result
            ]
            truncated = truncated or len(edges) > edge_limit
            edges = edges[:edge_limit]
        return GraphSnapshot(nodes=nodes, edges=edges, truncated=truncated)

    async def get_entity(self, case_id: uuid.UUID | str, key: str) -> EntityDetail | None:
        result = await self._tx.run(_GET_ENTITY, case_id=_case(case_id), key=key)
        record = await result.single()
        if record is None or record["node"] is None:
            return None
        return EntityDetail(entity=_entity(record["node"]), neighbours=int(record["neighbours"]))

    async def delete_entity(self, case_id: uuid.UUID | str, key: str) -> DeletedEntity | None:
        """Delete the node and its relationships; ``None`` if there was no such node."""
        result = await self._tx.run(_DELETE_ENTITY, case_id=_case(case_id), key=key)
        record = await result.single()
        if record is None:
            return None
        return DeletedEntity(
            type=EntityType(record["type"]), sensitivity=Sensitivity(record["sensitivity"])
        )

    async def count_graph(self, case_id: uuid.UUID | str) -> GraphCounts:
        """How many entities (and person-related ones) and relationships the case has."""
        result = await self._tx.run(_COUNT_GRAPH, case_id=_case(case_id))
        record = await result.single(strict=True)
        return GraphCounts(
            entities=int(record["entities"]),
            person_entities=int(record["person"]),
            relationships=int(record["relationships"]),
        )

    async def iter_entities(
        self, case_id: uuid.UUID | str, *, person: bool | None = None
    ) -> AsyncIterator[EntityRecord]:
        """Every entity of the case, by key, streamed from Neo4j.

        ``person`` keeps only person-related entities (``True``) or only the
        others (``False``). The result must be consumed before the next query
        in the same transaction.
        """
        result = await self._tx.run(_EXPORT_ENTITIES, case_id=_case(case_id), person=person)
        async for record in result:
            yield _entity(record["node"])

    async def iter_relationships(
        self, case_id: uuid.UUID | str, *, person: bool | None = None
    ) -> AsyncIterator[RelationshipRecord]:
        """Every relationship of the case (by source, type, target), streamed.

        ``person`` keeps only relationships touching a person-related entity
        (``True``) or only the others (``False``). Records carry both
        endpoints' sensitivities.
        """
        result = await self._tx.run(_EXPORT_RELATIONSHIPS, case_id=_case(case_id), person=person)
        async for record in result:
            yield _relationship(
                record["type"],
                record["source"],
                record["target"],
                record["rel"],
                source_sensitivity=Sensitivity(record["source_sensitivity"]),
                target_sensitivity=Sensitivity(record["target_sensitivity"]),
            )

    async def existing_keys(self, case_id: uuid.UUID | str, keys: Sequence[str]) -> set[str]:
        """Those of ``keys`` that are entities of the case."""
        if not keys:
            return set()
        result = await self._tx.run(_EXISTING_KEYS, case_id=_case(case_id), keys=list(keys))
        return {record["key"] async for record in result}

    async def screenshots(self, case_id: uuid.UUID | str, limit: int) -> list[Screenshot]:
        """The case's entities with a screenshot's perceptual hash (at most ``limit``), by key."""
        result = await self._tx.run(_SCREENSHOTS, case_id=_case(case_id), limit=limit)
        return [
            Screenshot(
                key=record["key"],
                type=EntityType(record["type"]),
                value=record["value"],
                phash=str(record["phash"]),
                screenshot=record["screenshot"],
            )
            async for record in result
        ]

    async def delete_case_batch(self, case_id: uuid.UUID | str, batch_size: int) -> int:
        """Delete up to ``batch_size`` of the case's nodes; returns how many were deleted."""
        result = await self._tx.run(_DELETE_CASE_BATCH, case_id=_case(case_id), batch=batch_size)
        return int((await result.single(strict=True))["deleted"])


class GraphRepository:
    """Case graphs on one Neo4j driver."""

    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    @asynccontextmanager
    async def write(self) -> AsyncIterator[GraphOperations]:
        """One write transaction: committed when the block ends, rolled back if it raises.

        A failed commit raises :class:`GraphCommitError`, so the caller knows
        its own work (e.g. an audit event) is already done but the graph
        change is not.
        """
        async with self._driver.session() as session:
            tx = await session.begin_transaction(timeout=TRANSACTION_TIMEOUT)
            try:
                yield GraphOperations(tx)
            except BaseException:
                # Not awaited: this also runs on cancellation. The session then
                # drops the connection instead of reusing it mid-transaction.
                tx.cancel()
                raise
            try:
                await tx.commit()
            except Exception as exc:
                raise GraphCommitError("The graph change could not be saved.") from exc

    @asynccontextmanager
    async def read(
        self, *, tx_timeout: float = TRANSACTION_TIMEOUT
    ) -> AsyncIterator[GraphOperations]:
        """One read transaction; ``tx_timeout`` is Neo4j's server-side limit in seconds."""
        async with self._driver.session() as session:
            tx = await session.begin_transaction(timeout=tx_timeout)
            try:
                yield GraphOperations(tx)
            finally:
                await tx.close()

    # Single-operation conveniences, each in its own transaction.

    async def upsert_entity(
        self, case_id: uuid.UUID | str, entity: EntityBase, observation: Observation
    ) -> EntityRecord:
        async with self.write() as ops:
            return await ops.upsert_entity(case_id, entity, observation)

    async def upsert_relationship(
        self,
        case_id: uuid.UUID | str,
        rel_type: RelationType,
        source_key: str,
        target_key: str,
        observation: Observation,
        *,
        allow_person_entities: bool = True,
    ) -> RelationshipRecord:
        async with self.write() as ops:
            return await ops.upsert_relationship(
                case_id,
                rel_type,
                source_key,
                target_key,
                observation,
                allow_person_entities=allow_person_entities,
            )

    async def get_graph(
        self,
        case_id: uuid.UUID | str,
        types: Sequence[EntityType] | None = None,
        limit: int = DEFAULT_GRAPH_LIMIT,
    ) -> GraphSnapshot:
        async with self.read() as ops:
            return await ops.get_graph(case_id, types, limit)

    async def get_entity(self, case_id: uuid.UUID | str, key: str) -> EntityDetail | None:
        async with self.read() as ops:
            return await ops.get_entity(case_id, key)

    async def existing_keys(self, case_id: uuid.UUID | str, keys: Sequence[str]) -> set[str]:
        async with self.read() as ops:
            return await ops.existing_keys(case_id, keys)

    async def count_graph(self, case_id: uuid.UUID | str) -> GraphCounts:
        async with self.read() as ops:
            return await ops.count_graph(case_id)

    async def screenshots(self, case_id: uuid.UUID | str, limit: int) -> list[Screenshot]:
        async with self.read() as ops:
            return await ops.screenshots(case_id, limit)

    async def delete_entity(self, case_id: uuid.UUID | str, key: str) -> DeletedEntity | None:
        async with self.write() as ops:
            return await ops.delete_entity(case_id, key)

    async def delete_case_graph(
        self, case_id: uuid.UUID | str, *, batch_size: int = DELETE_BATCH_SIZE
    ) -> int:
        """Delete every node (and relationship) of the case, ``batch_size`` nodes per transaction.

        Batches keep each transaction's memory bounded on large graphs.
        Returns the number of nodes deleted.
        """
        case = _case(case_id)
        total = 0
        while True:
            async with self.write() as ops:
                deleted = await ops.delete_case_batch(case, batch_size)
            total += deleted
            if deleted < batch_size:
                return total
