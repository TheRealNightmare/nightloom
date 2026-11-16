import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";

import {
  ENTITY_TYPE_INFO,
  type EntityNode,
  type EntityType,
  type InvestigationGraph,
  type RelationEdge,
} from "../domain/entities";

export interface NodeAttributes {
  label: string;
  entityType: EntityType;
  x: number;
  y: number;
  size: number;
  color: string;
  entity: EntityNode;
}

export interface EdgeAttributes {
  label: string;
  size: number;
  color: string;
  relation: RelationEdge;
}

export type NightloomGraph = Graph<NodeAttributes, EdgeAttributes>;

export const EDGE_COLOR = "#3a4568";

/** Iteration cap for the synchronous layout: the UI must never block for long. */
export const MAX_LAYOUT_ITERATIONS = 500;

export interface BuildGraphOptions {
  /** ForceAtlas2 iterations, clamped to [0, MAX_LAYOUT_ITERATIONS]. */
  layoutIterations?: number;
}

/**
 * Builds the graphology graph that sigma renders, then lays it out.
 *
 * Starting positions are deterministic (a circle, grouped by entity type) and
 * ForceAtlas2 runs for a bounded number of iterations, so the same data always
 * produces the same picture and layout time stays bounded.
 */
export function buildGraph(
  data: InvestigationGraph,
  { layoutIterations = 300 }: BuildGraphOptions = {},
): NightloomGraph {
  const graph: NightloomGraph = new Graph({ type: "directed", multi: false });

  const ordered = [...data.nodes].sort(
    (a, b) => a.type.localeCompare(b.type) || a.id.localeCompare(b.id),
  );
  ordered.forEach((entity, index) => {
    const angle = (2 * Math.PI * index) / Math.max(ordered.length, 1);
    graph.addNode(entity.id, {
      label: entity.label,
      entityType: entity.type,
      x: Math.cos(angle) * 100,
      y: Math.sin(angle) * 100,
      size: 6,
      color: ENTITY_TYPE_INFO[entity.type].color,
      entity,
    });
  });

  for (const relation of data.edges) {
    graph.addDirectedEdgeWithKey(relation.id, relation.from, relation.to, {
      label: relation.relation.replaceAll("_", " "),
      size: 1,
      color: EDGE_COLOR,
      relation,
    });
  }

  // Better-connected entities are drawn larger.
  graph.forEachNode((node) => {
    graph.setNodeAttribute(node, "size", 5 + Math.sqrt(graph.degree(node)) * 2.4);
  });

  const iterations = Math.min(Math.max(Math.floor(layoutIterations), 0), MAX_LAYOUT_ITERATIONS);
  if (iterations > 0 && graph.order > 1) {
    forceAtlas2.assign(graph, {
      iterations,
      settings: {
        ...forceAtlas2.inferSettings(graph),
        adjustSizes: true,
        barnesHutOptimize: false,
      },
    });
  }
  return graph;
}
