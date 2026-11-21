/**
 * The 3D helpers at their edges: degenerate cameras, ranges that fit no
 * calendar step, rays that miss, and inputs shorter than the count they
 * come with (read as zeros, never NaN).
 */
import { describe, expect, it } from "vitest";

import {
  basisOf,
  boundingSphere,
  distanceToShow,
  dolly,
  orbit,
  type Camera3D,
  type Vec3,
} from "./camera3d";
import { blendDepth, timeTicks } from "./depth";
import { keyAction3d } from "./keyboard3d";
import { dragDelta, focusOrder3d, nodesInScreenRect, pickNode } from "./picking";
import { project, projectAll } from "./projection";

const VIEWPORT = { width: 800, height: 600 };
const finite = (values: ArrayLike<number>) => Array.from(values).every(Number.isFinite);

/** A sparse array-like: only the given indices are set. */
function sparse(length: number, values: Record<number, number>): ArrayLike<number> {
  return { length, ...values };
}

describe("camera edge cases", () => {
  it("finds a basis when 'up' is parallel to the view direction", () => {
    const camera: Camera3D = { position: [0, 0, 10], target: [0, 0, 0], up: [0, 0, 1] };
    const { forward, right, up } = basisOf(camera);
    expect(forward).toEqual([0, 0, -1]);
    expect(Math.hypot(...right)).toBeCloseTo(1);
    expect(Math.hypot(...up)).toBeCloseTo(1);
  });

  it("leaves a camera on its own target where it is", () => {
    const camera: Camera3D = { position: [1, 2, 3], target: [1, 2, 3], up: [0, 1, 0] };
    expect(orbit(camera, 0, 0.3).position).toEqual([1, 2, 3]);
    expect(dolly(camera, 2)).toEqual(camera);
  });

  it("reads missing coordinates as zero", () => {
    const sphere = boundingSphere(sparse(3, { 0: 4 }), 2);
    expect(sphere.center).toEqual([2, 0, 0]);
    expect(sphere.radius).toBe(2);
    const camera: Camera3D = { position: [0, 0, 100], target: [0, 0, 0], up: [0, 1, 0] };
    expect(Number.isFinite(distanceToShow(camera, [], [0, 1], 1.5))).toBe(true);
  });
});

describe("projection edge cases", () => {
  it("projects with a short matrix as if the missing entries were zero", () => {
    const p = project([], [1, 2, 3], VIEWPORT);
    expect(p).toEqual({ x: 400, y: 300, depth: 0 });
    const out = new Float32Array(6);
    projectAll([], sparse(2, { 0: 1 }), 2, VIEWPORT, out);
    expect(finite(out)).toBe(true);
    expect(out[0]).toBe(400);
  });
});

describe("picking edge cases", () => {
  it("copes with missing projections, radii and ids", () => {
    const nodes = { ids: [], projected: sparse(3, { 2: 10 }), radii: [], count: 2 };
    // The first node is at (0, 0) with no radius: hit within the slop, but it has no id.
    expect(pickNode(nodes, { x: 1, y: 1 })).toBeNull();
    expect(nodesInScreenRect(nodes, { x: -5, y: -5, width: 10, height: 10 })).toEqual([]);
  });

  it("orders focus by depth band, then rows, then x, then id", () => {
    const order = focusOrder3d(
      [
        { id: "far", point: { x: 10, y: 10 }, depth: 100 },
        { id: "b", point: { x: 50, y: 10 }, depth: 1 },
        { id: "a", point: { x: 50, y: 10 }, depth: 1 },
        { id: "left", point: { x: 5, y: 12 }, depth: 1 },
        { id: "lower", point: { x: 1, y: 300 }, depth: 1 },
        { id: "behind", point: { x: 1, y: 1 }, depth: -1 },
        { id: "off", point: { x: 1000, y: 1 }, depth: 5 },
      ],
      VIEWPORT,
    );
    expect(order).toEqual(["left", "a", "b", "lower", "far"]);
    expect(focusOrder3d([], VIEWPORT)).toEqual([]);
  });

  it("returns no drag when the plane is behind the camera", () => {
    const camera: Camera3D = { position: [0, 0, 100], target: [0, 0, 0], up: [0, 1, 0] };
    const behind: Vec3 = [0, 0, 200];
    expect(dragDelta(camera, VIEWPORT, behind, { x: 400, y: 300 }, { x: 410, y: 300 })).toBeNull();
    const moved = dragDelta(camera, VIEWPORT, [0, 0, 0], { x: 400, y: 300 }, { x: 410, y: 300 });
    expect(moved?.dx).toBeGreaterThan(0);
  });
});

describe("depth edge cases", () => {
  const DAY = 86_400_000;

  it("marks a span shorter than a day once when both ends read the same", () => {
    const start = Date.UTC(2026, 3, 10, 6);
    expect(timeTicks(start, start + 3 * 3_600_000)).toEqual([{ time: start, label: "10 Apr" }]);
  });

  it("marks both ends of a short span across midnight-free days that read differently", () => {
    const start = Date.UTC(2025, 11, 31, 23, 30);
    const ticks = timeTicks(start + 1, start + 20 * 60_000);
    expect(ticks.length).toBeGreaterThanOrEqual(1);
  });

  it("starts year marks on 1 January itself, and caps marks on very long ranges", () => {
    const from = Date.UTC(2000, 0, 1);
    const yearly = timeTicks(from, Date.UTC(2005, 0, 1));
    expect(yearly[0]).toEqual({ time: from, label: "2000" });

    // Longer than any step fits: the coarsest step, and no more than twice the cap.
    const ages = timeTicks(Date.UTC(-5000, 0, 1), Date.UTC(5000, 0, 1), 3);
    expect(ages.length).toBeGreaterThan(0);
    expect(ages.length).toBeLessThanOrEqual(7);
    expect(ages[1]!.time - ages[0]!.time).toBeGreaterThan(99 * 365 * DAY);
  });

  it("blends arrays shorter than the output as zeros", () => {
    const out = new Float32Array(3);
    blendDepth([10], [], 0.5, out);
    expect(Array.from(out)).toEqual([5, 0, 0]);
  });
});

describe("keyboard edge cases", () => {
  it("leaves unknown keys and modified keys to the browser", () => {
    const key = (k: string, extra: Partial<Parameters<typeof keyAction3d>[0]> = {}) =>
      keyAction3d(
        { key: k, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...extra },
        true,
      );
    expect(key("x")).toBeNull();
    expect(key("ArrowLeft", { altKey: true })).toBeNull();
    expect(key("_")).toEqual({ kind: "zoom", direction: -1 });
    expect(key("=")).toEqual({ kind: "zoom", direction: 1 });
    expect(key("Q")).toEqual({ kind: "rotate", steps: -1 });
    expect(key("E")).toEqual({ kind: "rotate", steps: 1 });
    expect(key("P")).toEqual({ kind: "togglePin" });
    expect(key("V")).toEqual({ kind: "views" });
    expect(
      keyAction3d(
        { key: "p", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false },
        false,
      ),
    ).toBeNull();
  });
});
