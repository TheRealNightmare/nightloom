/**
 * The minimap: every visible node as a dot in its type color and the
 * current viewport as a brass outline, north up (graph y up, like the
 * unrotated view), so a rotated camera shows as a rotated outline. Pure
 * projection and drawing; ./Minimap.tsx schedules the redraws.
 */
import type { Point } from "./geometry";
import type { AnySigma } from "./interactions";

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface MinimapSize {
  width: number;
  height: number;
}

/** Space kept free around the drawing (pixels). */
export const MINIMAP_PADDING = 6;

export interface MinimapProjection {
  toMinimap: (point: Point) => Point;
  toGraph: (point: Point) => Point;
}

/** The smallest box around `points` (a unit box around the origin if there are none). */
export function boundsOf(points: Iterable<Point>): Bounds {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const { x, y } of points) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  if (minX === Infinity) return { minX: -1, minY: -1, maxX: 1, maxY: 1 };
  return { minX, minY, maxX, maxY };
}

/** Grows `bounds` to include `points` (the viewport's corners, so it is never drawn off the map). */
export function extendBounds(bounds: Bounds, points: Iterable<Point>): Bounds {
  const result = { ...bounds };
  for (const { x, y } of points) {
    result.minX = Math.min(result.minX, x);
    result.minY = Math.min(result.minY, y);
    result.maxX = Math.max(result.maxX, x);
    result.maxY = Math.max(result.maxY, y);
  }
  return result;
}

/** Fits `bounds` into `size`, centered, keeping the aspect ratio; y is flipped (graph y up). */
export function minimapProjection(
  bounds: Bounds,
  size: MinimapSize,
  padding = MINIMAP_PADDING,
): MinimapProjection {
  const spanX = Math.max(bounds.maxX - bounds.minX, 1e-9);
  const spanY = Math.max(bounds.maxY - bounds.minY, 1e-9);
  const innerW = Math.max(size.width - 2 * padding, 1);
  const innerH = Math.max(size.height - 2 * padding, 1);
  const scale = Math.min(innerW / spanX, innerH / spanY);
  const offsetX = padding + (innerW - spanX * scale) / 2;
  const offsetY = padding + (innerH - spanY * scale) / 2;
  return {
    toMinimap: ({ x, y }) => ({
      x: offsetX + (x - bounds.minX) * scale,
      y: offsetY + (bounds.maxY - y) * scale,
    }),
    toGraph: ({ x, y }) => ({
      x: bounds.minX + (x - offsetX) / scale,
      y: bounds.maxY - (y - offsetY) / scale,
    }),
  };
}

export interface MinimapDot {
  x: number;
  y: number;
  color: string;
}

export interface MinimapColors {
  viewport: string;
}

/** The subset of CanvasRenderingContext2D the drawing uses. */
export type MinimapContext = Pick<
  CanvasRenderingContext2D,
  | "clearRect"
  | "fillRect"
  | "beginPath"
  | "moveTo"
  | "lineTo"
  | "closePath"
  | "stroke"
  | "fill"
  | "setTransform"
> & { fillStyle: unknown; strokeStyle: unknown; lineWidth: number; globalAlpha: number };

export const DOT_SIZE = 2;

/** Draws the dots and the viewport outline (graph coordinates, projected). */
export function drawMinimap(
  context: MinimapContext,
  size: MinimapSize,
  dots: readonly MinimapDot[],
  viewport: readonly Point[],
  projection: MinimapProjection,
  colors: MinimapColors,
): void {
  context.clearRect(0, 0, size.width, size.height);
  // One fillStyle change per color run, not per dot.
  let color = "";
  for (const dot of dots) {
    if (dot.color !== color) {
      color = dot.color;
      context.fillStyle = color;
    }
    const p = projection.toMinimap(dot);
    context.fillRect(p.x - DOT_SIZE / 2, p.y - DOT_SIZE / 2, DOT_SIZE, DOT_SIZE);
  }
  if (viewport.length < 3) return;
  context.beginPath();
  viewport.forEach((corner, index) => {
    const p = projection.toMinimap(corner);
    if (index === 0) context.moveTo(p.x, p.y);
    else context.lineTo(p.x, p.y);
  });
  context.closePath();
  context.globalAlpha = 0.12;
  context.fillStyle = colors.viewport;
  context.fill();
  context.globalAlpha = 1;
  context.strokeStyle = colors.viewport;
  context.lineWidth = 1.5;
  context.stroke();
}

/** At most this many redraws per second while the camera or nodes move. */
export const MINIMAP_FPS = 10;
/** The minimap is shown by default above this many visible entities. */
export const MINIMAP_DEFAULT_THRESHOLD = 60;

/**
 * Throttles `draw` to one call per `interval` ms: the first request draws on
 * the next frame, later ones within the interval are folded into one
 * trailing call. Nothing runs while nothing is requested (idle).
 */
export function throttle(
  draw: () => void,
  interval = 1000 / MINIMAP_FPS,
  {
    now = () => performance.now(),
    setTimer = (callback: () => void, ms: number): unknown => setTimeout(callback, ms),
    clearTimer = (handle: unknown) => {
      clearTimeout(handle as ReturnType<typeof setTimeout>);
    },
  } = {},
): { request: () => void; cancel: () => void } {
  let last = -Infinity;
  let timer: unknown = null;
  const run = () => {
    timer = null;
    last = now();
    draw();
  };
  return {
    request() {
      if (timer !== null) return;
      const wait = Math.max(0, last + interval - now());
      timer = setTimer(run, wait);
    },
    cancel() {
      if (timer !== null) clearTimer(timer);
      timer = null;
    },
  };
}

/** The viewport's four corners in graph coordinates (a rotated camera gives a rotated outline). */
export function viewportCorners(sigma: AnySigma): Point[] {
  const { width, height } = sigma.getDimensions();
  return [
    { x: 0, y: 0 },
    { x: width, y: 0 },
    { x: width, y: height },
    { x: 0, y: height },
  ].map((corner) => sigma.viewportToGraph(corner));
}

/** Centers the camera on a graph point. */
export function centerOn(sigma: AnySigma, point: Point): void {
  const framed = sigma.viewportToFramedGraph(sigma.graphToViewport(point));
  sigma.getCamera().setState({ x: framed.x, y: framed.y });
}
