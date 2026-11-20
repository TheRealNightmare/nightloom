/**
 * The third axis of the 3D view. x / y come from the same layout as 2D
 * (pins included); z comes from the depth mode:
 *
 * - **Time**: when the entity was first seen, scaled to a fixed depth range
 *   (oldest at the back, newest at the front). Entities without a usable
 *   first-seen date sit on a separate "Undated" plane in front of it.
 * - **Type**: one evenly spaced layer per entity type present, in the
 *   legend's order (ENTITY_TYPES).
 * - **Flat**: z = 0, the 2D picture in space.
 *
 * The range is "fixed" relative to the graph: it scales with the layout's
 * extent (see {@link depthSpan}) so a small and a large graph both look
 * like a block, not a needle or a pancake. Pure: GraphCanvas3D animates
 * between two results.
 */
import { ENTITY_TYPE_INFO, ENTITY_TYPES, type EntityType } from "../../domain/entities";
import type { DepthMode } from "../mode";

export interface DepthInput {
  type: EntityType;
  /** ISO 8601 (an empty or unreadable value counts as undated). */
  firstSeen: string;
}

export interface RulerTick {
  z: number;
  label: string;
}

export interface DepthResult {
  /** z per input, in order. */
  z: Float32Array;
  /** Marks along the depth axis (dates, type names, "Undated"). */
  ticks: RulerTick[];
}

/** The depth range as a share of the layout's diameter, and its smallest size. */
export const DEPTH_RATIO = 0.8;
export const MIN_DEPTH_SPAN = 120;
/** The undated plane sits this share of the range in front of the newest entity. */
export const UNDATED_GAP_RATIO = 0.15;
export const UNDATED_LABEL = "Undated";
/** A change of depth mode takes this long (at once with reduced motion). */
export const DEPTH_ANIMATION_MS = 450;
/** At most this many date marks on the ruler. */
export const MAX_TIME_TICKS = 6;

/** The depth range for a layout of `radius` (graph units). */
export function depthSpan(radius: number): number {
  return Math.max(2 * radius * DEPTH_RATIO, MIN_DEPTH_SPAN);
}

/** Milliseconds since the epoch, or null for an empty or unreadable date. */
export function parseSeen(value: string): number | null {
  if (value.trim() === "") return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

// --- time ticks -------------------------------------------------------------------

const DAY_MS = 86_400_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

interface Step {
  unit: "day" | "month" | "year";
  count: number;
}

/** Candidate spacings between date marks, finest first. */
const STEPS: readonly Step[] = [
  { unit: "day", count: 1 },
  { unit: "day", count: 2 },
  { unit: "day", count: 7 },
  { unit: "day", count: 14 },
  { unit: "month", count: 1 },
  { unit: "month", count: 3 },
  { unit: "month", count: 6 },
  { unit: "year", count: 1 },
  { unit: "year", count: 2 },
  { unit: "year", count: 5 },
  { unit: "year", count: 10 },
  { unit: "year", count: 25 },
  { unit: "year", count: 50 },
  { unit: "year", count: 100 },
];

function approxMs(step: Step): number {
  const unit =
    step.unit === "day" ? DAY_MS : step.unit === "month" ? 30.44 * DAY_MS : 365.25 * DAY_MS;
  return unit * step.count;
}

function formatTick(time: number, step: Step, spansYears: boolean): string {
  const date = new Date(time);
  const year = String(date.getUTCFullYear());
  const month = MONTHS[date.getUTCMonth()] ?? "";
  if (step.unit === "year") return year;
  if (step.unit === "month") return `${month} ${year}`;
  const day = `${String(date.getUTCDate())} ${month}`;
  return spansYears ? `${day} ${year}` : day;
}

/** The first calendar boundary of `step` at or after `time` (UTC). */
function firstBoundary(time: number, step: Step): number {
  const date = new Date(time);
  if (step.unit === "day") {
    const midnight = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
    return midnight < time ? midnight + DAY_MS : midnight;
  }
  if (step.unit === "month") {
    let start = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
    if (start < time) start = Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
    // Align to multiples of `count` months (Jan, Apr, Jul, Oct for quarters).
    let d = new Date(start);
    while (d.getUTCMonth() % step.count !== 0) {
      d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1));
    }
    return d.getTime();
  }
  let year = date.getUTCFullYear();
  if (Date.UTC(year, 0, 1) < time) year += 1;
  while (year % step.count !== 0) year += 1;
  return Date.UTC(year, 0, 1);
}

