import { describe, expect, it, vi } from "vitest";

import { FakeSigma } from "../test/sigmaMock";
import type { AnySigma } from "./interactions";
import {
  boundsOf,
  centerOn,
  drawMinimap,
  extendBounds,
  minimapProjection,
  throttle,
  viewportCorners,
  type MinimapContext,
} from "./minimap";

const size = { width: 160, height: 100 };

describe("minimap projection", () => {
  it("fits the graph, centered, with graph y up", () => {
    const bounds = boundsOf([
      { x: -10, y: -5 },
      { x: 10, y: 5 },
    ]);
    const projection = minimapProjection(bounds, size, 0);
    // 20 × 10 graph units into 160 × 100: limited by the width (8 px per unit).
    expect(projection.toMinimap({ x: -10, y: 5 })).toEqual({ x: 0, y: 10 });
    expect(projection.toMinimap({ x: 10, y: -5 })).toEqual({ x: 160, y: 90 });
    const back = projection.toGraph(projection.toMinimap({ x: 3, y: -2 }));
    expect(back.x).toBeCloseTo(3);
    expect(back.y).toBeCloseTo(-2);
  });

  it("copes with no nodes, one node, and a viewport larger than the graph", () => {
    expect(boundsOf([])).toEqual({ minX: -1, minY: -1, maxX: 1, maxY: 1 });
    const single = minimapProjection(boundsOf([{ x: 4, y: 4 }]), size);
    expect(Number.isFinite(single.toMinimap({ x: 4, y: 4 }).x)).toBe(true);
    expect(extendBounds(boundsOf([{ x: 0, y: 0 }]), [{ x: -50, y: 80 }])).toEqual({
      minX: -50,
      minY: 0,
      maxX: 0,
      maxY: 80,
    });
  });
});

function fakeContext() {
  const calls: string[] = [];
  const context = {
    fillStyle: "" as unknown,
    strokeStyle: "" as unknown,
    lineWidth: 1,
    globalAlpha: 1,
    clearRect: vi.fn(() => calls.push("clear")),
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    closePath: vi.fn(),
    stroke: vi.fn(() => calls.push("stroke")),
    fill: vi.fn(),
    setTransform: vi.fn(),
  };
  return { context: context as unknown as MinimapContext & typeof context, calls };
}

describe("drawing the minimap", () => {
  it("draws one dot per node and the viewport outline in brass", () => {
    const { context, calls } = fakeContext();
    const dots = [
      { x: 0, y: 0, color: "#aaa" },
      { x: 1, y: 1, color: "#aaa" },
      { x: 2, y: 2, color: "#bbb" },
    ];
    const viewport = [
      { x: -1, y: -1 },
      { x: 3, y: -1 },
      { x: 3, y: 3 },
      { x: -1, y: 3 },
    ];
    const projection = minimapProjection(extendBounds(boundsOf(dots), viewport), size);
    drawMinimap(context, size, dots, viewport, projection, { viewport: "#c8963e" });
    expect(context.fillRect).toHaveBeenCalledTimes(3);
    expect(context.lineTo).toHaveBeenCalledTimes(3);
    expect(context.strokeStyle).toBe("#c8963e");
    expect(calls).toEqual(["clear", "stroke"]);
  });

  it("skips the outline without a viewport", () => {
    const { context } = fakeContext();
    drawMinimap(context, size, [], [], minimapProjection(boundsOf([]), size), { viewport: "x" });
    expect(context.stroke).not.toHaveBeenCalled();
  });
});

describe("throttled redraws", () => {
  it("draws at most once per interval, and not at all when idle", () => {
    let now = 0;
    const timers: { at: number; run: () => void }[] = [];
    const draw = vi.fn();
    const throttled = throttle(draw, 100, {
      now: () => now,
      setTimer: (run, ms) => {
        const timer = { at: now + ms, run };
        timers.push(timer);
        return timer;
      },
      clearTimer: (handle) => {
        timers.splice(timers.indexOf(handle as (typeof timers)[number]), 1);
      },
    });
    const tick = (to: number) => {
      now = to;
      for (const timer of [...timers]) {
        if (timer.at <= now) {
          timers.splice(timers.indexOf(timer), 1);
          timer.run();
        }
      }
    };
    throttled.request();
    tick(0);
    expect(draw).toHaveBeenCalledTimes(1);
    // A burst of requests (camera moving) within 100 ms: one more draw at its end.
    for (let t = 10; t <= 90; t += 10) {
      now = t;
      throttled.request();
    }
    tick(99);
    expect(draw).toHaveBeenCalledTimes(1);
    tick(100);
    expect(draw).toHaveBeenCalledTimes(2);
    // Idle: nothing scheduled.
    tick(1000);
    expect(draw).toHaveBeenCalledTimes(2);
    expect(timers).toHaveLength(0);
    throttled.request();
    throttled.cancel();
    tick(2000);
    expect(draw).toHaveBeenCalledTimes(2);
  });
});

describe("minimap and camera", () => {
  const make = () => {
    const fake = new FakeSigma(
      { nodes: () => [], edges: () => [], hasNode: () => false, getNodeAttributes: () => ({}) },
      document.createElement("div"),
    );
    return { fake, sigma: fake as unknown as AnySigma };
  };

  it("outlines the viewport, rotated with the camera", () => {
    const { fake, sigma } = make();
    const upright = viewportCorners(sigma);
    expect(upright[0]!.y).toBeGreaterThan(upright[3]!.y);
    fake.camera.setState({ angle: Math.PI / 2 });
    const turned = viewportCorners(sigma);
    // A quarter turn: the outline's top edge now runs vertically.
    expect(turned[0]!.x).toBeCloseTo(turned[1]!.x);
  });

  it("centers the camera where the minimap was clicked", () => {
    const { fake, sigma } = make();
    centerOn(sigma, { x: 120, y: -40 });
    expect(fake.camera.x).toBeCloseTo(120);
    expect(fake.camera.y).toBeCloseTo(-40);
  });
});
