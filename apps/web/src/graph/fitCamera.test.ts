import { describe, expect, it } from "vitest";

import { FakeCamera, FakeSigma, VIEWPORT } from "../test/sigmaMock";
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

  it("never zooms out further than `minZoom`, and centers the graph at the zoom it keeps", () => {
    // 4000 px wide: it would need a zoom of about 0.2 to fit.
    const knots = [
      { x: -1500, y: 300, radius: 8 },
      { x: 2500, y: 300, radius: 8 },
    ];
    const fit = fit2d(knots, W, H, [LEGEND, CONTROLS], Infinity, FIT_SHARE, 0.5);
    if (!fit) throw new Error("no fit");
    expect(fit.zoom).toBe(0.5);
    // The graph's middle lands in the middle of the free area at that zoom.
    const middle = { x: 500, y: 300 };
    const areaX = fit.area.x + fit.area.width / 2;
    const areaY = fit.area.y + fit.area.height / 2;
    expect(W / 2 + (middle.x - fit.anchor.x) * fit.zoom).toBeCloseTo(areaX, 9);
    expect(H / 2 + (middle.y - fit.anchor.y) * fit.zoom).toBeCloseTo(areaY, 9);
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

  it("centers a graph too large for the farthest camera in the free area", () => {
    const sigma = sigmaWith({ a: [-20000, 0], b: [20000, 0] });
    const cover = [{ x: 0, y: 450, width: VIEWPORT.width, height: 150 }];
    const state = fittedCameraState(sigma as unknown as AnySigma, ["a", "b"], cover);
    if (!state) throw new Error("no fit");
    expect(state.ratio).toBe(sigma.camera.maxRatio);
    const middle = shownAt(sigma, { x: 0, y: 0 }, state);
    expect(middle.x).toBeCloseTo(VIEWPORT.width / 2, 6);
    expect(middle.y).toBeCloseTo(450 / 2, 6);
  });
});

/**
 * Like sigma 4: the graph's extent is framed into the unit square (an extent
 * of 0, one entity or all on one spot, counts as 1 unit), and the unit square
 * spans the canvas's smaller side at camera ratio 1. Unrotated.
 */
class FramedSigma {
  readonly camera = new FakeCamera();
  private readonly positions: Record<string, [number, number]>;
  constructor(positions: Record<string, [number, number]>) {
    this.positions = positions;
  }
  private extent() {
    const xs = Object.values(this.positions).map(([x]) => x);
    const ys = Object.values(this.positions).map(([, y]) => y);
    return { x: [Math.min(...xs), Math.max(...xs)], y: [Math.min(...ys), Math.max(...ys)] };
  }
  private frame({ x, y }: { x: number; y: number }) {
    const { x: ex, y: ey } = this.extent();
    const [minX = 0, maxX = 0] = ex;
    const [minY = 0, maxY = 0] = ey;
    const span = Math.max(maxX - minX, maxY - minY) || 1;
    return { x: 0.5 + (x - (minX + maxX) / 2) / span, y: 0.5 + (y - (minY + maxY) / 2) / span };
  }
  private get side() {
    return Math.min(VIEWPORT.width, VIEWPORT.height);
  }
  getDimensions() {
    return { ...VIEWPORT };
  }
  getCamera() {
    return this.camera;
  }
  getBBox() {
    return this.extent();
  }
  getGraph() {
    return {
      hasNode: (node: string) => node in this.positions,
      getNodeAttributes: (node: string) => {
        const [x, y] = this.positions[node] ?? [0, 0];
        return { x, y };
      },
    };
  }
  getNodeDisplayData() {
    return { size: 8 };
  }
  graphToViewport(
    point: { x: number; y: number },
    at: { cameraState: { x: number; y: number; ratio: number } },
  ) {
    const framed = this.frame(point);
    const { x, y, ratio } = at.cameraState;
    return {
      x: VIEWPORT.width / 2 + ((framed.x - x) / ratio) * this.side,
      y: VIEWPORT.height / 2 - ((framed.y - y) / ratio) * this.side,
    };
  }
  viewportToFramedGraph(
    point: { x: number; y: number },
    at: { cameraState: { x: number; y: number; ratio: number } },
  ) {
    const { x, y, ratio } = at.cameraState;
    return {
      x: x + ((point.x - VIEWPORT.width / 2) * ratio) / this.side,
      y: y - ((point.y - VIEWPORT.height / 2) * ratio) / this.side,
    };
  }
}

describe("fittedCameraState with sigma's framing of the graph's extent", () => {
  const cover = [{ x: 0, y: 450, width: VIEWPORT.width, height: 150 }];
  const at = (state: { x: number; y: number; ratio: number }) => ({ cameraState: state });

  it("frames a lone entity in the middle of the free area, at the minimum extent's scale", () => {
    // Before: the minimum extent was measured as 200 framed units, so the
    // camera went as far out as it can and the anchor sent the knot off screen.
    const sigma = new FramedSigma({ a: [100, 0] });
    const state = fittedCameraState(sigma as unknown as AnySigma, ["a"], cover);
    if (!state) throw new Error("no fit");
    const shown = sigma.graphToViewport({ x: 100, y: 0 }, at(state));
    expect(shown.x).toBeCloseTo(VIEWPORT.width / 2, 6);
    expect(shown.y).toBeCloseTo(450 / 2, 6);
    // The frame (standing for the minimum extent) spans 85% of the smaller side.
    expect(state.ratio).toBeCloseTo(1 / FIT_SHARE, 6);
  });

  it("frames entities on one spot the same way", () => {
    const sigma = new FramedSigma({ a: [5, 5], b: [5, 5] });
    const state = fittedCameraState(sigma as unknown as AnySigma, ["a", "b"]);
    if (!state) throw new Error("no fit");
    const shown = sigma.graphToViewport({ x: 5, y: 5 }, at(state));
    expect(shown.x).toBeCloseTo(VIEWPORT.width / 2, 6);
    expect(shown.y).toBeCloseTo(VIEWPORT.height / 2, 6);
    expect(state.ratio).toBeCloseTo(1 / FIT_SHARE, 6);
  });

  it("keeps a tiny graph centered when its minimum extent is beyond the farthest camera", () => {
    // 2 units wide: sigma blows it up to the frame, so 200 units would be 100
    // frames; the camera stops at its largest ratio and still centers it.
    const sigma = new FramedSigma({ a: [0, 0], b: [2, 0] });
    const state = fittedCameraState(sigma as unknown as AnySigma, ["a", "b"], cover);
    if (!state) throw new Error("no fit");
    expect(state.ratio).toBe(sigma.camera.maxRatio);
    const a = sigma.graphToViewport({ x: 0, y: 0 }, at(state));
    const b = sigma.graphToViewport({ x: 2, y: 0 }, at(state));
    expect((a.x + b.x) / 2).toBeCloseTo(VIEWPORT.width / 2, 6);
    expect(a.y).toBeCloseTo(450 / 2, 6);
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
