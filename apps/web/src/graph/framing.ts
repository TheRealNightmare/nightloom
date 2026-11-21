/**
 * Where the initial view and Reset view put the graph: in the largest part
 * of the canvas the overlays (legend, zoom controls, minimap) leave free,
 * filling {@link FIT_SHARE} of it, centered there. Used by both views (the
 * 3D one through ./three/camera3d).
 */
import type { Point, Rect } from "./geometry";

/** Share of the free area's width or height the framed graph fills. */
export const FIT_SHARE = 0.85;
/**
 * A graph is framed as if it were at least this wide (layout units, as the
 * first layout's circle, see START_RADIUS in ./buildGraph): one or two
 * entities are not blown up to fill the canvas.
 */
export const MIN_FIT_EXTENT = 200;
/** A free area narrower or lower than this share of the canvas is not used. */
export const MIN_AREA_SHARE = 0.4;

function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

function contains(outer: Rect, inner: Rect): boolean {
  return (
    inner.x >= outer.x &&
    inner.y >= outer.y &&
    inner.x + inner.width <= outer.x + outer.width &&
    inner.y + inner.height <= outer.y + outer.height
  );
}

/**
 * The largest upright rectangles of a `width` × `height` canvas that no
 * rectangle of `covered` overlaps (each as large as it can be), keeping
 * only those at least {@link MIN_AREA_SHARE} of the canvas in both
 * directions. The whole canvas when nothing is covered, or when no free
 * area is large enough.
 */
export function freeAreas(width: number, height: number, covered: readonly Rect[]): Rect[] {
  const whole: Rect = { x: 0, y: 0, width, height };
  const blocks = covered
    .map((r) => {
      const x0 = Math.max(r.x, 0);
      const y0 = Math.max(r.y, 0);
      const x1 = Math.min(r.x + r.width, width);
      const y1 = Math.min(r.y + r.height, height);
      return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
    })
    .filter((r) => r.width > 0 && r.height > 0);
  if (blocks.length === 0) return [whole];
  const lefts = [0, ...blocks.map((r) => r.x + r.width)];
  const rights = [width, ...blocks.map((r) => r.x)];
  const tops = [0, ...blocks.map((r) => r.y + r.height)];
  const bottoms = [height, ...blocks.map((r) => r.y)];
  const found: Rect[] = [];
  for (const left of lefts) {
    for (const right of rights) {
      if (right - left < width * MIN_AREA_SHARE) continue;
      for (const top of tops) {
        for (const bottom of bottoms) {
          if (bottom - top < height * MIN_AREA_SHARE) continue;
          const area = { x: left, y: top, width: right - left, height: bottom - top };
          if (blocks.some((block) => overlaps(area, block))) continue;
          found.push(area);
        }
      }
    }
  }
  const maximal = found.filter(
    (area, i) =>
      !found.some(
        (other, j) =>
          j !== i &&
          contains(other, area) &&
          (other.width * other.height > area.width * area.height || j < i),
      ),
  );
  return maximal.length > 0 ? maximal : [whole];
}

/** A knot where the camera's reset view draws it (pixels), with its radius on screen. */
export interface ScreenKnot {
  x: number;
  y: number;
  radius: number;
}

export interface Fit2d {
  /** How much closer than the reset view (2: twice as large). */
  zoom: number;
  /** The point of the reset view (pixels) that goes to the canvas's center. */
  anchor: Point;
  /** The free area the graph is framed in. */
  area: Rect;
}

/**
 * The 2D framing of `knots` (as the reset view draws them) on a `width` ×
 * `height` canvas whose `covered` rectangles are hidden by overlays: zoomed
 * so the knots' bounds fill `share` of the best free area (the one that
 * shows them largest) without exceeding `maxZoom`, and centered in it.
 * Knot radii are pixels whatever the zoom. Null when there is no knot.
 */
export function fit2d(
  knots: readonly ScreenKnot[],
  width: number,
  height: number,
  covered: readonly Rect[],
  maxZoom: number,
  share = FIT_SHARE,
): Fit2d | null {
  if (knots.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let pad = 0;
  for (const knot of knots) {
    minX = Math.min(minX, knot.x);
    minY = Math.min(minY, knot.y);
    maxX = Math.max(maxX, knot.x);
    maxY = Math.max(maxY, knot.y);
    pad = Math.max(pad, knot.radius);
  }
  const spanX = maxX - minX;
  const spanY = maxY - minY;
  let best: { zoom: number; area: Rect } | null = null;
  for (const area of freeAreas(width, height, covered)) {
    const roomX = share * area.width - 2 * pad;
    const roomY = share * area.height - 2 * pad;
    if (roomX <= 0 || roomY <= 0) continue;
    const zoom = Math.min(
      spanX > 0 ? roomX / spanX : Infinity,
      spanY > 0 ? roomY / spanY : Infinity,
      maxZoom,
    );
    const better =
      best === null ||
      zoom > best.zoom + 1e-9 ||
      (Math.abs(zoom - best.zoom) <= 1e-9 &&
        area.width * area.height > best.area.width * best.area.height);
    if (better) best = { zoom, area };
  }
  if (best === null || !(best.zoom > 0) || !Number.isFinite(best.zoom)) return null;
  const { zoom, area } = best;
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const areaX = area.x + area.width / 2;
  const areaY = area.y + area.height / 2;
  return {
    zoom,
    anchor: {
      x: centerX - (areaX - width / 2) / zoom,
      y: centerY - (areaY - height / 2) / zoom,
    },
    area,
  };
}
