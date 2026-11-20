/**
 * The sign-in loom, as plain numbers: warp threads running away from the
 * viewer, a weft thread passing across them over and under, and knots where
 * it crosses some of them. The cloth drifts away from the viewer by one row
 * per pass.
 *
 * Both drawings use this module: the WebGL scene (./loomScene, loaded on
 * demand) fills its buffers from {@link loomFrame} every frame, and the static
 * fallback (src/components/LoomArt.tsx) projects one frame with
 * {@link projectPoint}, so the two show the same loom. No three.js here: this
 * module is part of the sign-in page's initial JavaScript.
 */
import type { EntityType } from "../../domain/entities";

export const LOOM = {
  /** Warp threads, centred on x = 0. */
  warps: 15,
  warpSpacing: 0.6,
  /** Rows of weft on the cloth (the oldest fade into the background). */
  rows: 16,
  rowSpacing: 0.42,
  /** Where the weft is laid: the "fell" of the cloth, nearest the viewer. */
  fellZ: 1.4,
  /** The warp threads' near end (toward the viewer). */
  warpNearZ: 2.6,
  /** How far above and below the warp plane the weft passes. */
  amplitude: 0.028,
  /** Seconds for one pass of the weft across the warp (8–12 s: calm). */
  passSeconds: 10,
  /** Line segments of the weft between two warp threads. */
  samplesPerGap: 6,
  /** The share of crossings that carry a knot. */
  knotChance: 0.1,
  /** The knots' radius in world units, before perspective. */
  knotRadius: 0.06,
  /** How many of the oldest rows fade into the background. */
  fadeRows: 5,
} as const;

/** The far end of the warp: a little beyond the oldest row. */
export const WARP_FAR_Z = LOOM.fellZ - (LOOM.rows + 1) * LOOM.rowSpacing;

/** Entity types whose colors the knots take (the types that have a color of their own). */
export const KNOT_TYPES: readonly EntityType[] = [
  "domain",
  "ip_address",
  "netblock",
  "asn",
  "certificate",
  "url",
  "organization",
  "service",
];

/** The scene's camera: above the cloth, looking along it into the distance. */
export const CAMERA = {
  position: [0, 2.7, 4.4] as const,
  target: [0, -0.5, -2.6] as const,
  fovDegrees: 34,
} as const;

/**
 * Where the clock starts: a full cloth is already woven, and the first pass
 * is under way (so the scene never starts empty).
 */
export const START_SECONDS = LOOM.rows * LOOM.passSeconds + 0.35 * LOOM.passSeconds;

export type Vec3 = readonly [number, number, number];

export interface WeftSegment {
  from: Vec3;
  to: Vec3;
}

export interface WeftRow {
  /** The row's absolute index (the pass that laid it). */
  index: number;
  /** Polyline points, in the order the shuttle laid them. */
  points: Vec3[];
  /** 1 = full color, 0 = the background (the oldest rows fade out). */
  fade: number;
}

export interface Knot {
  position: Vec3;
  /** Index into {@link KNOT_TYPES}. */
  type: number;
  /** 0–1: grows from nothing as the weft passes the crossing. */
  scale: number;
  fade: number;
}

export interface LoomFrame {
  /** Warp threads as [near, far] points. */
  warps: [Vec3, Vec3][];
  rows: WeftRow[];
  knots: Knot[];
}

/** The x of warp `k` (0 … warps − 1). */
export function warpX(k: number): number {
  return (k - (LOOM.warps - 1) / 2) * LOOM.warpSpacing;
}

/** A small deterministic hash in [0, 1) of two integers. */
export function hash01(a: number, b: number): number {
  let h = Math.imul(a ^ 0x2c1b3c6d, 0x297a2d39) ^ Math.imul(b ^ 0x5f356495, 0x6b5f0e3d);
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 13;
  return (h >>> 0) / 4294967296;
}

/** Whether a knot sits where row `row` crosses warp `k`, and its type. */
export function knotAt(row: number, k: number): number | null {
  if (hash01(row, k) >= LOOM.knotChance) return null;
  return Math.floor(hash01(k + 101, row + 7) * KNOT_TYPES.length);
}

