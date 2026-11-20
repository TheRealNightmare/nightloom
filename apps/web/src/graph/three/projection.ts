/**
 * Screen ↔ world for the 3D view, the same perspective camera three.js
 * draws with (column-major matrices, NDC in [-1, 1], y up), as plain
 * arithmetic so the label pass, picking and box selection can project
 * thousands of points per frame and be unit-tested without WebGL.
 */
import type { Point } from "../geometry";
import {
  add,
  basisOf,
  distanceOf,
  dot,
  normalize,
  scale,
  sub,
  TAN_HALF_FOV,
  type Camera3D,
  type Vec3,
} from "./camera3d";

export interface Viewport {
  width: number;
  height: number;
}

/** Near and far clip planes for a camera at `distance` from a scene of `radius`. */
export function clipPlanes(distance: number, radius: number): { near: number; far: number } {
  const near = Math.max(distance / 1000, 0.05);
  return { near, far: Math.max(distance + 4 * radius + 100, near * 10) };
}

/**
 * The view-projection matrix (projection × view, column-major, 16 numbers)
 * of `camera` on a viewport of `aspect` (width / height).
 */
export function viewProjection(
  camera: Camera3D,
  aspect: number,
  near: number,
  far: number,
): Float64Array {
  const { forward: f, right: r, up: u } = basisOf(camera);
  const e = camera.position;
  // View matrix rows: right, up, -forward; translation -R·eye.
  const v = [
    [r[0], r[1], r[2], -dot(r, e)],
    [u[0], u[1], u[2], -dot(u, e)],
    [-f[0], -f[1], -f[2], dot(f, e)],
  ] as const;
  const sy = 1 / TAN_HALF_FOV;
  const sx = sy / Math.max(aspect, 1e-6);
  const a = -(far + near) / (far - near);
  const b = (-2 * far * near) / (far - near);
  // P × V, written out (P is sparse).
  const m = new Float64Array(16);
  for (let col = 0; col < 4; col += 1) {
    const v0 = v[0][col] ?? 0;
    const v1 = v[1][col] ?? 0;
    const v2 = v[2][col] ?? 0;
    const v3 = col === 3 ? 1 : 0;
    m[col * 4] = sx * v0;
    m[col * 4 + 1] = sy * v1;
    m[col * 4 + 2] = a * v2 + b * v3;
    m[col * 4 + 3] = -v2;
  }
  return m;
}

export interface Projected {
  /** Screen pixels (y down). */
  x: number;
  y: number;
  /** Distance in front of the camera along its view direction (≤ 0: behind it). */
  depth: number;
}

/** Where a world point lands on the screen (with `depth` ≤ 0 when it is behind the camera). */
export function project(m: ArrayLike<number>, point: Vec3, viewport: Viewport): Projected {
  const [x, y, z] = point;
  const cx = (m[0] ?? 0) * x + (m[4] ?? 0) * y + (m[8] ?? 0) * z + (m[12] ?? 0);
  const cy = (m[1] ?? 0) * x + (m[5] ?? 0) * y + (m[9] ?? 0) * z + (m[13] ?? 0);
  const w = (m[3] ?? 0) * x + (m[7] ?? 0) * y + (m[11] ?? 0) * z + (m[15] ?? 0);
  const inv = w > 1e-9 ? 1 / w : 0;
  return {
    x: (cx * inv * 0.5 + 0.5) * viewport.width,
    y: (0.5 - cy * inv * 0.5) * viewport.height,
    depth: w,
  };
}

/**
 * Projects `count` points (x, y, z triples) into `out` as (screen x, screen
 * y, depth) triples: one pass, no allocation, for the per-frame label pass.
 */
export function projectAll(
  m: ArrayLike<number>,
  positions: ArrayLike<number>,
  count: number,
  viewport: Viewport,
  out: Float32Array,
): void {
  const m0 = m[0] ?? 0;
  const m1 = m[1] ?? 0;
  const m3 = m[3] ?? 0;
  const m4 = m[4] ?? 0;
  const m5 = m[5] ?? 0;
  const m7 = m[7] ?? 0;
  const m8 = m[8] ?? 0;
  const m9 = m[9] ?? 0;
  const m11 = m[11] ?? 0;
  const m12 = m[12] ?? 0;
  const m13 = m[13] ?? 0;
  const m15 = m[15] ?? 0;
  const hw = viewport.width / 2;
  const hh = viewport.height / 2;
  for (let i = 0; i < count; i += 1) {
    const x = positions[i * 3] ?? 0;
    const y = positions[i * 3 + 1] ?? 0;
    const z = positions[i * 3 + 2] ?? 0;
    const w = m3 * x + m7 * y + m11 * z + m15;
    const inv = w > 1e-9 ? 1 / w : 0;
    out[i * 3] = (m0 * x + m4 * y + m8 * z + m12) * inv * hw + hw;
    out[i * 3 + 1] = hh - (m1 * x + m5 * y + m9 * z + m13) * inv * hh;
    out[i * 3 + 2] = w;
  }
}

export interface Ray {
  origin: Vec3;
  /** Unit length. */
  direction: Vec3;
}

/** The ray from the camera through a screen point. */
export function screenRay(camera: Camera3D, point: Point, viewport: Viewport): Ray {
  const { forward, right, up } = basisOf(camera);
  const aspect = viewport.width / Math.max(viewport.height, 1);
  const ndcX = (point.x / Math.max(viewport.width, 1)) * 2 - 1;
  const ndcY = 1 - (point.y / Math.max(viewport.height, 1)) * 2;
  const direction = normalize(
    add(forward, add(scale(right, ndcX * TAN_HALF_FOV * aspect), scale(up, ndcY * TAN_HALF_FOV))),
  );
  return { origin: [...camera.position], direction };
}

/** Where `ray` meets the plane through `point` with `normal` (null: parallel, or behind the ray). */
export function intersectPlane(ray: Ray, point: Vec3, normal: Vec3): Vec3 | null {
  const denominator = dot(ray.direction, normal);
  if (Math.abs(denominator) < 1e-9) return null;
  const t = dot(sub(point, ray.origin), normal) / denominator;
  if (t < 0) return null;
  return add(ray.origin, scale(ray.direction, t));
}

/**
 * The world point under a screen point, on the plane through `anchor`
 * facing the camera (the target, by default): what the wheel zooms toward.
 */
export function unproject(
  camera: Camera3D,
  point: Point,
  viewport: Viewport,
  anchor: Vec3 = camera.target,
): Vec3 {
  const { forward } = basisOf(camera);
  return intersectPlane(screenRay(camera, point, viewport), anchor, forward) ?? [...camera.target];
}

/** Distance from the camera to `point` along its view direction. */
export function depthOf(camera: Camera3D, point: Vec3): number {
  return dot(sub(point, camera.position), basisOf(camera).forward);
}

export { distanceOf };