function nextBoundary(time: number, step: Step): number {
  const date = new Date(time);
  if (step.unit === "day") return time + step.count * DAY_MS;
  if (step.unit === "month") {
    return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + step.count, 1);
  }
  return Date.UTC(date.getUTCFullYear() + step.count, 0, 1);
}

/** Calendar-aligned date marks between `min` and `max` (ms), at most `maxTicks`. */
export function timeTicks(
  min: number,
  max: number,
  maxTicks = MAX_TIME_TICKS,
): { time: number; label: string }[] {
  const spansYears = new Date(min).getUTCFullYear() !== new Date(max).getUTCFullYear();
  if (max - min < 1) {
    return [{ time: min, label: formatTick(min, { unit: "day", count: 1 }, true) }];
  }
  const range = max - min;
  const step = STEPS.find((candidate) => range / approxMs(candidate) <= maxTicks - 1) ??
    STEPS[STEPS.length - 1] ?? { unit: "year", count: 100 };
  const ticks: { time: number; label: string }[] = [];
  for (let time = firstBoundary(min, step); time <= max; time = nextBoundary(time, step)) {
    ticks.push({ time, label: formatTick(time, step, spansYears) });
    if (ticks.length > maxTicks * 2) break;
  }
  if (ticks.length === 0) {
    // A range shorter than one step: mark both ends (once when they read the same).
    const fine = { unit: "day" as const, count: 1 };
    const first = { time: min, label: formatTick(min, fine, spansYears) };
    const last = { time: max, label: formatTick(max, fine, spansYears) };
    return first.label === last.label ? [first] : [first, last];
  }
  return ticks;
}

// --- modes ------------------------------------------------------------------------

/** Depth by first seen: oldest at the back (−span / 2), newest at the front, undated in front of all. */
export function timeDepth(nodes: readonly DepthInput[], span: number): DepthResult {
  const z = new Float32Array(nodes.length);
  const times = nodes.map((node) => parseSeen(node.firstSeen));
  let min = Infinity;
  let max = -Infinity;
  for (const time of times) {
    if (time === null) continue;
    if (time < min) min = time;
    if (time > max) max = time;
  }
  const half = span / 2;
  const undatedZ = half + span * UNDATED_GAP_RATIO;
  const toZ = (time: number) => (max > min ? -half + ((time - min) / (max - min)) * span : 0);
  let undated = false;
  for (const [index, time] of times.entries()) {
    if (time === null) {
      undated = true;
      z[index] = undatedZ;
    } else {
      z[index] = toZ(time);
    }
  }
  const ticks: RulerTick[] =
    min === Infinity ? [] : timeTicks(min, max).map(({ time, label }) => ({ z: toZ(time), label }));
  if (undated) ticks.push({ z: undatedZ, label: UNDATED_LABEL });
  return { z, ticks };
}

/** Depth by type: the types present, evenly spaced back to front in the legend's order. */
export function typeDepth(nodes: readonly DepthInput[], span: number): DepthResult {
  const present = new Set(nodes.map((node) => node.type));
  const layers = ENTITY_TYPES.filter((type) => present.has(type));
  const half = span / 2;
  const zOf = new Map<EntityType, number>(
    layers.map((type, index) => [
      type,
      layers.length > 1 ? -half + (index * span) / (layers.length - 1) : 0,
    ]),
  );
  const z = Float32Array.from(nodes, (node) => zOf.get(node.type) ?? 0);
  const ticks = layers.map((type) => ({
    z: zOf.get(type) ?? 0,
    label: ENTITY_TYPE_INFO[type].plural,
  }));
  return { z, ticks };
}

export function depthOf(mode: DepthMode, nodes: readonly DepthInput[], span: number): DepthResult {
  if (mode === "time") return timeDepth(nodes, span);
  if (mode === "type") return typeDepth(nodes, span);
  return { z: new Float32Array(nodes.length), ticks: [] };
}

/** `from` → `to` at `t` (0..1) into `out` (same lengths). */
export function blendDepth(
  from: ArrayLike<number>,
  to: ArrayLike<number>,
  t: number,
  out: Float32Array,
): void {
  const k = Math.min(Math.max(t, 0), 1);
  for (let i = 0; i < out.length; i += 1) {
    const a = from[i] ?? 0;
    out[i] = a + ((to[i] ?? 0) - a) * k;
  }
}

/** How long a depth change animates. */
export function depthAnimationMs(reducedMotion: boolean): number {
  return reducedMotion ? 0 : DEPTH_ANIMATION_MS;
}
