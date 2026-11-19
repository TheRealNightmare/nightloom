import Graph from "graphology";

import {
  relationLabel,
  type EntityNode,
  type EntityType,
  type InvestigationGraph,
  type RelationEdge,
} from "../domain/entities";
import { DEFAULT_GRAPH_PALETTE, type GraphPalette } from "../theme/graphPalette";
import { baseKnotSize, isUncertain } from "./encoding";
import { FULL_LAYOUT_ITERATIONS, layoutSync } from "./layout";

export { MAX_LAYOUT_ITERATIONS } from "./layout";

export interface NodeAttributes {
  label: string;
  entityType: EntityType;
  x: number;
  y: number;
  size: number;
  color: string;
  entity: EntityNode;
  /** Set only while a layout run keeps the node in place. */
  fixed?: boolean;
  /** Kept in place by the user (drawn with a brass ring; set by the interactions). */
  pinned?: boolean;
}

export interface EdgeAttributes {
  label: string;
  size: number;
  color: string;
  relation: RelationEdge;
  /** Low confidence or not confirmed: drawn as a faint, thin thread (see ./encoding). */
  uncertain: boolean;
}

export type NightloomGraph = Graph<NodeAttributes, EdgeAttributes>;

/** Radius of the starting circle of a freshly built graph. */
export const START_RADIUS = 100;

export function createGraph(): NightloomGraph {
  // Multi: two entities can be linked by several relationship types (HAS_MX and HAS_NS).
  return new Graph<NodeAttributes, EdgeAttributes>({ type: "directed", multi: true });
}

/** The node's color: its type's (the knot's ring and center; see ./encoding). */
function colorOf(entity: EntityNode, palette: GraphPalette): string {
  return palette.types[entity.type];
}

/** A stable number in [0, 1) for a string (FNV-1a), for deterministic placement. */
export function unitHash(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) / 2 ** 32;
}

export interface SyncResult {
  /** Nodes that were not in the graph before. */
  addedNodes: string[];
  /** Edges that were not in the graph before. */
  addedEdges: string[];
  removedNodes: number;
  removedEdges: number;
  /** Anything at all changed (including attributes). */
  changed: boolean;
}

function edgeAttributes(relation: RelationEdge, palette: GraphPalette): EdgeAttributes {
  return {
    label: relationLabel(relation.relation),
    size: 1,
    color: palette.edge,
    relation,
    uncertain: isUncertain(relation),
  };
}

/**
 * Repaints every node and edge in `palette` (after the theme changed); only
 * colors change, never positions or sizes.
 */
export function recolorGraph(graph: NightloomGraph, palette: GraphPalette): void {
  graph.updateEachNodeAttributes((_node, attributes) => {
    const color = colorOf(attributes.entity, palette);
    return attributes.color === color ? attributes : { ...attributes, color };
  });
  graph.updateEachEdgeAttributes((_edge, attributes) =>
    attributes.color === palette.edge ? attributes : { ...attributes, color: palette.edge },
  );
}

/** Places a new node: at the centre of its already placed neighbours, else outside the rest. */
function placeNear(graph: NightloomGraph, node: string, placed: ReadonlySet<string>): void {
  let x = 0;
  let y = 0;
  let count = 0;
  graph.forEachNeighbor(node, (neighbor, attributes) => {
    if (!placed.has(neighbor)) return;
    x += attributes.x;
    y += attributes.y;
    count += 1;
  });
  const angle = unitHash(node) * 2 * Math.PI;
  if (count > 0) {
    // A little away from the centre, so two new leaves of one node do not overlap.
    const offset = 12 + 10 * unitHash(`${node}#r`);
    graph.mergeNodeAttributes(node, {
      x: x / count + Math.cos(angle) * offset,
      y: y / count + Math.sin(angle) * offset,
    });
    return;
  }
  // No placed neighbour: just outside the current extent of the graph.
  let radius = 0;
  let cx = 0;
  let cy = 0;
  for (const other of placed) {
    cx += graph.getNodeAttribute(other, "x");
    cy += graph.getNodeAttribute(other, "y");
  }
  if (placed.size > 0) {
    cx /= placed.size;
    cy /= placed.size;
    for (const other of placed) {
      const attributes = graph.getNodeAttributes(other);
      radius = Math.max(radius, Math.hypot(attributes.x - cx, attributes.y - cy));
    }
  }
  const distance = radius + 20;
  graph.mergeNodeAttributes(node, {
    x: cx + Math.cos(angle) * distance,
    y: cy + Math.sin(angle) * distance,
  });
}

