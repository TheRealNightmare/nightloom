/**
 * Screen-space geometry for the graph's interactions: rotation steps and
 * drags, selection rectangles, and which nodes a rectangle holds.
 *
 * The selection rectangle is drawn in screen pixels and stays upright on the
 * screen, whatever the camera's rotation; nodes are tested at their on-screen
 * positions (the caller projects them with sigma's graphToViewport), so a
 * rotated view selects exactly what the rectangle covers.
 */

export interface Point {
  x: number;
  y: number;
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** One press of ⟲ / ⟳ or Q / E: 15°. */
export const ROTATE_STEP = Math.PI / 12;
const TURN = 2 * Math.PI;

/** The same angle in (−π, π]. */
export function normalizeAngle(angle: number): number {
  let result = angle % TURN;
  if (result <= -Math.PI) result += TURN;
  if (result > Math.PI) result -= TURN;
  return result;
}

/**
 * The camera angle after `steps` rotation steps (positive: clockwise on
 * screen, the way the content turns when dragged clockwise).
 */
export function rotateBySteps(angle: number, steps: number): number {
  return normalizeAngle(angle + steps * ROTATE_STEP);
}

/** `point` rotated by `angle` (radians, clockwise on screen, where y points down) around `center`. */
export function rotatePoint(point: Point, angle: number, center: Point = { x: 0, y: 0 }): Point {
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const dx = point.x - center.x;
  const dy = point.y - center.y;
  return { x: center.x + dx * cos - dy * sin, y: center.y + dx * sin + dy * cos };
}

/**
 * The camera angle while dragging from `start` to `current` around `center`
 * (screen pixels): the content turns with the pointer, as sigma's own
 * right-button rotation does.
 */
export function dragRotation(
  center: Point,
  start: Point,
  current: Point,
  startAngle: number,
): number {
  const from = Math.atan2(start.y - center.y, start.x - center.x);
  const to = Math.atan2(current.y - center.y, current.x - center.x);
  return normalizeAngle(startAngle + (to - from));
}

/** The upright rectangle with corners `a` and `b`. */
export function rectFromPoints(a: Point, b: Point): Rect {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(a.x - b.x),
    height: Math.abs(a.y - b.y),
  };
}

export function rectContains(rect: Rect, point: Point): boolean {
  return (
    point.x >= rect.x &&
    point.x <= rect.x + rect.width &&
    point.y >= rect.y &&
    point.y <= rect.y + rect.height
  );
}

/** A drag shorter than this (pixels) is a click, not a rectangle. */
export const MIN_RECT_SIZE = 4;

export function isRectTooSmall(rect: Rect): boolean {
  return rect.width < MIN_RECT_SIZE && rect.height < MIN_RECT_SIZE;
}

/**
 * The nodes whose on-screen position lies in `rect`. `toScreen` projects a
 * node to screen pixels (sigma's graphToViewport for the current camera,
 * rotation included); `nodes` are the candidates (the visible ones).
 */
export function nodesInRect<T extends string>(
  nodes: Iterable<T>,
  toScreen: (node: T) => Point | null,
  rect: Rect,
): T[] {
  const result: T[] = [];
  for (const node of nodes) {
    const point = toScreen(node);
    if (point && rectContains(rect, point)) result.push(node);
  }
  return result;
}

/**
 * A projection like sigma's camera: graph point → screen, for a camera at
 * `center` (graph units) zoomed by `scale` (pixels per graph unit) and
 * rotated by `angle`, on a viewport of `size`. Used by the tests to check
 * rectangle selection under rotation without WebGL.
 */
export function cameraProjection(
  center: Point,
  angle: number,
  scale: number,
  size: { width: number; height: number },
): (point: Point) => Point {
  const middle = { x: size.width / 2, y: size.height / 2 };
  return (point) => {
    const offset = { x: (point.x - center.x) * scale, y: -(point.y - center.y) * scale };
    const turned = rotatePoint(offset, angle);
    return { x: middle.x + turned.x, y: middle.y + turned.y };
  };
}
