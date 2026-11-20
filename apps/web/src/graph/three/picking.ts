/**
 * What the pointer and the keyboard reach in the 3D view, from the
 * projected nodes (see ./projection): the knot under a point (the nearest
 * one wins), the knots in a screen rectangle, the keyboard focus order, and
 * dragging a knot on the plane facing the camera.
 */
import { rectContains, type Point, type Rect } from "../geometry";
import { ROW_HEIGHT } from "../focusOrder";
import { basisOf, sub, type Camera3D, type Vec3 } from "./camera3d";
import { intersectPlane, screenRay, type Viewport } from "./projection";

// --- knot size on screen ---------------------------------------------------------

/**
 * Knots keep their 2D size (pixels) at the target's distance and grow or
 * shrink with perspective elsewhere, within these factors.
 */
export const MIN_ATTENUATION = 0.55;
export const MAX_ATTENUATION = 1.8;

/** A knot's radius on screen (pixels): its 2D size, scaled by perspective. */
export function knotScreenRadius(sizePx: number, depth: number, referenceDepth: number): number {
  if (depth <= 0) return 0;
  const factor = Math.min(Math.max(referenceDepth / depth, MIN_ATTENUATION), MAX_ATTENUATION);
  return sizePx * factor;
}

// --- picking ----------------------------------------------------------------------

/** Pixels around a knot that still count as on it (small knots are hard to hit). */
export const PICK_SLOP = 3;

export interface ScreenNodes {
  ids: readonly string[];
  /** (x, y, depth) per node, from projectAll. */
  projected: ArrayLike<number>;
  /** Radius on screen (pixels) per node. */
  radii: ArrayLike<number>;
  count: number;
}

/** The node drawn at `point` (the nearest to the camera when knots overlap), or null. */
export function pickNode(nodes: ScreenNodes, point: Point, slop = PICK_SLOP): string | null {
  let best = -1;
  let bestDepth = Infinity;
  let bestDistance = Infinity;
  for (let i = 0; i < nodes.count; i += 1) {
    const depth = nodes.projected[i * 3 + 2] ?? 0;
    if (depth <= 0) continue;
    const dx = (nodes.projected[i * 3] ?? 0) - point.x;
    const dy = (nodes.projected[i * 3 + 1] ?? 0) - point.y;
    const distance = Math.hypot(dx, dy);
    if (distance > (nodes.radii[i] ?? 0) + slop) continue;
    if (
      depth < bestDepth - 1e-9 ||
      (Math.abs(depth - bestDepth) <= 1e-9 && distance < bestDistance)
    ) {
      best = i;
      bestDepth = depth;
      bestDistance = distance;
    }
  }
  return best === -1 ? null : (nodes.ids[best] ?? null);
}

/** The nodes in front of the camera whose center is inside the screen rectangle. */
export function nodesInScreenRect(nodes: ScreenNodes, rect: Rect): string[] {
  const result: string[] = [];
  for (let i = 0; i < nodes.count; i += 1) {
    if ((nodes.projected[i * 3 + 2] ?? 0) <= 0) continue;
    const point = { x: nodes.projected[i * 3] ?? 0, y: nodes.projected[i * 3 + 1] ?? 0 };
    const id = nodes.ids[i];
    if (id !== undefined && rectContains(rect, point)) result.push(id);
  }
  return result;
}

// --- keyboard focus order ---------------------------------------------------------

/** Depth bands of the focus order: nearer bands first (see {@link focusOrder3d}). */
export const DEPTH_BANDS = 4;

export interface Placed3D {
  id: string;
  point: Point;
  depth: number;
}

/**
 * Tab order in 3D: the nodes on screen, nearest to the camera first, in
 * DEPTH_BANDS bands of depth; within a band, reading order (rows from the
 * top, then left to right, as in 2D). Nodes behind the camera or off
 * screen are left out.
 */
export function focusOrder3d(
  nodes: readonly Placed3D[],
  viewport: Viewport,
  rowHeight = ROW_HEIGHT,
): string[] {
  const shown = nodes.filter(
    (node) =>
      node.depth > 0 &&
      node.point.x >= 0 &&
      node.point.y >= 0 &&
      node.point.x <= viewport.width &&
      node.point.y <= viewport.height,
  );
  if (shown.length === 0) return [];
  let min = Infinity;
  let max = -Infinity;
  for (const node of shown) {
    if (node.depth < min) min = node.depth;
    if (node.depth > max) max = node.depth;
  }
  const band = Math.max((max - min) / DEPTH_BANDS, 1e-9);
  const bandOf = (depth: number) => Math.min(Math.floor((depth - min) / band), DEPTH_BANDS - 1);
  return shown
    .sort(
      (a, b) =>
        bandOf(a.depth) - bandOf(b.depth) ||
        Math.floor(a.point.y / rowHeight) - Math.floor(b.point.y / rowHeight) ||
        a.point.x - b.point.x ||
        a.id.localeCompare(b.id),
    )
    .map((node) => node.id);
}

// --- dragging ---------------------------------------------------------------------

/**
 * How far a drag from `start` to `current` (screen pixels) moves a knot at
 * `anchor`, on the plane through it facing the camera, in graph x / y (the
 * depth axis is derived from the depth mode, so z is left out). Null when
 * the pointer's ray misses the plane.
 */
export function dragDelta(
  camera: Camera3D,
  viewport: Viewport,
  anchor: Vec3,
  start: Point,
  current: Point,
): { dx: number; dy: number } | null {
  const normal = basisOf(camera).forward;
  const from = intersectPlane(screenRay(camera, start, viewport), anchor, normal);
  const to = intersectPlane(screenRay(camera, current, viewport), anchor, normal);
  if (!from || !to) return null;
  const [dx, dy] = sub(to, from);
  return { dx, dy };
}
