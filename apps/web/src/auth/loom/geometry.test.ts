import { describe, expect, it } from "vitest";

import {
  CAMERA,
  KNOT_TYPES,
  LOOM,
  START_SECONDS,
  WARP_FAR_Z,
  ease,
  fadeOf,
  hash01,
  knotAt,
  loomFrame,
  projectPoint,
  warpX,
  weftY,
} from "./geometry";

describe("the loom's geometry", () => {
  it("centres the warp threads on x = 0", () => {
    expect(warpX(0)).toBeCloseTo(-warpX(LOOM.warps - 1));
    expect(warpX((LOOM.warps - 1) / 2)).toBeCloseTo(0);
    expect(warpX(1) - warpX(0)).toBeCloseTo(LOOM.warpSpacing);
  });

  it("hashes deterministically into [0, 1)", () => {
    const values = Array.from({ length: 2000 }, (_, i) => hash01(i, i * 7 + 3));
    expect(values.every((v) => v >= 0 && v < 1)).toBe(true);
    expect(hash01(4, 9)).toBe(hash01(4, 9));
    expect(hash01(4, 9)).not.toBe(hash01(9, 4));
    // Spread out, not clumped.
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    expect(mean).toBeGreaterThan(0.4);
    expect(mean).toBeLessThan(0.6);
  });

  it("knots about knotChance of the crossings, with a type color each", () => {
    let knots = 0;
    let total = 0;
    for (let row = 0; row < 200; row++) {
      for (let k = 0; k < LOOM.warps; k++) {
        total++;
        const type = knotAt(row, k);
        if (type === null) continue;
        knots++;
        expect(type).toBeGreaterThanOrEqual(0);
        expect(type).toBeLessThan(KNOT_TYPES.length);
      }
    }
    expect(knots / total).toBeGreaterThan(LOOM.knotChance / 2);
    expect(knots / total).toBeLessThan(LOOM.knotChance * 2);
  });

  it("passes the weft over one warp and under the next, and the other way on the next row", () => {
    expect(weftY(0, warpX(0))).toBeCloseTo(LOOM.amplitude);
    expect(weftY(0, warpX(1))).toBeCloseTo(-LOOM.amplitude);
    expect(weftY(1, warpX(0))).toBeCloseTo(-LOOM.amplitude);
    expect(weftY(1, warpX(1))).toBeCloseTo(LOOM.amplitude);
    // Between two warps it crosses the warp plane.
    expect(weftY(0, (warpX(0) + warpX(1)) / 2)).toBeCloseTo(0);
  });

  it("eases each pass in and out", () => {
    expect(ease(-1)).toBe(0);
    expect(ease(0)).toBe(0);
    expect(ease(0.5)).toBeCloseTo(0.5);
    expect(ease(1)).toBe(1);
    expect(ease(2)).toBe(1);
    expect(ease(0.1)).toBeLessThan(0.1);
  });

  it("fades the oldest rows into the background", () => {
    expect(fadeOf(0)).toBe(1);
    expect(fadeOf(LOOM.rows - LOOM.fadeRows)).toBe(1);
    expect(fadeOf(LOOM.rows - LOOM.fadeRows / 2)).toBeCloseTo(0.5);
    expect(fadeOf(LOOM.rows)).toBe(0);
    expect(fadeOf(LOOM.rows + 3)).toBe(0);
  });
});

