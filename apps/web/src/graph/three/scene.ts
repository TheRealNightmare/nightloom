/**
 * The 3D scene as typed arrays, ready for the GPU: one record per visible
 * node (position, type color, knot size and rings, dimming) and two line
 * vertices per thread segment. Everything visual follows the 2D rules of
 * ../encoding and ../view (sizes, selection, pins, pulse, dimming, uncertain
 * threads); this module only lays the results out for three.js.
 *
 * Two kinds of work, both measured by the performance tests:
 * - {@link buildModel} when what is drawn or how it looks changes (O(n + e));
 * - {@link updatePositions} and the writers each frame something moves
 *   (layout, drag, depth animation).
 */
import type { EntityNode } from "../../domain/entities";
import type { GraphPalette } from "../../theme/graphPalette";
import type { NightloomGraph } from "../buildGraph";
import {
  compactCount,
  DIM_OPACITY,
  FOCUS_UNCERTAIN_OPACITY,
  HALO_WIDTH,
  LOW_CONFIDENCE_OPACITY,
  PIN_WIDTH,
  SELECTED_SCALE,
} from "../encoding";
import { canvasLabel } from "../labels";
import type { DepthMode } from "../mode";
import { isNodeVisible, PULSE_SCALE, type Highlight, type ViewState } from "../view";
import { blendDepth, depthOf, depthSpan, type RulerTick } from "./depth";

export type Rgb = [number, number, number];

