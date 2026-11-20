import { describe, expect, it } from "vitest";

import {
  cameraProjection,
  dragRotation,
  isRectTooSmall,
  nodesInRect,
  normalizeAngle,
  rectContains,
  rectFromPoints,
  ROTATE_STEP,
  rotateBySteps,
  rotatePoint,
  type Point,
} from "./geometry";

const close = (a: Point, b: Point) => {
  expect(a.x).toBeCloseTo(b.x);
  expect(a.y).toBeCloseTo(b.y);
};

describe("rotation math", () => {
  it("steps by 15° and stays within (−π, π]", () => {
    expect(ROTATE_STEP).toBeCloseTo((15 * Math.PI) / 180);
    expect(rotateBySteps(0, 1)).toBeCloseTo(ROTATE_STEP);
    expect(rotateBySteps(0, -2)).toBeCloseTo(-2 * ROTATE_STEP);
    expect(rotateBySteps(Math.PI, 1)).toBeCloseTo(-Math.PI + ROTATE_STEP);
    // 24 steps are a full turn.
    expect(rotateBySteps(0.3, 24)).toBeCloseTo(0.3);
    expect(normalizeAngle(-Math.PI)).toBeCloseTo(Math.PI);
    expect(normalizeAngle(3 * Math.PI)).toBeCloseTo(Math.PI);
  });

  it("rotates points clockwise on screen around a center", () => {
    close(rotatePoint({ x: 1, y: 0 }, Math.PI / 2), { x: 0, y: 1 });
    close(rotatePoint({ x: 2, y: 1 }, Math.PI, { x: 1, y: 1 }), { x: 0, y: 1 });
  });

  it("follows the pointer around the center while dragging", () => {
    const center = { x: 100, y: 100 };
    // A quarter turn clockwise on screen (right → below).
    expect(dragRotation(center, { x: 200, y: 100 }, { x: 100, y: 200 }, 0)).toBeCloseTo(
      Math.PI / 2,
    );
    expect(dragRotation(center, { x: 200, y: 100 }, { x: 200, y: 100 }, 0.4)).toBeCloseTo(0.4);
  });
});

describe("selection rectangles", () => {
  it("is upright whichever way it is drawn", () => {
    expect(rectFromPoints({ x: 10, y: 50 }, { x: 4, y: 20 })).toEqual({
      x: 4,
      y: 20,
      width: 6,
      height: 30,
    });
    expect(rectContains({ x: 0, y: 0, width: 10, height: 10 }, { x: 10, y: 0 })).toBe(true);
    expect(rectContains({ x: 0, y: 0, width: 10, height: 10 }, { x: 11, y: 0 })).toBe(false);
    expect(isRectTooSmall(rectFromPoints({ x: 0, y: 0 }, { x: 3, y: 2 }))).toBe(true);
    expect(isRectTooSmall(rectFromPoints({ x: 0, y: 0 }, { x: 3, y: 9 }))).toBe(false);
  });

  // Three nodes in a row along the graph's x axis, and one above them.
  const graph: Record<string, Point> = {
    a: { x: -10, y: 0 },
    b: { x: 0, y: 0 },
    c: { x: 10, y: 0 },
    up: { x: 0, y: 10 },
  };
  const size = { width: 400, height: 400 };

  it("picks the nodes inside it, unrotated", () => {
    const project = cameraProjection({ x: 0, y: 0 }, 0, 10, size);
    // A wide band across the middle of the screen: the row, not the node above it.
    const band = { x: 50, y: 180, width: 300, height: 40 };
    expect(nodesInRect(Object.keys(graph), (n) => project(graph[n]!), band)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("picks what is inside it on screen when the view is rotated", () => {
    // Rotated a quarter turn: the row now runs down the screen, so the same
    // wide band holds only the middle node, and a tall band holds the row.
    const project = cameraProjection({ x: 0, y: 0 }, Math.PI / 2, 10, size);
    const wide = { x: 50, y: 180, width: 300, height: 40 };
    const tall = { x: 180, y: 50, width: 40, height: 300 };
    expect(nodesInRect(Object.keys(graph), (n) => project(graph[n]!), wide)).toEqual(["b", "up"]);
    expect(nodesInRect(Object.keys(graph), (n) => project(graph[n]!), tall)).toEqual([
      "a",
      "b",
      "c",
    ]);
    // At 45° the row is a diagonal: a box around one end holds just that end.
    const diagonal = cameraProjection({ x: 0, y: 0 }, Math.PI / 4, 10, size);
    const end = diagonal(graph["c"]!);
    const box = { x: end.x - 5, y: end.y - 5, width: 10, height: 10 };
    expect(nodesInRect(Object.keys(graph), (n) => diagonal(graph[n]!), box)).toEqual(["c"]);
  });

  it("skips nodes without a position", () => {
    expect(
      nodesInRect(["a", "b"], (n) => (n === "a" ? null : { x: 1, y: 1 }), {
        x: 0,
        y: 0,
        width: 5,
        height: 5,
      }),
    ).toEqual(["b"]);
  });
});
