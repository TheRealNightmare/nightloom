import { describe, expect, it } from "vitest";

import { orbit, type Camera3D } from "./camera3d";
import {
  dragDelta,
  focusOrder3d,
  knotScreenRadius,
  MAX_ATTENUATION,
  MIN_ATTENUATION,
  nodesInScreenRect,
  pickNode,
  type ScreenNodes,
} from "./picking";
import { project, viewProjection } from "./projection";

const VIEWPORT = { width: 800, height: 600 };

function screen(points: [string, number, number, number, number][]): ScreenNodes {
  return {
    ids: points.map(([id]) => id),
    projected: points.flatMap(([, x, y, depth]) => [x, y, depth]),
    radii: points.map(([, , , , radius]) => radius),
    count: points.length,
  };
}

describe("knot size on screen", () => {
  it("keeps the 2D size at the target's depth and scales with perspective, within bounds", () => {
    expect(knotScreenRadius(10, 100, 100)).toBe(10);
    expect(knotScreenRadius(10, 50, 100)).toBe(10 * MAX_ATTENUATION);
    expect(knotScreenRadius(10, 150, 100)).toBeCloseTo(10 * (100 / 150), 6);
    expect(knotScreenRadius(10, 1e6, 100)).toBe(10 * MIN_ATTENUATION);
    expect(knotScreenRadius(10, -1, 100)).toBe(0);
  });
});

describe("picking", () => {
  const nodes = screen([
    ["far", 100, 100, 300, 10],
    ["near", 104, 100, 200, 10],
    ["alone", 400, 300, 250, 6],
    ["behind", 400, 300, -5, 50],
  ]);

  it("picks the nearest knot under the pointer", () => {
    expect(pickNode(nodes, { x: 102, y: 100 })).toBe("near");
    expect(pickNode(nodes, { x: 400, y: 308 })).toBe("alone");
  });

  it("allows a few pixels of slop, then misses", () => {
    expect(pickNode(nodes, { x: 400, y: 308.5 })).toBe("alone");
    expect(pickNode(nodes, { x: 400, y: 320 })).toBeNull();
  });

  it("never picks what is behind the camera", () => {
    expect(pickNode(nodes, { x: 400, y: 340 })).toBeNull();
  });

  it("prefers the closer center between knots at the same depth", () => {
    const tied = screen([
      ["a", 100, 100, 100, 10],
      ["b", 106, 100, 100, 10],
    ]);
    expect(pickNode(tied, { x: 105, y: 100 })).toBe("b");
  });

  it("selects the knots in front of the camera inside a screen rectangle", () => {
    expect(nodesInScreenRect(nodes, { x: 90, y: 90, width: 20, height: 20 })).toEqual([
      "far",
      "near",
    ]);
    expect(nodesInScreenRect(nodes, { x: 350, y: 250, width: 100, height: 100 })).toEqual([
      "alone",
    ]);
  });
});

describe("focus order in 3D", () => {
  it("goes nearest first, then in reading order within a depth band", () => {
    const order = focusOrder3d(
      [
        { id: "far-left", point: { x: 10, y: 10 }, depth: 900 },
        { id: "near-right", point: { x: 500, y: 10 }, depth: 100 },
        { id: "near-left", point: { x: 50, y: 12 }, depth: 105 },
        { id: "near-below", point: { x: 20, y: 300 }, depth: 110 },
        { id: "middle", point: { x: 300, y: 300 }, depth: 500 },
      ],
      VIEWPORT,
    );
    expect(order).toEqual(["near-left", "near-right", "near-below", "middle", "far-left"]);
  });

  it("is reading order when everything is at one depth, and skips what is not on screen", () => {
    const order = focusOrder3d(
      [
        { id: "b", point: { x: 300, y: 100 }, depth: 50 },
        { id: "a", point: { x: 100, y: 100 }, depth: 50 },
        { id: "off", point: { x: 900, y: 100 }, depth: 50 },
        { id: "behind", point: { x: 100, y: 100 }, depth: -1 },
      ],
      VIEWPORT,
    );
    expect(order).toEqual(["a", "b"]);
    expect(focusOrder3d([], VIEWPORT)).toEqual([]);
  });
});

describe("dragging on the plane facing the camera", () => {
  it("follows the pointer exactly in a front view", () => {
    const camera: Camera3D = { position: [0, 0, 600], target: [0, 0, 0], up: [0, 1, 0] };
    const m = viewProjection(camera, 4 / 3, 1, 5000);
    const anchor: [number, number, number] = [20, 10, 0];
    const start = project(m, anchor, VIEWPORT);
    const delta = dragDelta(camera, VIEWPORT, anchor, start, { x: start.x + 50, y: start.y - 30 });
    expect(delta).not.toBeNull();
    const moved: [number, number, number] = [20 + (delta?.dx ?? 0), 10 + (delta?.dy ?? 0), 0];
    const end = project(m, moved, VIEWPORT);
    expect(end.x).toBeCloseTo(start.x + 50, 3);
    expect(end.y).toBeCloseTo(start.y - 30, 3);
  });

  it("only moves x / y when the camera looks from the side (z stays with the depth mode)", () => {
    const side = orbit({ position: [0, 0, 600], target: [0, 0, 0], up: [0, 1, 0] }, Math.PI / 2, 0);
    const delta = dragDelta(side, VIEWPORT, [0, 0, 0], { x: 400, y: 300 }, { x: 450, y: 250 });
    // Looking along −x, screen right is −z (dropped); screen up is +y.
    expect(delta?.dx ?? NaN).toBeCloseTo(0, 6);
    expect(delta?.dy ?? NaN).toBeGreaterThan(0);
  });
});
