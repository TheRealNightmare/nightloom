import { describe, expect, it } from "vitest";

import { orbit, type Camera3D } from "./camera3d";
import {
  cameraOnMap,
  FOOTPRINT_REACH,
  headingOnMap,
  lookAtOnMap,
  viewFootprint,
} from "./minimap3d";

const VIEWPORT = { width: 800, height: 600 };
const TOP: Camera3D = { position: [10, 20, 500], target: [10, 20, 0], up: [0, 1, 0] };

describe("3D minimap", () => {
  it("outlines the view from the top as a rectangle around the target", () => {
    const corners = viewFootprint(TOP, VIEWPORT);
    expect(corners).toHaveLength(4);
    const xs = corners.map((c) => c.x);
    const ys = corners.map((c) => c.y);
    expect((Math.min(...xs) + Math.max(...xs)) / 2).toBeCloseTo(10, 4);
    expect((Math.min(...ys) + Math.max(...ys)) / 2).toBeCloseTo(20, 4);
    // Wider than tall, like the viewport.
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(Math.max(...ys) - Math.min(...ys));
    // The top-left corner of the screen is up and to the left on the map.
    expect(corners[0]?.x).toBeLessThan(10);
    expect(corners[0]?.y).toBeGreaterThan(20);
  });

  it("cuts corners that look above the horizon at a fixed reach", () => {
    const level: Camera3D = { position: [0, -500, 0.5], target: [0, 0, 0], up: [0, 0, 1] };
    const corners = viewFootprint(level, VIEWPORT);
    for (const corner of corners) {
      expect(Math.hypot(corner.x, corner.y + 500)).toBeLessThanOrEqual(FOOTPRINT_REACH * 500 + 1);
    }
  });

  it("shows the camera and where it heads, and moves the target there on click", () => {
    expect(cameraOnMap(TOP)).toEqual({ x: 10, y: 20 });
    expect(headingOnMap(TOP)).toBeNull();
    const turned = orbit(TOP, 0, -1);
    const heading = headingOnMap(turned);
    expect(heading).not.toBeNull();
    expect(Math.hypot(heading?.x ?? 0, heading?.y ?? 0)).toBeCloseTo(1, 6);
    const moved = lookAtOnMap(TOP, { x: 100, y: -50 });
    expect(moved.target).toEqual([100, -50, 0]);
    expect(moved.position).toEqual([100, -50, 500]);
  });
});
