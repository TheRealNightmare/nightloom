/**
 * Pure graph-view logic: highlighting, filtering, search and summaries.
 *
 * Nothing here touches WebGL, so it is unit-tested directly; the sigma
 * component only forwards these results to its reducers.
 */

import type { NodeDisplayData, EdgeDisplayData } from "sigma/types";

import {
  ENTITY_TYPES,
  type EntityNode,
  type EntityType,
  type RelationEdge,
} from "../domain/entities";
import type { NightloomGraph } from "./buildGraph";

export const CANVAS_BACKGROUND = "#0b1020";
export const LABEL_COLOR = "#d6dbe6";
export const HIGHLIGHT_EDGE_COLOR = "#e8c37a";

export interface ViewState {
  hoveredNode: string | null;
  selectedNode: string | null;
  hiddenTypes: ReadonlySet<EntityType>;
}

export const INITIAL_VIEW_STATE: ViewState = {
  hoveredNode: null,
  selectedNode: null,
  hiddenTypes: new Set<EntityType>(),
};

/** Precomputed per render so the reducers stay O(1) per item. */
export interface Highlight {
  /** The node whose neighborhood is emphasized (hover wins over selection). */
  focus: string | null;
  /** `focus` and its direct neighbors; empty when nothing is focused. */
  neighborhood: ReadonlySet<string>;
}

export function isTypeVisible(type: EntityType, state: ViewState): boolean {
  return !state.hiddenTypes.has(type);
}

export function neighborhoodOf(graph: NightloomGraph, node: string): Set<string> {
  const result = new Set<string>([node]);
  graph.forEachNeighbor(node, (neighbor) => result.add(neighbor));
  return result;
}

export function computeHighlight(graph: NightloomGraph, state: ViewState): Highlight {
  const candidate = state.hoveredNode ?? state.selectedNode;
  if (
    candidate === null ||
    !graph.hasNode(candidate) ||
    !isTypeVisible(graph.getNodeAttribute(candidate, "entityType"), state)
  ) {
    return { focus: null, neighborhood: new Set() };
  }
  return { focus: candidate, neighborhood: neighborhoodOf(graph, candidate) };
}

/** Blends a `#rrggbb` color toward `toward` (0 = unchanged, 1 = `toward`). */
export function mixColor(color: string, toward: string, amount: number): string {
  const parse = (hex: string): [number, number, number] => {
    const match = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!match?.[1]) throw new Error(`Expected a #rrggbb color, got ${hex}`);
    const value = Number.parseInt(match[1], 16);
    return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
  };
  const t = Math.min(Math.max(amount, 0), 1);
  const from = parse(color);
  const to = parse(toward);
  return `#${from
    .map((channel, i) => Math.round(channel + ((to[i] ?? 0) - channel) * t))
    .map((channel) => channel.toString(16).padStart(2, "0"))
    .join("")}`;
}

const DIM_AMOUNT = 0.78;

/** Sigma node reducer body: hidden types, dimming and emphasis. */
export function nodeView(
  graph: NightloomGraph,
  node: string,
  state: ViewState,
  highlight: Highlight,
): Partial<NodeDisplayData> {
  const attributes = graph.getNodeAttributes(node);
  if (!isTypeVisible(attributes.entityType, state)) {
    return { visibility: "hidden", labelVisibility: "hidden" };
  }

  const view: Partial<NodeDisplayData> = {};
  if (highlight.focus !== null) {
    if (highlight.neighborhood.has(node)) {
      view.labelVisibility = "visible";
      view.zIndex = 1;
      view.depth = "topNodes";
      view.labelDepth = "topNodes";
    } else {
      view.color = mixColor(attributes.color, CANVAS_BACKGROUND, DIM_AMOUNT);
      view.labelVisibility = "hidden";
      view.zIndex = 0;
    }
  }
  if (node === state.selectedNode) {
    view.highlighted = true;
    view.size = attributes.size * 1.35;
    view.labelVisibility = "visible";
    view.zIndex = 2;
    view.depth = "topNodes";
    view.labelDepth = "topNodes";
  }
  return view;
}

/** Sigma edge reducer body. */
export function edgeView(
  graph: NightloomGraph,
  edge: string,
  state: ViewState,
  highlight: Highlight,
): Partial<EdgeDisplayData> {
  const [source, target] = graph.extremities(edge);
  const sourceType = graph.getNodeAttribute(source, "entityType");
  const targetType = graph.getNodeAttribute(target, "entityType");
  if (!isTypeVisible(sourceType, state) || !isTypeVisible(targetType, state)) {
    return { visibility: "hidden" };
  }
  if (highlight.focus === null) return {};

  if (source === highlight.focus || target === highlight.focus) {
    return {
      color: HIGHLIGHT_EDGE_COLOR,
      size: 2,
      zIndex: 1,
      depth: "topEdges",
      label: graph.getEdgeAttribute(edge, "label"),
      labelVisibility: "visible",
      labelColor: LABEL_COLOR,
    };
  }
  return {
    color: mixColor(graph.getEdgeAttribute(edge, "color"), CANVAS_BACKGROUND, 0.6),
    zIndex: 0,
  };
}

export function toggleType(hidden: ReadonlySet<EntityType>, type: EntityType): Set<EntityType> {
  const next = new Set(hidden);
  if (next.has(type)) next.delete(type);
  else next.add(type);
  return next;
}

export interface TypeCount {
  type: EntityType;
  count: number;
}

/** Entity types present in the data, in the canonical order of ENTITY_TYPES. */
export function countByType(nodes: readonly EntityNode[]): TypeCount[] {
  const counts = new Map<EntityType, number>();
  for (const node of nodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
  return ENTITY_TYPES.filter((type) => counts.has(type)).map((type) => ({
    type,
    count: counts.get(type) ?? 0,
  }));
}

/**
 * Finds visible nodes whose label (or id) contains `query`, case-insensitively.
 * Exact matches rank first, then prefix matches, then shorter labels.
 */
export function searchNodes(
  nodes: readonly EntityNode[],
  query: string,
  hiddenTypes: ReadonlySet<EntityType> = new Set(),
  limit = 8,
): EntityNode[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return [];

  const rank = (node: EntityNode): number => {
    const label = node.label.toLowerCase();
    if (label === needle) return 0;
    if (label.startsWith(needle)) return 1;
    if (label.includes(needle)) return 2;
    if (node.id.toLowerCase().includes(needle)) return 3;
    return -1;
  };

  return nodes
    .filter((node) => !hiddenTypes.has(node.type))
    .map((node) => ({ node, score: rank(node) }))
    .filter(({ score }) => score >= 0)
    .sort(
      (a, b) =>
        a.score - b.score ||
        a.node.label.length - b.node.label.length ||
        a.node.label.localeCompare(b.node.label),
    )
    .slice(0, limit)
    .map(({ node }) => node);
}

export interface Connection {
  edge: RelationEdge;
  direction: "out" | "in";
  other: EntityNode;
}

/** Relationships of `node`, outgoing first, each with the entity on the other end. */
export function connectionsOf(graph: NightloomGraph, node: string): Connection[] {
  if (!graph.hasNode(node)) return [];
  const result: Connection[] = [];
  graph.forEachOutEdge(node, (_edge, attributes, _source, target) => {
    result.push({
      edge: attributes.relation,
      direction: "out",
      other: graph.getNodeAttribute(target, "entity"),
    });
  });
  graph.forEachInEdge(node, (_edge, attributes, source) => {
    result.push({
      edge: attributes.relation,
      direction: "in",
      other: graph.getNodeAttribute(source, "entity"),
    });
  });
  return result;
}
