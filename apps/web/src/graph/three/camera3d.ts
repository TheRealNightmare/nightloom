/**
 * The 3D camera as plain numbers, and everything that moves it: orbit around
 * the target, pan, zoom (toward a point), framing a sphere and the
 * in-between cameras of an animation. Pure functions, so the keyboard, the
 * wheel, fly-to and saved views share one tested model; GraphCanvas3D copies
 * the result onto the three.js camera.
 */
import { normalizeAngle } from "../geometry";
import type { GraphCamera } from "../interactions";
import type { Camera3D, Vec3 } from "../mode";

export type { Camera3D, Vec3 } from "../mode";

/** Vertical field of view of the 3D camera (degrees). */
export const FOV_DEGREES = 45;

// --- vectors ----------------------------------------------------------------------

export const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const scale = (a: Vec3, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];
export const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
export const length = (a: Vec3): number => Math.hypot(a[0], a[1], a[2]);
export const lerp = (a: Vec3, b: Vec3, t: number): Vec3 => [
  a[0] + (b[0] - a[0]) * t,
  a[1] + (b[1] - a[1]) * t,
  a[2] + (b[2] - a[2]) * t,
];

export function normalize(a: Vec3, fallback: Vec3 = [0, 0, 1]): Vec3 {
  const n = length(a);
  return n < 1e-12 ? fallback : [a[0] / n, a[1] / n, a[2] / n];
}

/** `v` rotated by `angle` radians around the unit `axis` (right-handed; Rodrigues). */
export function rotateAround(v: Vec3, axis: Vec3, angle: number): Vec3 {
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const k = cross(axis, v);
  const d = dot(axis, v) * (1 - cos);
  return [
    v[0] * cos + k[0] * sin + axis[0] * d,
    v[1] * cos + k[1] * sin + axis[1] * d,
    v[2] * cos + k[2] * sin + axis[2] * d,
  ];
}

// --- camera basis -----------------------------------------------------------------

export const TAN_HALF_FOV = Math.tan((FOV_DEGREES * Math.PI) / 360);
/** The camera never gets closer to its target than this, nor further than MAX_DISTANCE. */
export const MIN_DISTANCE = 0.5;
export const MAX_DISTANCE = 2e5;
/** Coordinates of a saved camera stay within this (like pinned positions). */
export const MAX_COORDINATE = 1e6;
/** Orbiting stops this far (radians) from looking straight along the up axis. */
export const POLAR_MARGIN = 0.05;

export interface Basis {
  /** From the camera toward its target. */
  forward: Vec3;
  /** Screen right. */
  right: Vec3;
  /** Screen up (perpendicular to forward). */
  up: Vec3;
}

export function distanceOf(camera: Camera3D): number {
  return length(sub(camera.position, camera.target));
}

export function basisOf(camera: Camera3D): Basis {
  const forward = normalize(sub(camera.target, camera.position), [0, 0, -1]);
  let right = cross(forward, camera.up);
  if (length(right) < 1e-9) right = cross(forward, [0, 0, 1]);
  if (length(right) < 1e-9) right = cross(forward, [1, 0, 0]);
  right = normalize(right);
  return { forward, right, up: cross(right, forward) };
}

/** Graph units covered by one screen pixel at `distance` from the camera. */
export function worldPerPixel(distance: number, viewportHeight: number): number {
  return (2 * distance * TAN_HALF_FOV) / Math.max(viewportHeight, 1);
}

// --- moves ------------------------------------------------------------------------

/**
 * Orbits around the target: `azimuth` radians around the camera's up axis
 * (positive: the camera moves to its right), then `polar` radians toward
 * the up axis (positive: the camera rises), stopping short of the poles.
 */
export function orbit(camera: Camera3D, azimuth: number, polar: number): Camera3D {
  const axis = normalize(camera.up, [0, 1, 0]);
  let offset = sub(camera.position, camera.target);
  offset = rotateAround(offset, axis, azimuth);
  if (polar !== 0) {
    const distance = length(offset);
    const current = Math.acos(Math.min(Math.max(dot(normalize(offset), axis), -1), 1));
    const next = Math.min(Math.max(current - polar, POLAR_MARGIN), Math.PI - POLAR_MARGIN);
    const side = cross(axis, offset);
    if (length(side) > 1e-9 && distance > 0) {
      offset = rotateAround(offset, normalize(side), next - current);
    }
  }
  return { ...camera, position: add(camera.target, offset) };
}

/**
 * Moves the camera and its target by `dx`, `dy` screen pixels (screen
 * directions; y down): the view shows what lies that way, like 2D panning.
 */
export function pan(camera: Camera3D, dx: number, dy: number, viewportHeight: number): Camera3D {
  const { right, up } = basisOf(camera);
  const unit = worldPerPixel(distanceOf(camera), viewportHeight);
  const shift = add(scale(right, dx * unit), scale(up, -dy * unit));
  return { ...camera, position: add(camera.position, shift), target: add(camera.target, shift) };
}

