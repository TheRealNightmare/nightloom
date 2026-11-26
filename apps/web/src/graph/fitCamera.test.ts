import { describe, expect, it } from "vitest";

import { FakeSigma, VIEWPORT } from "../test/sigmaMock";
import { fit2d, FIT_SHARE, freeAreas, MIN_FIT_EXTENT, type ScreenKnot } from "./framing";
import type { Rect } from "./geometry";
import { fittedCameraState, focusCameraState, type AnySigma } from "./interactions";

const W = 1000;
const H = 600;
/** The legend along the bottom left, the zoom controls at the bottom right. */
const LEGEND: Rect = { x: 0, y: 480, width: 840, height: 120 };
const CONTROLS: Rect = { x: 850, y: 460, width: 150, height: 140 };
/** The minimap above the controls. */
const MINIMAP: Rect = { x: 760, y: 300, width: 240, height: 150 };

function bounds(rects: readonly Rect[]) {
  return {
    minX: Math.min(...rects.map((r) => r.x)),
    minY: Math.min(...rects.map((r) => r.y)),
    maxX: Math.max(...rects.map((r) => r.x + r.width)),
    maxY: Math.max(...rects.map((r) => r.y + r.height)),
  };
}

describe("freeAreas", () => {
  it("is the whole canvas when nothing is covered", () => {
    expect(freeAreas(W, H, [])).toEqual([{ x: 0, y: 0, width: W, height: H }]);
  });

  it("is the band above the legend and controls, or the part above the legend left of the controls", () => {
    const areas = freeAreas(W, H, [LEGEND, CONTROLS]);
    expect(areas).toHaveLength(2);
    expect(areas).toContainEqual({ x: 0, y: 0, width: W, height: 460 });
    expect(areas).toContainEqual({ x: 0, y: 0, width: 850, height: 480 });
  });

  it("offers both the band above the minimap and the part beside it", () => {
    const areas = freeAreas(W, H, [LEGEND, CONTROLS, MINIMAP]);
    expect(areas).toContainEqual({ x: 0, y: 0, width: W, height: 300 });
    expect(areas).toContainEqual({ x: 0, y: 0, width: 760, height: 480 });
    for (const area of areas) {
      for (const block of [LEGEND, CONTROLS, MINIMAP]) {
        const apart =
          area.x + area.width <= block.x ||
          block.x + block.width <= area.x ||
          area.y + area.height <= block.y ||
          block.y + block.height <= area.y;
        expect(apart).toBe(true);
      }
    }
  });

  it("ignores overlays that would leave too little room", () => {
    const tall = { x: 0, y: 200, width: W, height: 400 };
    expect(freeAreas(W, H, [tall])).toEqual([{ x: 0, y: 0, width: W, height: H }]);
  });
});

describe("fit2d", () => {
  // A wide graph: 400 × 100 px in the reset view, around (500, 300).
  const knots: ScreenKnot[] = [
    { x: 300, y: 250, radius: 6 },
    { x: 700, y: 350, radius: 6 },
    { x: 450, y: 300, radius: 10 },
  ];

  it("fills 85% of the free width or height (the tighter one) and centers the graph there", () => {
    const fit = fit2d(knots, W, H, [LEGEND, CONTROLS], Infinity);
    if (!fit) throw new Error("no fit");
    expect(fit.area).toEqual({ x: 0, y: 0, width: W, height: 460 });
    // Width is the tighter one: 400 px of knot centers plus a radius each side.
    expect(fit.zoom).toBeCloseTo((FIT_SHARE * W - 2 * 10) / 400, 9);
    // Shown at the reset view's point `anchor` at the canvas's center, zoomed:
    const show = (x: number, y: number) => ({
      x: W / 2 + (x - fit.anchor.x) * fit.zoom,
      y: H / 2 + (y - fit.anchor.y) * fit.zoom,
    });
    const left = show(300, 250);
    const right = show(700, 350);
    expect((left.x + right.x) / 2).toBeCloseTo(W / 2, 9);
    expect((left.y + right.y) / 2).toBeCloseTo(460 / 2, 9);
    expect(right.x - left.x + 20).toBeCloseTo(FIT_SHARE * W, 9);
  });

  it("picks the free area that shows the graph largest", () => {
    // Wide: the band above the minimap beats the taller part beside it.
    const wide = fit2d(knots, W, H, [LEGEND, CONTROLS, MINIMAP], Infinity);
    expect(wide?.area).toEqual({ x: 0, y: 0, width: W, height: 300 });
    // Tall: the part beside the minimap.
    const tall = fit2d(
      [
        { x: 500, y: 100, radius: 6 },
        { x: 520, y: 500, radius: 6 },
      ],
      W,
      H,
      [LEGEND, CONTROLS, MINIMAP],
      Infinity,
    );
    expect(tall?.area).toEqual({ x: 0, y: 0, width: 760, height: 480 });
  });

  it("never zooms in further than `maxZoom` (one knot, or a tiny graph)", () => {
    const one = fit2d([{ x: 500, y: 300, radius: 8 }], W, H, [], 2.5);
    expect(one?.zoom).toBe(2.5);
    const pair = fit2d(
      [
        { x: 500, y: 300, radius: 8 },
        { x: 501, y: 300, radius: 8 },
      ],
      W,
      H,
      [],
      2.5,
    );
    expect(pair?.zoom).toBe(2.5);
  });

  it("is null without knots", () => {
    expect(fit2d([], W, H, [], 1)).toBeNull();
  });
});