/**
 * Makes `graph` match `data` without rebuilding it: removes what is gone,
 * updates what changed, adds what is new. Into an empty graph, nodes start
 * on a circle grouped by type (deterministic); later additions are placed
 * next to their neighbours, so the existing picture stays where it was.
 */
export function syncGraph(
  graph: NightloomGraph,
  data: InvestigationGraph,
  palette: GraphPalette = DEFAULT_GRAPH_PALETTE,
): SyncResult {
  const nodeIds = new Set(data.nodes.map((node) => node.id));
  const edgeIds = new Set<string>();
  for (const edge of data.edges) {
    if (nodeIds.has(edge.from) && nodeIds.has(edge.to) && edge.from !== edge.to) {
      edgeIds.add(edge.id);
    }
  }
  const result: SyncResult = {
    addedNodes: [],
    addedEdges: [],
    removedNodes: 0,
    removedEdges: 0,
    changed: false,
  };

  for (const edge of graph.edges()) {
    if (!edgeIds.has(edge)) {
      graph.dropEdge(edge);
      result.removedEdges += 1;
    }
  }
  for (const node of graph.nodes()) {
    if (!nodeIds.has(node)) {
      graph.dropNode(node);
      result.removedNodes += 1;
    }
  }

  const wasEmpty = graph.order === 0;
  const ordered = wasEmpty
    ? [...data.nodes].sort((a, b) => a.type.localeCompare(b.type) || a.id.localeCompare(b.id))
    : data.nodes;
  ordered.forEach((entity, index) => {
    if (graph.hasNode(entity.id)) {
      if (graph.getNodeAttribute(entity.id, "entity") !== entity) {
        graph.mergeNodeAttributes(entity.id, {
          entity,
          label: entity.label,
          entityType: entity.type,
          color: colorOf(entity, palette),
          size: baseKnotSize(entity, graph.degree(entity.id)),
        });
        result.changed = true;
      }
      return;
    }
    const angle = (2 * Math.PI * index) / Math.max(ordered.length, 1);
    graph.addNode(entity.id, {
      label: entity.label,
      entityType: entity.type,
      x: Math.cos(angle) * START_RADIUS,
      y: Math.sin(angle) * START_RADIUS,
      size: baseKnotSize(entity, 0),
      color: colorOf(entity, palette),
      entity,
    });
    result.addedNodes.push(entity.id);
  });

  for (const relation of data.edges) {
    if (!edgeIds.has(relation.id)) continue;
    if (graph.hasEdge(relation.id)) {
      if (graph.getEdgeAttribute(relation.id, "relation") !== relation) {
        graph.replaceEdgeAttributes(relation.id, edgeAttributes(relation, palette));
        result.changed = true;
      }
      continue;
    }
    graph.addDirectedEdgeWithKey(
      relation.id,
      relation.from,
      relation.to,
      edgeAttributes(relation, palette),
    );
    result.addedEdges.push(relation.id);
  }

  if (!wasEmpty && result.addedNodes.length > 0) {
    const added = new Set(result.addedNodes);
    const placed = new Set(graph.nodes().filter((node) => !added.has(node)));
    for (const node of result.addedNodes) {
      placeNear(graph, node, placed);
      placed.add(node);
    }
  }

  const touched = result.addedNodes.length + result.addedEdges.length;
  if (touched + result.removedNodes + result.removedEdges > 0) {
    result.changed = true;
    // No attribute hints: with them, sigma repaints in place and throws for
    // a node it has not indexed yet (one added a moment ago).
    graph.updateEachNodeAttributes((node, attributes) => {
      const size = baseKnotSize(attributes.entity, graph.degree(node));
      return attributes.size === size ? attributes : { ...attributes, size };
    });
  }
  return result;
}

export interface BuildGraphOptions {
  /** ForceAtlas2 iterations, clamped to [0, MAX_LAYOUT_ITERATIONS]. */
  layoutIterations?: number;
}

/**
 * Builds the graphology graph that sigma renders, then lays it out
 * synchronously. Starting positions are deterministic (a circle, grouped by
 * entity type) and the iterations are bounded, so the same data always
 * produces the same picture.
 */
export function buildGraph(
  data: InvestigationGraph,
  { layoutIterations = FULL_LAYOUT_ITERATIONS }: BuildGraphOptions = {},
): NightloomGraph {
  const graph = createGraph();
  syncGraph(graph, data);
  layoutSync(graph, layoutIterations);
  return graph;
}