function clampFactor(camera: Camera3D, factor: number): number {
  const distance = distanceOf(camera);
  if (distance <= 0) return 1;
  const next = Math.min(Math.max(distance * factor, MIN_DISTANCE), MAX_DISTANCE);
  return next / distance;
}

/** Moves the camera toward (factor < 1) or away from its target. */
export function dolly(camera: Camera3D, factor: number): Camera3D {
  const f = clampFactor(camera, factor);
  return {
    ...camera,
    position: add(camera.target, scale(sub(camera.position, camera.target), f)),
  };
}

/**
 * Zooms by `factor` toward `point`: camera and target move along the lines
 * to it, so `point` stays where it is on the screen (the wheel zooms toward
 * the pointer this way).
 */
export function dollyToward(camera: Camera3D, point: Vec3, factor: number): Camera3D {
  const f = clampFactor(camera, factor);
  return {
    ...camera,
    position: add(point, scale(sub(camera.position, point), f)),
    target: add(point, scale(sub(camera.target, point), f)),
  };
}

/** Moves the camera with its target so that it looks at `target` (same angle and distance). */
export function moveTarget(camera: Camera3D, target: Vec3): Camera3D {
  const shift = sub(target, camera.target);
  return { ...camera, position: add(camera.position, shift), target };
}

// --- framing ----------------------------------------------------------------------

export interface Sphere {
  center: Vec3;
  radius: number;
}

/** The smallest sphere (around the box's center) holding every point of `positions` (x, y, z triples). */
export function boundingSphere(positions: ArrayLike<number>, count: number): Sphere {
  if (count === 0) return { center: [0, 0, 0], radius: 1 };
  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < count; i += 1) {
    const x = positions[i * 3] ?? 0;
    const y = positions[i * 3 + 1] ?? 0;
    const z = positions[i * 3 + 2] ?? 0;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (z < minZ) minZ = z;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
    if (z > maxZ) maxZ = z;
  }
  const center: Vec3 = [(minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2];
  let radius = 0;
  for (let i = 0; i < count; i += 1) {
    const d = Math.hypot(
      (positions[i * 3] ?? 0) - center[0],
      (positions[i * 3 + 1] ?? 0) - center[1],
      (positions[i * 3 + 2] ?? 0) - center[2],
    );
    if (d > radius) radius = d;
  }
  return { center, radius: Math.max(radius, 1) };
}

/** Room left around a framed sphere (1 = touching the edges). */
export const FRAME_MARGIN = 1.15;
/**
 * The direction (from the target toward the camera) of a reset view: a
 * little to the side and above, so the depth axis is visible at once.
 */
export const RESET_DIRECTION: Vec3 = normalize([-0.42, 0.26, 1]);

/**
 * The camera that shows the whole `sphere`, looking from `direction` (unit,
 * from the target toward the camera) with `up`, on a viewport of `aspect`
 * (width / height).
 */
export function frameSphere(
  sphere: Sphere,
  aspect: number,
  direction: Vec3 = RESET_DIRECTION,
  up: Vec3 = [0, 1, 0],
): Camera3D {
  const halfV = Math.atan(TAN_HALF_FOV);
  const halfH = Math.atan(TAN_HALF_FOV * Math.max(aspect, 1e-3));
  const half = Math.min(halfV, halfH);
  const distance = Math.min(
    Math.max((sphere.radius * FRAME_MARGIN) / Math.sin(half), MIN_DISTANCE),
    MAX_DISTANCE,
  );
  return {
    position: add(sphere.center, scale(normalize(direction), distance)),
    target: [...sphere.center],
    up: [...up],
  };
}

/**
 * The camera of a fly-to: looking at `point` from the current direction,
 * at `distance` (closer than now, never further).
 */
export function flyTo(camera: Camera3D, point: Vec3, distance: number): Camera3D {
  const direction = normalize(sub(camera.position, camera.target));
  const d = Math.min(Math.max(distance, MIN_DISTANCE), Math.max(distanceOf(camera), MIN_DISTANCE));
  return { ...camera, target: [...point], position: add(point, scale(direction, d)) };
}

/**
 * The distance (along the current viewing direction, from the target) at
 * which every point of `points` (x, y, z triples, the `indices` given) is
 * on screen with `margin` to spare, on a viewport of `aspect`. Points
 * behind the target need less, points in front of it more (perspective).
 */
export function distanceToShow(
  camera: Camera3D,
  points: ArrayLike<number>,
  indices: Iterable<number>,
  aspect: number,
  margin = FRAME_MARGIN,
): number {
  const { forward, right, up } = basisOf(camera);
  let needed = 0;
  for (const i of indices) {
    const offset: Vec3 = [
      (points[i * 3] ?? 0) - camera.target[0],
      (points[i * 3 + 1] ?? 0) - camera.target[1],
      (points[i * 3 + 2] ?? 0) - camera.target[2],
    ];
    const along = dot(offset, forward);
    const horizontal =
      (Math.abs(dot(offset, right)) * margin) / (TAN_HALF_FOV * Math.max(aspect, 1e-3));
    const vertical = (Math.abs(dot(offset, up)) * margin) / TAN_HALF_FOV;
    needed = Math.max(needed, horizontal - along, vertical - along);
  }
  return Math.min(needed, MAX_DISTANCE);
}