describe("fittedCameraState (2D framing of the visible graph)", () => {
  function sigmaWith(positions: Record<string, [number, number]>) {
    const graph = {
      nodes: () => Object.keys(positions),
      edges: () => [],
      hasNode: (node: string) => node in positions,
      getNodeAttributes: (node: string) => {
        const [x, y] = positions[node] ?? [0, 0];
        return { x, y };
      },
    };
    const sigma = new FakeSigma(graph, document.createElement("div"));
    sigma.displayFromGraph = true;
    return sigma;
  }

  /** Where `point` (graph units) shows with the camera at `state`. */
  function shownAt(sigma: FakeSigma, point: { x: number; y: number }, state: object) {
    return sigma.graphToViewport(point, {
      cameraState: { ...sigma.camera.getState(), ...state },
    });
  }

  it("frames the visible nodes in 85% of what the overlays leave free", () => {
    // 300 × 100 graph units: wide, so the width is the tighter fit.
    const sigma = sigmaWith({ a: [0, 0], b: [300, 100], c: [100, 50] });
    const cover = [{ x: 0, y: 450, width: VIEWPORT.width, height: 150 }];
    const state = fittedCameraState(sigma as unknown as AnySigma, ["a", "b", "c"], cover);
    if (!state) throw new Error("no fit");
    expect(state.angle).toBe(0);
    const a = shownAt(sigma, { x: 0, y: 0 }, state);
    const b = shownAt(sigma, { x: 300, y: 100 }, state);
    // Knot centers span 85% of the width less a radius (8 px) each side.
    expect(b.x - a.x).toBeCloseTo(FIT_SHARE * VIEWPORT.width - 16, 6);
    // Centered in the free band above the cover.
    expect((a.x + b.x) / 2).toBeCloseTo(VIEWPORT.width / 2, 6);
    expect((a.y + b.y) / 2).toBeCloseTo(450 / 2, 6);
    const drawn = bounds([
      { x: a.x - 8, y: a.y - 8, width: 16, height: 16 },
      { x: b.x - 8, y: b.y - 8, width: 16, height: 16 },
    ]);
    expect(drawn.maxY).toBeLessThan(450);
  });

  it("ignores hidden nodes (those not given)", () => {
    const sigma = sigmaWith({ a: [0, 0], b: [400, 0], far: [5000, 0] });
    const state = fittedCameraState(sigma as unknown as AnySigma, ["a", "b"]);
    if (!state) throw new Error("no fit");
    const a = shownAt(sigma, { x: 0, y: 0 }, state);
    const b = shownAt(sigma, { x: 400, y: 0 }, state);
    expect(b.x - a.x).toBeCloseTo(FIT_SHARE * VIEWPORT.width - 16, 6);
  });

  it(`frames a tiny graph as if it were ${String(MIN_FIT_EXTENT)} units wide`, () => {
    const sigma = sigmaWith({ a: [0, 0], b: [2, 1] });
    const state = fittedCameraState(sigma as unknown as AnySigma, ["a", "b"]);
    if (!state) throw new Error("no fit");
    const a = shownAt(sigma, { x: 0, y: 0 }, state);
    const end = shownAt(sigma, { x: MIN_FIT_EXTENT, y: 0 }, state);
    expect(end.x - a.x).toBeCloseTo(FIT_SHARE * Math.min(VIEWPORT.width, VIEWPORT.height), 6);
  });

  it("is null when no node is visible", () => {
    const sigma = sigmaWith({ a: [0, 0] });
    expect(fittedCameraState(sigma as unknown as AnySigma, [])).toBeNull();
  });
});

describe("focusCameraState (flying to one node)", () => {
  const point = { x: 120, y: -40 };
  const sigma = () =>
    new FakeSigma(
      {
        nodes: () => [],
        edges: () => [],
        hasNode: () => false,
        getNodeAttributes: () => ({ x: 0, y: 0 }),
      },
      document.createElement("div"),
    );
  const shown = (fake: FakeSigma, state: { x: number; y: number; ratio: number }) =>
    fake.graphToViewport(point, { cameraState: { ...state, angle: 0 } });

  it("puts the node in the middle of the canvas when nothing covers it", () => {
    const fake = sigma();
    const state = focusCameraState(fake as unknown as AnySigma, point, 0.7);
    expect(state).toEqual({ x: 120, y: -40, ratio: 0.7 });
  });

  it("puts the node in the middle of the largest free area, not under the legend", () => {
    const fake = sigma();
    const cover = [{ x: 0, y: 450, width: VIEWPORT.width, height: 150 }];
    const state = focusCameraState(fake as unknown as AnySigma, point, 0.7, cover);
    expect(state.ratio).toBe(0.7);
    const at = shown(fake, state);
    expect(at.x).toBeCloseTo(VIEWPORT.width / 2, 6);
    expect(at.y).toBeCloseTo(450 / 2, 6);
  });
});
