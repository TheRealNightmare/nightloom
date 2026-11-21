/**
 * The loom scene with a stand-in renderer (jsdom has no WebGL): everything
 * else is real three.js, so the buffers, the clock and the clean-up are what
 * the browser gets.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOOM, START_SECONDS, loomFrame } from "./geometry";
import type { LoomColors } from "./loomScene";

const renderers: FakeRenderer[] = [];

class FakeRenderer {
  pixelRatio = 1;
  size: [number, number] = [0, 0];
  clear: number[] = [];
  renders = 0;
  disposed = false;
  lost = false;
  outputColorSpace = "";
  toneMapping = -1;
  options: { canvas: HTMLCanvasElement };
  constructor(options: { canvas: HTMLCanvasElement }) {
    this.options = options;
    renderers.push(this);
  }
  setPixelRatio(ratio: number) {
    this.pixelRatio = ratio;
  }
  setSize(width: number, height: number) {
    this.size = [width, height];
  }
  setClearColor(color: { r: number; g: number; b: number }) {
    this.clear = [color.r, color.g, color.b];
  }
  render() {
    this.renders++;
  }
  dispose() {
    this.disposed = true;
  }
  forceContextLoss() {
    this.lost = true;
  }
}

vi.mock("three", async (importOriginal) => ({
  ...(await importOriginal<typeof import("three")>()),
  WebGLRenderer: FakeRenderer,
}));

const { MAX_PIXEL_RATIO, startLoomScene, writeFrame } = await import("./loomScene");

const COLORS: LoomColors = {
  background: [0.1, 0.1, 0.1],
  warp: [0.3, 0.2, 0.1],
  weft: [0.8, 0.6, 0.2],
  knots: [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
    [1, 1, 0],
    [1, 0, 1],
    [0, 1, 1],
    [0.5, 0.5, 0.5],
    [0.2, 0.4, 0.6],
  ],
};

function buffers() {
  return {
    warpPositions: new Float32Array(LOOM.warps * 6),
    warpColors: new Float32Array(LOOM.warps * 6),
    weftPositions: new Float32Array(LOOM.rows * 85 * 2 * 3),
    weftColors: new Float32Array(LOOM.rows * 85 * 2 * 3),
    knotMatrices: new Float32Array(LOOM.rows * LOOM.warps * 16),
    knotColors: new Float32Array(LOOM.rows * LOOM.warps * 3),
    knotStyles: new Float32Array(LOOM.rows * LOOM.warps * 2),
  };
}

describe("writeFrame", () => {
  it("fills the warp, weft and knot buffers from one frame", () => {
    const out = buffers();
    const frame = loomFrame(START_SECONDS);
    const counts = writeFrame(frame, COLORS, out);
    expect(counts.warpVertices).toBe(LOOM.warps * 2);
    const segments = frame.rows.reduce((total, row) => total + row.points.length - 1, 0);
    expect(counts.weftVertices).toBe(segments * 2);
    expect(counts.knots).toBe(frame.knots.length);
    // The warp's near end is --hair; its far end nearly the background.
    expect([...out.warpColors.slice(0, 3)]).toEqual(COLORS.warp.map(Math.fround));
    expect(out.warpColors[3]).toBeCloseTo(0.1 + (0.3 - 0.1) * 0.15);
    // The newest row is full brass.
    expect(out.weftColors[0]).toBeCloseTo(0.8);
    // Knots: a translation matrix, the type color, the size and the fade.
    const knot = frame.knots[0];
    expect(knot).toBeDefined();
    if (!knot) return;
    expect(out.knotMatrices[0]).toBe(1);
    expect(out.knotMatrices[12]).toBeCloseTo(knot.position[0]);
    expect(out.knotMatrices[14]).toBeCloseTo(knot.position[2]);
    expect([...out.knotColors.slice(0, 3)]).toEqual(
      (COLORS.knots[knot.type] ?? []).map(Math.fround),
    );
    expect(out.knotStyles[0]).toBeCloseTo(LOOM.knotRadius * knot.scale);
    expect(out.knotStyles[1]).toBeCloseTo(knot.fade);
  });

  it("blends faded rows toward the background, and stops at the buffers' capacity", () => {
    const out = buffers();
    const frame = {
      warps: [],
      rows: [
        {
          index: 1,
          fade: 0,
          points: [
            [0, 0, 0],
            [1, 0, 0],
          ] as [number, number, number][],
        },
      ],
      knots: Array.from({ length: LOOM.rows * LOOM.warps + 5 }, () => ({
        position: [0, 0, 0] as const,
        type: 99,
        scale: 1,
        fade: 1,
      })),
    };
    const counts = writeFrame(frame, COLORS, out);
    expect(out.weftColors[0]).toBeCloseTo(0.1);
    expect(counts.knots).toBe(LOOM.rows * LOOM.warps);
    // An unknown type falls back to the weft color.
    expect(out.knotColors[0]).toBeCloseTo(0.8);
  });
});

describe("startLoomScene", () => {
  let frames: ((now: number) => void)[];
  let intersection: ((entries: { isIntersecting: boolean }[]) => void) | null;
  let resized: (() => void) | null;
  let host: HTMLDivElement;
  let canvas: HTMLCanvasElement;

  const runFrame = (now: number) => {
    const pending = frames;
    frames = [];
    for (const frame of pending) frame(now);
  };

  beforeEach(() => {
    renderers.length = 0;
    frames = [];
    intersection = null;
    resized = null;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {
      frames = [];
    });
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
          intersection = callback;
        }
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: () => void) {
          resized = callback;
        }
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("devicePixelRatio", 3);
    host = document.createElement("div");
    Object.defineProperty(host, "clientWidth", { configurable: true, value: 800 });
    Object.defineProperty(host, "clientHeight", { configurable: true, value: 600 });
    canvas = document.createElement("canvas");
    host.append(canvas);
    document.body.append(host);
  });

  afterEach(() => {
    host.remove();
  });

  it("caps the pixel ratio, sizes to its parent and draws on its own clock", () => {
    const onReady = vi.fn();
    const scene = startLoomScene(canvas, COLORS, { onReady });
    const renderer = renderers[0];
    expect(renderer?.pixelRatio).toBe(MAX_PIXEL_RATIO);
    expect(renderer?.size).toEqual([800, 600]);
    expect(renderer?.clear).toEqual(COLORS.background);
    expect(renderer?.renders).toBe(0);
    runFrame(0);
    expect(renderer?.renders).toBe(1);
    expect(onReady).toHaveBeenCalledTimes(1);
    // 30 fps: the next 60 Hz frame is skipped, the one after is drawn.
    runFrame(16.7);
    expect(renderer?.renders).toBe(1);
    runFrame(33.4);
    expect(renderer?.renders).toBe(2);
    expect(onReady).toHaveBeenCalledTimes(1);
    scene.dispose();
  });

  it("pauses while the tab is hidden or the canvas is off-screen", () => {
    const scene = startLoomScene(canvas, COLORS);
    runFrame(0);
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(scene.loop.running()).toBe(false);
    expect(frames).toHaveLength(0);
    hidden.mockReturnValue(false);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(scene.loop.running()).toBe(true);

    intersection?.([{ isIntersecting: false }]);
    expect(scene.loop.running()).toBe(false);
    intersection?.([{ isIntersecting: true }]);
    expect(scene.loop.running()).toBe(true);
    scene.dispose();
  });

  it("repaints with new colors at once, and redraws after a resize", () => {
    const scene = startLoomScene(canvas, COLORS);
    const renderer = renderers[0];
    scene.loop.pause("offscreen");
    const before = renderer?.renders ?? 0;
    scene.setColors({ ...COLORS, background: [0.9, 0.9, 0.85] });
    expect(renderer?.clear).toEqual([0.9, 0.9, 0.85]);
    expect(renderer?.renders).toBe(before + 1);

    // A resize while paused only resizes; while running it also redraws.
    Object.defineProperty(host, "clientWidth", { configurable: true, value: 400 });
    resized?.();
    expect(renderer?.size).toEqual([400, 600]);
    expect(renderer?.renders).toBe(before + 1);
    scene.loop.resume("offscreen");
    resized?.();
    expect(renderer?.renders).toBe(before + 2);

    // An empty parent (display: none) is not a size.
    Object.defineProperty(host, "clientHeight", { configurable: true, value: 0 });
    resized?.();
    expect(renderer?.size).toEqual([400, 600]);
    scene.dispose();
  });

  it("stops and reports a lost WebGL context", () => {
    const onError = vi.fn();
    const scene = startLoomScene(canvas, COLORS, { onError });
    const event = new Event("webglcontextlost", { cancelable: true });
    canvas.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(onError).toHaveBeenCalledWith(expect.any(Error));
    expect(scene.loop.running()).toBe(false);
    scene.dispose();
  });

  it("releases everything on dispose, once", () => {
    const remove = vi.spyOn(document, "removeEventListener");
    const scene = startLoomScene(canvas, COLORS);
    scene.dispose();
    const renderer = renderers[0];
    expect(renderer?.disposed).toBe(true);
    expect(renderer?.lost).toBe(true);
    expect(remove).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
    expect(scene.loop.running()).toBe(false);
    const renders = renderer?.renders ?? 0;
    // Colors after dispose do not draw.
    scene.setColors(COLORS);
    expect(renderer?.renders).toBe(renders);
    scene.dispose();
  });

  it("works without IntersectionObserver, and with a canvas that has no parent", () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    vi.stubGlobal("devicePixelRatio", 0);
    const lone = document.createElement("canvas");
    const scene = startLoomScene(lone, COLORS);
    expect(renderers[0]?.pixelRatio).toBe(1);
    runFrame(0);
    expect(renderers[0]?.renders).toBe(1);
    scene.dispose();
  });
});
