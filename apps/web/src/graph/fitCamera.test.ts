import { describe, expect, it } from "vitest";

import { fittedCameraState, type AnySigma } from "./interactions";

/** A viewport whose reset camera maps pixels linearly onto the framed graph [0, 1]². */
function fakeSigma(width: number, height: number): AnySigma {
  return {
    getDimensions: () => ({ width, height }),
    viewportToFramedGraph: (point: { x: number; y: number }) => ({
      x: point.x / width,
      y: point.y / height,
    }),
  } as unknown as AnySigma;
}

describe("fittedCameraState (2D framing above the legend and controls)", () => {
  it("leaves the fit to sigma's reset when nothing is covered", () => {
    expect(fittedCameraState(fakeSigma(800, 600), 0)).toBeNull();
  });

  it("ignores an inset that would leave too little room", () => {
    expect(fittedCameraState(fakeSigma(800, 600), 400)).toBeNull();
  });

  it("zooms out to fit the uncovered height and centres the graph there", () => {
    const state = fittedCameraState(fakeSigma(800, 600), 200);
    expect(state).not.toBeNull();
    // 600 px tall, 400 px left: zoomed out by 1.5.
    expect(state?.ratio).toBeCloseTo(1.5, 9);
    expect(state?.angle).toBe(0);
    expect(state?.x).toBeCloseTo(0.5, 9);
    // The graph's centre (0.5) is shown 100 px above the viewport's centre:
    // the camera looks at the point 1.5 × 100 px below it at the reset scale.
    expect(state?.y).toBeCloseTo((300 + 150) / 600, 9);
  });

  it("keeps the stage padding all round the graph", () => {
    const padding = 64;
    const state = fittedCameraState(fakeSigma(800, 600), 200, padding);
    // The graph's height at reset (600 − 128) shrinks to what is left (400 − 128).
    const ratio = (600 - 2 * padding) / (400 - 2 * padding);
    expect(state?.ratio).toBeCloseTo(ratio, 9);
    expect(state?.y).toBeCloseTo((300 + (ratio * 200) / 2) / 600, 9);
    // Its top edge is shown `padding` below the viewport's top, its bottom
    // edge `padding` above the inset.
    const shownCenter = 300 - 100;
    const shownHalf = (600 - 2 * padding) / 2 / ratio;
    expect(shownCenter - shownHalf).toBeCloseTo(padding, 9);
    expect(shownCenter + shownHalf).toBeCloseTo(600 - 200 - padding, 9);
  });
});
