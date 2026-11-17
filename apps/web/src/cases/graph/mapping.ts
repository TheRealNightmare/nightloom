/**
 * API graph → the shapes the shared graph view renders. The view only knows
 * {@link EntityNode} and {@link RelationEdge}; case data adds its sensitivity
 * and the full provenance summary as `details`.
 */
import type { ApiEntity, ApiGraph, ApiProvenance, ApiRelationship } from "../../api/graph";
import type {
  EntityNode,
  InvestigationGraph,
  Provenance,
  ProvenanceDetails,
  RelationEdge,
} from "../../domain/entities";

function provenanceOf(provenance: ApiProvenance): Provenance & { details: ProvenanceDetails } {
  return {
    source: provenance.sources.join(", "),
    first_seen: provenance.first_seen,
    last_seen: provenance.last_seen,
    confidence: provenance.max_confidence,
    details: {
      sources: provenance.sources,
      observation_count: provenance.observation_count,
      admiralty: provenance.admiralty,
      note: provenance.note,
      evidence_refs: provenance.evidence_refs,
    },
  };
}

export function toEntityNode(entity: ApiEntity): EntityNode {
  return {
    id: entity.key,
    label: entity.value,
    type: entity.type,
    sensitivity: entity.sensitivity,
    properties: entity.properties,
    ...provenanceOf(entity.provenance),
  };
}

export function toRelationEdge(relationship: ApiRelationship): RelationEdge {
  return {
    id: relationship.id,
    from: relationship.source_key,
    to: relationship.target_key,
    relation: relationship.type,
    ...provenanceOf(relationship.provenance),
  };
}

export function toInvestigationGraph(graph: ApiGraph): InvestigationGraph {
  return { nodes: graph.nodes.map(toEntityNode), edges: graph.edges.map(toRelationEdge) };
}

/** Adds or replaces (same key) an entity, keeping every other object as it was. */
export function withEntity(data: InvestigationGraph, entity: EntityNode): InvestigationGraph {
  const index = data.nodes.findIndex((node) => node.id === entity.id);
  const nodes =
    index === -1 ? [...data.nodes, entity] : data.nodes.map((n, i) => (i === index ? entity : n));
  return { nodes, edges: data.edges };
}

/** Removes an entity and every relationship touching it. */
export function withoutEntity(data: InvestigationGraph, id: string): InvestigationGraph {
  return {
    nodes: data.nodes.filter((node) => node.id !== id),
    edges: data.edges.filter((edge) => edge.from !== id && edge.to !== id),
  };
}

/** Adds or replaces (same id) a relationship. */
export function withRelationship(data: InvestigationGraph, edge: RelationEdge): InvestigationGraph {
  const index = data.edges.findIndex((existing) => existing.id === edge.id);
  const edges =
    index === -1 ? [...data.edges, edge] : data.edges.map((e, i) => (i === index ? edge : e));
  return { nodes: data.nodes, edges };
}