describe("loomFrame", () => {
  it("draws every warp thread from near the viewer to beyond the oldest row", () => {
    const frame = loomFrame(START_SECONDS);
    expect(frame.warps).toHaveLength(LOOM.warps);
    for (const [near, far] of frame.warps) {
      expect(near[2]).toBe(LOOM.warpNearZ);
      expect(far[2]).toBe(WARP_FAR_Z);
      expect(near[0]).toBe(far[0]);
    }
  });

  it("starts with a woven cloth and a pass under way", () => {
    const frame = loomFrame(START_SECONDS);
    expect(frame.rows.length).toBeGreaterThan(LOOM.rows - LOOM.fadeRows);
    expect(frame.rows.length).toBeLessThanOrEqual(LOOM.rows);
    const [newest, previous] = frame.rows;
    // The newest row is partly laid; the one before it is complete.
    const steps = (LOOM.warps - 1) * LOOM.samplesPerGap;
    expect(newest?.points.length).toBeLessThan(steps + 1);
    expect(previous?.points.length).toBe(steps + 1);
    expect(frame.knots.length).toBeGreaterThan(0);
  });

  it("lays rows in alternate directions", () => {
    const frame = loomFrame(START_SECONDS);
    for (const row of frame.rows.slice(1, 4)) {
      const first = row.points[0]?.[0] ?? 0;
      const last = row.points[row.points.length - 1]?.[0] ?? 0;
      if (row.index % 2 === 0) expect(first).toBeLessThan(last);
      else expect(first).toBeGreaterThan(last);
    }
  });

  it("moves the shuttle across the warp during a pass and ends the row at the far side", () => {
    const pass = LOOM.passSeconds;
    const at = (f: number) => loomFrame(10 * pass + f * pass).rows[0];
    const early = at(0.1);
    const late = at(0.9);
    expect(early?.index).toBe(10);
    expect(late?.points.length).toBeGreaterThan(early?.points.length ?? 0);
    // Row 10 runs left to right: its head is further right later.
    const head = (row: typeof early) => row?.points[row.points.length - 1]?.[0] ?? 0;
    expect(head(late)).toBeGreaterThan(head(early));
    expect(head(at(0.999_999))).toBeCloseTo(warpX(LOOM.warps - 1), 3);
  });

  it("drifts the cloth away from the viewer by one row per pass", () => {
    const pass = LOOM.passSeconds;
    const zOf = (seconds: number, index: number) =>
      loomFrame(seconds).rows.find((row) => row.index === index)?.points[0]?.[2] ?? NaN;
    const before = zOf(20 * pass, 18);
    const after = zOf(21 * pass, 18);
    expect(before - after).toBeCloseTo(LOOM.rowSpacing);
  });

  it("knots the newest row only where the shuttle has passed, growing as it moves on", () => {
    const pass = LOOM.passSeconds;
    for (let i = 0; i < 40; i++) {
      const seconds = 30 * pass + (i / 40) * pass;
      const frame = loomFrame(seconds);
      const newest = frame.rows[0];
      if (!newest) continue;
      const forward = newest.index % 2 === 0;
      const head = newest.points[newest.points.length - 1]?.[0] ?? 0;
      for (const knot of frame.knots) {
        expect(knot.scale).toBeGreaterThanOrEqual(0);
        expect(knot.scale).toBeLessThanOrEqual(1);
        if (knot.position[2] !== newest.points[0]?.[2]) continue;
        if (forward) expect(knot.position[0]).toBeLessThanOrEqual(head + 1e-6);
        else expect(knot.position[0]).toBeGreaterThanOrEqual(head - 1e-6);
      }
    }
  });

  it("is empty but valid at the very start, and treats negative time as the start", () => {
    expect(loomFrame(0).rows).toHaveLength(0);
    expect(loomFrame(-5)).toEqual(loomFrame(0));
    expect(loomFrame(0.5 * LOOM.passSeconds).rows).toHaveLength(1);
  });
});

describe("projectPoint", () => {
  it("puts the camera's target in the middle of the view", () => {
    const p = projectPoint(CAMERA.target, { aspect: 1.5 });
    expect(p.x).toBeCloseTo(0);
    expect(p.y).toBeCloseTo(0);
    expect(p.depth).toBeGreaterThan(0);
  });

  it("shows nearer threads lower and wider apart, like the scene's camera", () => {
    const aspect = 1.25;
    const near = projectPoint([warpX(LOOM.warps - 1), 0, LOOM.fellZ], { aspect });
    const far = projectPoint([warpX(LOOM.warps - 1), 0, WARP_FAR_Z], { aspect });
    expect(near.y).toBeLessThan(far.y);
    expect(near.x).toBeGreaterThan(far.x);
    expect(near.depth).toBeLessThan(far.depth);
    expect(projectPoint([warpX(0), 0, LOOM.fellZ], { aspect }).x).toBeLessThan(0);
  });

  it("never divides by a depth of zero", () => {
    const p = projectPoint(CAMERA.position, { aspect: 1 });
    expect(Number.isFinite(p.depth)).toBe(true);
    expect(p.depth).toBeGreaterThan(0);
  });
});