/**
 * Whether graph point (`x`, `y`) is inside what the 2D `camera` shows on a
 * viewport of `width` × `height` pixels (its rotation included).
 */
export function inView2d(
  camera: GraphCamera,
  x: number,
  y: number,
  width: number,
  height: number,
): boolean {
  const dx = x - camera.x;
  const dy = y - camera.y;
  const cos = Math.cos(camera.angle);
  const sin = Math.sin(camera.angle);
  const u = (dx * cos + dy * sin) / camera.ratio;
  const v = (-dx * sin + dy * cos) / camera.ratio;
  return Math.abs(u) <= width / 2 && Math.abs(v) <= height / 2;
}

// --- animation --------------------------------------------------------------------

export function easeInOut(t: number): number {
  const c = Math.min(Math.max(t, 0), 1);
  return c < 0.5 ? 2 * c * c : 1 - (-2 * c + 2) ** 2 / 2;
}

/**
 * The camera at `t` (0..1) between `from` and `to`: the target moves in a
 * straight line, the viewing direction and up turn smoothly, and the
 * distance changes geometrically (a zoom feels even).
 */
export function interpolateCamera(from: Camera3D, to: Camera3D, t: number): Camera3D {
  if (t <= 0) return from;
  if (t >= 1) return to;
  const target = lerp(from.target, to.target, t);
  const d0 = Math.max(distanceOf(from), 1e-9);
  const d1 = Math.max(distanceOf(to), 1e-9);
  const distance = d0 * (d1 / d0) ** t;
  const dir0 = normalize(sub(from.position, from.target));
  const dir1 = normalize(sub(to.position, to.target));
  const direction = normalize(lerp(dir0, dir1, t), dir1);
  const up = normalize(lerp(from.up, to.up, t), to.up);
  return { position: add(target, scale(direction, distance)), target, up };
}

// --- 2D ↔ 3D -------------------------------------------------------------------------

/**
 * The 3D camera that shows what `camera` shows in 2D: straight down onto the
 * graph's plane, at the distance where one pixel covers `ratio` graph units,
 * with the 2D rotation as the camera's up direction.
 */
export function camera2dTo3d(camera: GraphCamera, viewportHeight: number): Camera3D {
  const distance = Math.max((camera.ratio * viewportHeight) / (2 * TAN_HALF_FOV), 1e-3);
  return {
    position: [camera.x, camera.y, distance],
    target: [camera.x, camera.y, 0],
    up: [-Math.sin(camera.angle), Math.cos(camera.angle), 0],
  };
}

/**
 * The 2D camera closest to `camera`: centered where it looks, at the scale of
 * its target's distance, rotated like its up direction seen from the front.
 */
export function camera3dTo2d(camera: Camera3D, viewportHeight: number): GraphCamera {
  const [tx, ty] = camera.target;
  const ratio = worldPerPixel(distanceOf(camera), viewportHeight);
  const [ux, uy] = camera.up;
  const angle = Math.hypot(ux, uy) < 1e-6 ? 0 : normalizeAngle(Math.atan2(-ux, uy));
  return { x: tx, y: ty, ratio: Math.max(ratio, 1e-6), angle };
}

// --- validity ---------------------------------------------------------------------

function finiteVec(value: unknown, bound: number): value is Vec3 {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((n) => typeof n === "number" && Number.isFinite(n) && Math.abs(n) <= bound)
  );
}

/** Whether `value` is a usable camera (finite, within bounds, up not zero, not at its target). */
export function isCamera3D(value: unknown): value is Camera3D {
  if (typeof value !== "object" || value === null) return false;
  const { position, target, up } = value as Record<string, unknown>;
  return (
    finiteVec(position, MAX_COORDINATE) &&
    finiteVec(target, MAX_COORDINATE) &&
    finiteVec(up, 1) &&
    length(up) > 1e-6 &&
    length(sub(position, target)) > 1e-6
  );
}

/** `camera` kept within the saved-view bounds (coordinates clamped, up normalized). */
export function boundedCamera(camera: Camera3D): Camera3D {
  const clamp = (v: Vec3): Vec3 =>
    v.map((n) =>
      Number.isFinite(n) ? Math.min(Math.max(n, -MAX_COORDINATE), MAX_COORDINATE) : 0,
    ) as Vec3;
  const position = clamp(camera.position);
  let target = clamp(camera.target);
  if (length(sub(position, target)) <= 1e-6) target = [target[0], target[1], target[2] - 1];
  return { position, target, up: normalize(clamp(camera.up), [0, 1, 0]) };
}
