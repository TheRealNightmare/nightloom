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
import { DEFAULT_GRAPH_PALETTE, type GraphPalette } from "../theme/graphPalette";
import type { Theme } from "../theme/tokens";
import type { NightloomGraph } from "./buildGraph";
import {
  attachmentName,
  DIM_OPACITY,
  EDGE_LABEL_SIZE,
  edgeSizeFor,
  focusThreadStyle,
  GROUP_ATTACHMENT,
  knotDisplaySize,
  knotVariables,
  LABEL_FONT,
  nodeLabelStyle,
  PIN_ATTACHMENT,
  threadStyle,
  type KnotVariables,
  type NodeLabelStyle,
} from "./encoding";

export interface ViewState {
  hoveredNode: string | null;
  selectedNode: string | null;
  hiddenTypes: ReadonlySet<EntityType>;
  /** Nodes the other filters hide (see ./filters), on top of the hidden types. */
  hiddenNodes?: ReadonlySet<string>;
  /** A multi-selection (Ctrl/Cmd+click, a selection rectangle); drawn like the selected node. */
  selectedNodes?: ReadonlySet<string>;
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

/** Whether the node is drawn: its type is shown and no filter hides it. */
export function isNodeVisible(graph: NightloomGraph, node: string, state: ViewState): boolean {
  return (
    isTypeVisible(graph.getNodeAttribute(node, "entityType"), state) &&
    !(state.hiddenNodes?.has(node) ?? false)
  );
}

export function neighborhoodOf(graph: NightloomGraph, node: string): Set<string> {
  const result = new Set<string>([node]);
  graph.forEachNeighbor(node, (neighbor) => result.add(neighbor));
  return result;
}

export function computeHighlight(graph: NightloomGraph, state: ViewState): Highlight {
  const candidate = state.hoveredNode ?? state.selectedNode;
  if (candidate === null || !graph.hasNode(candidate) || !isNodeVisible(graph, candidate, state)) {
    return { focus: null, neighborhood: new Set() };
  }
  return { focus: candidate, neighborhood: neighborhoodOf(graph, candidate) };
}

/** What the reducers need besides the graph and the view state. */
export interface RenderContext {
  /** Nodes to emphasize for a moment (e.g. what a Shuttle just found); never dimmed. */
  pulse: ReadonlySet<string>;
  palette: GraphPalette;
  /** Picks the label attachments drawn in this theme's colors. */
  theme: Theme;
  /** The camera's zoom ratio (1 = the whole graph; smaller = zoomed in). */
  cameraRatio: number;
}

/** No nodes pulsing. */
export const NO_PULSE: ReadonlySet<string> = new Set();

export const DEFAULT_RENDER_CONTEXT: RenderContext = {
  pulse: NO_PULSE,
  palette: DEFAULT_GRAPH_PALETTE,
  theme: "dark",
  cameraRatio: 1,
};

/** A pulsing knot is drawn this much larger. */
export const PULSE_SCALE = 1.15;

/** The node's custom display data: sigma's built-ins plus the knot program's variables. */
export type NodeView = Partial<NodeDisplayData> &
  Partial<KnotVariables> & {
    labelFont?: string;
    labelSize?: number;
    labelPosition?: NodeLabelStyle["labelPosition"];
    labelAttachment?: string | null;
    labelAttachmentPlacement?: "below" | "left";
    opacity?: number;
  };

export type EdgeView = Partial<EdgeDisplayData> & {
  opacity?: number;
  curvature?: number;
  labelFont?: string;
  labelSize?: number;
};

/**
 * Sigma node reducer body: the knot (see ./encoding), its label, and the
 * hover / selection state. While a node is hovered or selected, it and its
 * neighbours keep full strength and everything else dims to DIM_OPACITY.
 */
export function nodeView(
  graph: NightloomGraph,
  node: string,
  state: ViewState,
  highlight: Highlight,
  context: Partial<RenderContext> = {},
): NodeView {
  if (!isNodeVisible(graph, node, state)) {
    return { visibility: "hidden", labelVisibility: "hidden" };
  }
  const { pulse, palette, theme } = { ...DEFAULT_RENDER_CONTEXT, ...context };
  const attributes = graph.getNodeAttributes(node);
  const { entity } = attributes;
  const isGroup = entity.group !== undefined;
  const selected = node === state.selectedNode || (state.selectedNodes?.has(node) ?? false);
  const pinned = attributes.pinned === true;
  const pulsing = pulse.has(node);
  const knot = { isGroup, selected, pinned };

  const view: NodeView = {
    size: knotDisplaySize(attributes.size * (pulsing ? PULSE_SCALE : 1), knot),
    ...knotVariables(palette.types[entity.type], knot, palette),
    ...nodeLabelStyle(entity, selected, palette),
    labelAttachment: null,
    opacity: 1,
  };
  if (isGroup) {
    // The count is the knot's face: always shown, with the type name under it.
    view.labelVisibility = "visible";
    view.labelAttachment = attachmentName(GROUP_ATTACHMENT, theme);
    view.labelAttachmentPlacement = "below";
  } else if (pinned) {
    view.labelAttachment = attachmentName(PIN_ATTACHMENT, theme);
    view.labelAttachmentPlacement = "left";
  }

  if (highlight.focus !== null) {
    if (highlight.neighborhood.has(node)) {
      view.labelVisibility = "visible";
      view.zIndex = 1;
      view.depth = "topNodes";
      view.labelDepth = "topNodes";
    } else if (!pulsing) {
      view.opacity = DIM_OPACITY;
      view.labelVisibility = "hidden";
      view.zIndex = 0;
    }
  }
  if (pulsing || selected) {
    view.highlighted = true;
    view.labelVisibility = "visible";
    view.zIndex = 2;
    view.depth = "topNodes";
    view.labelDepth = "topNodes";
  }
  return view;
}

/**
 * Sigma edge reducer body: threads in --hair (faint and thin when
 * uncertain); the focused entity's threads in brass, with their labels.
 * Edge labels are hidden everywhere else.
 */
export function edgeView(
  graph: NightloomGraph,
  edge: string,
  state: ViewState,
  highlight: Highlight,
  context: Partial<RenderContext> = {},
): EdgeView {
  const [source, target] = graph.extremities(edge);
  if (!isNodeVisible(graph, source, state) || !isNodeVisible(graph, target, state)) {
    return { visibility: "hidden" };
  }
  const { palette, cameraRatio } = { ...DEFAULT_RENDER_CONTEXT, ...context };
  const { uncertain } = graph.getEdgeAttributes(edge);
  const touchesFocus =
    highlight.focus !== null && (source === highlight.focus || target === highlight.focus);

  if (touchesFocus) {
    const thread = focusThreadStyle(uncertain, palette);
    return {
      color: thread.color,
      size: edgeSizeFor(thread.width, cameraRatio),
      opacity: thread.opacity,
      curvature: thread.curvature,
      zIndex: 1,
      depth: "topEdges",
      label: graph.getEdgeAttribute(edge, "label"),
      labelVisibility: "visible",
      labelColor: palette.label,
      labelFont: LABEL_FONT,
      labelSize: EDGE_LABEL_SIZE,
    };
  }
  const thread = threadStyle(uncertain, cameraRatio, palette);
  return {
    color: thread.color,
    size: edgeSizeFor(thread.width, cameraRatio),
    opacity: highlight.focus === null ? thread.opacity : thread.opacity * DIM_OPACITY,
    curvature: thread.curvature,
    zIndex: 0,
    labelVisibility: "hidden",
  };
}

/** At most this many relationships are spelled out in {@link focusSummary}. */
export const SUMMARY_LIMIT = 12;

/**
 * The text of the relationships whose labels are on the canvas (those of
 * the hovered or selected entity), for assistive technology; empty when
 * none are. "example.org: has subdomain www.example.org; resolves to (from)
 * 192.0.2.1".
 */
export function focusSummary(
  graph: NightloomGraph,
  state: ViewState,
  highlight: Highlight,
): string {
  const focus = highlight.focus;
  if (focus === null) return "";
  const parts: string[] = [];
  let total = 0;
  const add = (edge: string, other: string, incoming: boolean) => {
    if (!isNodeVisible(graph, other, state)) return;
    total += 1;
    if (parts.length >= SUMMARY_LIMIT) return;
    const label = graph.getEdgeAttribute(edge, "label");
    const name = graph.getNodeAttribute(other, "label");
    parts.push(incoming ? `${name} ${label} it` : `${label} ${name}`);
  };
  graph.forEachOutEdge(focus, (edge, _attributes, _source, target) => {
    add(edge, target, false);
  });
  graph.forEachInEdge(focus, (edge, _attributes, source) => {
    add(edge, source, true);
  });
  if (total === 0) return "";
  const more = total > parts.length ? `; and ${String(total - parts.length)} more` : "";
  return `${graph.getNodeAttribute(focus, "label")}: ${parts.join("; ")}${more}`;
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