/** The weft's height above the warp plane at x, for row `row`: over one warp, under the next. */
export function weftY(row: number, x: number): number {
  const u = (x - warpX(0)) / LOOM.warpSpacing;
  const phase = row % 2 === 0 ? 0 : Math.PI;
  return LOOM.amplitude * Math.cos(Math.PI * u + phase);
}

/** Ease in and out: the shuttle starts and ends each pass slowly. */
export function ease(f: number): number {
  const t = Math.min(Math.max(f, 0), 1);
  return t * t * (3 - 2 * t);
}

/** 1 for young rows, falling to 0 over the last {@link LOOM.fadeRows} rows. */
export function fadeOf(age: number): number {
  return Math.min(Math.max((LOOM.rows - age) / LOOM.fadeRows, 0), 1);
}

/**
 * The loom at `seconds`: every visible row and knot. Rows alternate direction
 * (left to right, then back); the newest row is laid up to the shuttle.
 */
export function loomFrame(seconds: number): LoomFrame {
  const progress = Math.max(seconds, 0) / LOOM.passSeconds;
  const current = Math.floor(progress);
  const laid = ease(progress - current);
  const left = warpX(0);
  const right = warpX(LOOM.warps - 1);
  const span = right - left;
  const steps = (LOOM.warps - 1) * LOOM.samplesPerGap;

  const warps: [Vec3, Vec3][] = [];
  for (let k = 0; k < LOOM.warps; k++) {
    const x = warpX(k);
    warps.push([
      [x, 0, LOOM.warpNearZ],
      [x, 0, WARP_FAR_Z],
    ]);
  }

  const rows: WeftRow[] = [];
  const knots: Knot[] = [];
  for (let index = current; index > current - LOOM.rows && index >= 0; index--) {
    const age = progress - index;
    const z = LOOM.fellZ - age * LOOM.rowSpacing;
    const fade = fadeOf(age);
    if (fade <= 0) continue;
    const forward = index % 2 === 0;
    const extent = index === current ? laid : 1;
    const points: Vec3[] = [];
    const last = Math.round(steps * extent);
    for (let s = 0; s <= last; s++) {
      const along = (s / steps) * span;
      const x = forward ? left + along : right - along;
      points.push([x, weftY(index, x), z]);
    }
    // The shuttle's exact position, between two samples.
    if (index === current && last < steps) {
      const along = extent * span;
      const x = forward ? left + along : right - along;
      points.push([x, weftY(index, x), z]);
    }
    if (points.length >= 2 && extent > 0) rows.push({ index, points, fade });

    for (let k = 0; k < LOOM.warps; k++) {
      const type = knotAt(index, k);
      if (type === null) continue;
      const x = warpX(k);
      // How far past this crossing the shuttle is (in warp gaps).
      const passed =
        index === current ? (forward ? x - left : right - x) <= extent * span + 1e-9 : true;
      if (!passed) continue;
      const behind = index === current ? extent * span - (forward ? x - left : right - x) : span;
      const scale = Math.min(behind / LOOM.warpSpacing, 1);
      knots.push({ position: [x, weftY(index, x), z], type, scale, fade });
    }
  }
  return { warps, rows, knots };
}

/** Width over height of the projected view. */
export interface Projection {
  aspect: number;
}

function sub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function normalize(a: Vec3): Vec3 {
  const length = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / length, a[1] / length, a[2] / length];
}

const FORWARD = normalize(sub(CAMERA.target, CAMERA.position));
const RIGHT = normalize(cross(FORWARD, [0, 1, 0]));
const UP = cross(RIGHT, FORWARD);
const TAN_HALF_FOV = Math.tan((CAMERA.fovDegrees * Math.PI) / 360);

/**
 * A world point in normalized device coordinates (x and y in −1…1 across
 * the view, y up) and its distance along the view direction, with the
 * scene's camera: the static drawing uses this to match the 3D one.
 */
export function projectPoint(
  point: Vec3,
  { aspect }: Projection,
): { x: number; y: number; depth: number } {
  const d = sub(point, CAMERA.position);
  const depth = Math.max(dot(d, FORWARD), 1e-6);
  return {
    x: dot(d, RIGHT) / depth / TAN_HALF_FOV / aspect,
    y: dot(d, UP) / depth / TAN_HALF_FOV,
    depth,
  };
}