/** "#c8963e" → [0.78, 0.59, 0.24] (sRGB, as the tokens are; the renderer outputs them as is). */
export function hexToRgb(hex: string): Rgb {
  const value = hex.trim().replace(/^#/, "");
  const full =
    value.length === 3
      ? value
          .split("")
          .map((c) => c + c)
          .join("")
      : value.slice(0, 6);
  const n = Number.parseInt(full, 16);
  if (!Number.isFinite(n) || full.length !== 6) return [0, 0, 0];
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export interface SceneModel {
  /** Visible nodes, in drawing order. */
  ids: string[];
  index: Map<string, number>;
  count: number;
  entities: EntityNode[];
  isGroup: Uint8Array;
  pinned: Uint8Array;
  selected: Uint8Array;
  /** In the hovered / selected node's neighbourhood (labels there come first). */
  near: Uint8Array;
  /** Knot radius (pixels at the target's distance), after selection and pulse scaling. */
  sizePx: Float32Array;
  haloPx: Float32Array;
  pinPx: Float32Array;
  /** 1, or DIM_OPACITY while another node is hovered or selected. */
  dim: Float32Array;
  /** Type color per node (r, g, b). */
  color: Float32Array;
  degree: Float32Array;
  /** Canvas label (U3 shortened text; a group's count). */
  labels: string[];
  /** Depth as a share of the depth range: before and after a change of depth mode. */
  depthFrom: Float32Array;
  depthTo: Float32Array;
  /** Ruler marks (z as a share of the depth range). */
  ticks: RulerTick[];
  /** World positions (x, y, z), filled by updatePositions. */
  positions: Float32Array;
  /** The depth range used for the last positions. */
  span: number;
  edgeCount: number;
  edgeSource: Uint32Array;
  edgeTarget: Uint32Array;
  /** Final thread color per edge (r, g, b), dimming and uncertainty blended toward the background. */
  edgeColor: Float32Array;
}

export interface BuildContext {
  palette: GraphPalette;
  pulse: ReadonlySet<string>;
  depth: DepthMode;
}

function blend(into: Float32Array, at: number, background: Rgb, color: Rgb, alpha: number): void {
  into[at] = background[0] + (color[0] - background[0]) * alpha;
  into[at + 1] = background[1] + (color[1] - background[1]) * alpha;
  into[at + 2] = background[2] + (color[2] - background[2]) * alpha;
}

/**
 * The scene for the visible part of `graph`, as `view` and `highlight`
 * show it. Depth starts where `previous` left it (for the same node), so a
 * change of depth mode, or new nodes, can animate from there.
 */
export function buildModel(
  graph: NightloomGraph,
  view: ViewState,
  highlight: Highlight,
  context: BuildContext,
  previous: SceneModel | null = null,
): SceneModel {
  const ids = graph.filterNodes((node) => isNodeVisible(graph, node, view));
  const count = ids.length;
  const index = new Map<string, number>();
  ids.forEach((id, i) => index.set(id, i));
  const entities: EntityNode[] = new Array<EntityNode>(count);
  const isGroup = new Uint8Array(count);
  const pinned = new Uint8Array(count);
  const selected = new Uint8Array(count);
  const near = new Uint8Array(count);
  const sizePx = new Float32Array(count);
  const haloPx = new Float32Array(count);
  const pinPx = new Float32Array(count);
  const dim = new Float32Array(count);
  const color = new Float32Array(count * 3);
  const degree = new Float32Array(count);
  const labels = new Array<string>(count);
  const colorCache = new Map<string, Rgb>();
  const colorOf = (hex: string): Rgb => {
    let value = colorCache.get(hex);
    if (!value) {
      value = hexToRgb(hex);
      colorCache.set(hex, value);
    }
    return value;
  };

  const { palette, pulse } = context;
  const focus = highlight.focus;
  for (let i = 0; i < count; i += 1) {
    const id = ids[i] ?? "";
    const attributes = graph.getNodeAttributes(id);
    const entity = attributes.entity;
    entities[i] = entity;
    const group = entity.group !== undefined;
    const isSelected = id === view.selectedNode || (view.selectedNodes?.has(id) ?? false);
    const isPinned = attributes.pinned === true;
    const pulsing = pulse.has(id);
    isGroup[i] = group ? 1 : 0;
    pinned[i] = isPinned ? 1 : 0;
    selected[i] = isSelected ? 1 : 0;
    near[i] = focus !== null && highlight.neighborhood.has(id) ? 1 : 0;
    sizePx[i] = attributes.size * (pulsing ? PULSE_SCALE : 1) * (isSelected ? SELECTED_SCALE : 1);
    haloPx[i] = isSelected ? HALO_WIDTH : 0;
    pinPx[i] = isPinned ? PIN_WIDTH : 0;
    dim[i] = focus !== null && !highlight.neighborhood.has(id) && !pulsing ? DIM_OPACITY : 1;
    const c = colorOf(palette.types[entity.type]);
    color[i * 3] = c[0];
    color[i * 3 + 1] = c[1];
    color[i * 3 + 2] = c[2];
    degree[i] = graph.degree(id);
    labels[i] = entity.group ? compactCount(entity.group.members.length) : canvasLabel(entity);
  }

  // Depth (as a share of the range; the range follows the layout's size).
  const depth = depthOf(
    context.depth,
    entities.map((entity) => ({ type: entity.type, firstSeen: entity.first_seen })),
    1,
  );
  const depthTo = depth.z;
  const depthFrom = new Float32Array(count);
  for (let i = 0; i < count; i += 1) {
    const at = previous?.index.get(ids[i] ?? "");
    depthFrom[i] =
      previous === null || at === undefined ? (depthTo[i] ?? 0) : currentDepth(previous, at);
  }

  // Threads between visible nodes.
  const background = colorOf(palette.background);
  const hair = colorOf(palette.edge);
  const brass = colorOf(palette.selection);
  const sources: number[] = [];
  const targets: number[] = [];
  const colors: number[] = [];
  const scratch = new Float32Array(3);
  graph.forEachEdge((_edge, attributes, source, target) => {
    const s = index.get(source);
    const t = index.get(target);
    if (s === undefined || t === undefined) return;
    const touches = focus !== null && (source === focus || target === focus);
    let alpha: number;
    let base: Rgb;
    if (touches) {
      base = brass;
      alpha = attributes.uncertain ? FOCUS_UNCERTAIN_OPACITY : 1;
    } else {
      base = hair;
      alpha = attributes.uncertain ? LOW_CONFIDENCE_OPACITY : 1;
      if (focus !== null) alpha *= DIM_OPACITY;
    }
    blend(scratch, 0, background, base, alpha);
    sources.push(s);
    targets.push(t);
    colors.push(scratch[0] ?? 0, scratch[1] ?? 0, scratch[2] ?? 0);
  });

  return {
    ids,
    index,
    count,
    entities,
    isGroup,
    pinned,
    selected,
    near,
    sizePx,
    haloPx,
    pinPx,
    dim,
    color,
    degree,
    labels,
    depthFrom,
    depthTo,
    ticks: depth.ticks,
    positions: new Float32Array(count * 3),
    span: previous?.span ?? 0,
    edgeCount: sources.length,
    edgeSource: Uint32Array.from(sources),
    edgeTarget: Uint32Array.from(targets),
    edgeColor: Float32Array.from(colors),
  };
}

/** A node's depth share as last drawn (its world z over the range). */
function currentDepth(model: SceneModel, at: number): number {
  if (model.span <= 0) return model.depthTo[at] ?? 0;
  return (model.positions[at * 3 + 2] ?? 0) / model.span;
}

export interface Extent {
  /** Center and radius of the nodes in the x / y plane. */
  cx: number;
  cy: number;
  radius: number;
}

/**
 * Copies the layout's x / y into `model.positions` and sets z from the
 * depth mode, `t` (0..1) of the way from the previous depth to the new one.
 * The depth range follows the layout's size unless `span` is given (kept
 * still during a drag, so nothing else moves). Returns the x / y extent.
 */
export function updatePositions(
  model: SceneModel,
  graph: NightloomGraph,
  t: number,
  span?: number,
  scratch: Float32Array = new Float32Array(model.count),
): Extent {
  const { positions, ids, count } = model;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < count; i += 1) {
    const id = ids[i] ?? "";
    const { x, y } = graph.hasNode(id) ? graph.getNodeAttributes(id) : { x: 0, y: 0 };
    positions[i * 3] = x;
    positions[i * 3 + 1] = y;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  const extent: Extent =
    count === 0
      ? { cx: 0, cy: 0, radius: 1 }
      : {
          cx: (minX + maxX) / 2,
          cy: (minY + maxY) / 2,
          radius: Math.max(Math.hypot(maxX - minX, maxY - minY) / 2, 1),
        };
  model.span = span ?? depthSpan(extent.radius);
  const shares = scratch.length >= count ? scratch : new Float32Array(count);
  blendDepth(model.depthFrom, model.depthTo, t, shares);
  for (let i = 0; i < count; i += 1) positions[i * 3 + 2] = (shares[i] ?? 0) * model.span;
  return extent;
}

// --- GPU buffers ------------------------------------------------------------------

/** Per-instance data of one knot shape (entities, or collapsed groups). */
export interface KnotBuffers {
  capacity: number;
  count: number;
  /** Model index of each instance. */
  nodes: Uint32Array;
  /** Instance matrices (translation only; 16 numbers each). */
  matrix: Float32Array;
  /** Type color (r, g, b). */
  color: Float32Array;
  /** Knot radius, halo width, pin width (pixels), dimming. */
  style: Float32Array;
}

export function knotBuffers(capacity: number): KnotBuffers {
  const matrix = new Float32Array(capacity * 16);
  for (let i = 0; i < capacity; i += 1) {
    matrix[i * 16] = 1;
    matrix[i * 16 + 5] = 1;
    matrix[i * 16 + 10] = 1;
    matrix[i * 16 + 15] = 1;
  }
  return {
    capacity,
    count: 0,
    nodes: new Uint32Array(capacity),
    matrix,
    color: new Float32Array(capacity * 3),
    style: new Float32Array(capacity * 4),
  };
}

/** Room for `needed` instances, keeping `buffers` when it is large enough. */
export function ensureKnotCapacity(buffers: KnotBuffers, needed: number): KnotBuffers {
  if (needed <= buffers.capacity) return buffers;
  return knotBuffers(Math.max(needed, Math.ceil(buffers.capacity * 1.5), 16));
}

/** Fills the instance styles and colors of one shape (`groups`: collapsed groups, else entities). */
export function writeKnotStyles(model: SceneModel, buffers: KnotBuffers, groups: boolean): void {
  let n = 0;
  for (let i = 0; i < model.count; i += 1) {
    if ((model.isGroup[i] === 1) !== groups) continue;
    buffers.nodes[n] = i;
    buffers.color[n * 3] = model.color[i * 3] ?? 0;
    buffers.color[n * 3 + 1] = model.color[i * 3 + 1] ?? 0;
    buffers.color[n * 3 + 2] = model.color[i * 3 + 2] ?? 0;
    buffers.style[n * 4] = model.sizePx[i] ?? 0;
    buffers.style[n * 4 + 1] = model.haloPx[i] ?? 0;
    buffers.style[n * 4 + 2] = model.pinPx[i] ?? 0;
    buffers.style[n * 4 + 3] = model.dim[i] ?? 1;
    n += 1;
  }
  buffers.count = n;
}

/** Moves the instances to the model's positions. */
export function writeKnotPositions(model: SceneModel, buffers: KnotBuffers): void {
  const { positions } = model;
  for (let n = 0; n < buffers.count; n += 1) {
    const i = buffers.nodes[n] ?? 0;
    buffers.matrix[n * 16 + 12] = positions[i * 3] ?? 0;
    buffers.matrix[n * 16 + 13] = positions[i * 3 + 1] ?? 0;
    buffers.matrix[n * 16 + 14] = positions[i * 3 + 2] ?? 0;
  }
}

/** Segments per thread: few, the curve only has to read as a gentle sag. */
export const THREAD_SEGMENTS = 6;
/** How far a thread sags at its middle, as a share of its length. */
export const THREAD_SAG = 0.06;

export interface ThreadBuffers {
  capacity: number;
  /** Line-segment vertices (x, y, z): THREAD_SEGMENTS × 2 per thread. */
  positions: Float32Array;
  colors: Float32Array;
  /** Vertices in use. */
  vertices: number;
}

export function threadBuffers(capacity: number): ThreadBuffers {
  const size = capacity * THREAD_SEGMENTS * 2 * 3;
  return {
    capacity,
    positions: new Float32Array(size),
    colors: new Float32Array(size),
    vertices: 0,
  };
}

export function ensureThreadCapacity(buffers: ThreadBuffers, needed: number): ThreadBuffers {
  if (needed <= buffers.capacity) return buffers;
  return threadBuffers(Math.max(needed, Math.ceil(buffers.capacity * 1.5), 16));
}

/** Colors every thread's segments (after buildModel). */
export function writeThreadColors(model: SceneModel, buffers: ThreadBuffers): void {
  const { colors } = buffers;
  const per = THREAD_SEGMENTS * 2;
  for (let e = 0; e < model.edgeCount; e += 1) {
    const r = model.edgeColor[e * 3] ?? 0;
    const g = model.edgeColor[e * 3 + 1] ?? 0;
    const b = model.edgeColor[e * 3 + 2] ?? 0;
    for (let v = 0; v < per; v += 1) {
      const at = (e * per + v) * 3;
      colors[at] = r;
      colors[at + 1] = g;
      colors[at + 2] = b;
    }
  }
  buffers.vertices = model.edgeCount * per;
}

/**
 * Each thread as a quadratic curve from source to target that sags
 * downward (graph −y) at its middle by THREAD_SAG of its length, cut into
 * THREAD_SEGMENTS straight segments.
 */
export function writeThreadPositions(model: SceneModel, buffers: ThreadBuffers): void {
  const { positions } = model;
  const out = buffers.positions;
  let at = 0;
  for (let e = 0; e < model.edgeCount; e += 1) {
    const s = (model.edgeSource[e] ?? 0) * 3;
    const t = (model.edgeTarget[e] ?? 0) * 3;
    const ax = positions[s] ?? 0;
    const ay = positions[s + 1] ?? 0;
    const az = positions[s + 2] ?? 0;
    const bx = positions[t] ?? 0;
    const by = positions[t + 1] ?? 0;
    const bz = positions[t + 2] ?? 0;
    const length = Math.hypot(bx - ax, by - ay, bz - az);
    // Control point: the middle, lowered so the curve's middle sags by THREAD_SAG × length.
    const cx = (ax + bx) / 2;
    const cy = (ay + by) / 2 - 2 * THREAD_SAG * length;
    const cz = (az + bz) / 2;
    let px = ax;
    let py = ay;
    let pz = az;
    for (let k = 1; k <= THREAD_SEGMENTS; k += 1) {
      const u = k / THREAD_SEGMENTS;
      const a = (1 - u) * (1 - u);
      const b = 2 * (1 - u) * u;
      const c = u * u;
      const qx = a * ax + b * cx + c * bx;
      const qy = a * ay + b * cy + c * by;
      const qz = a * az + b * cz + c * bz;
      out[at] = px;
      out[at + 1] = py;
      out[at + 2] = pz;
      out[at + 3] = qx;
      out[at + 4] = qy;
      out[at + 5] = qz;
      at += 6;
      px = qx;
      py = qy;
      pz = qz;
    }
  }
}
