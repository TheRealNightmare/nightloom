import { describe, expect, it, vi } from "vitest";

import { sampleGraph } from "../demo/sampleGraph";
import { buildGraph } from "./buildGraph";
import {
  clampIterations,
  frameSchedule,
  fullLayoutIterations,
  LARGE_GRAPH,
  layoutSettings,
  layoutSync,
  MAX_LAYOUT_ITERATIONS,
  startLayout,
} from "./layout";

// A clock that never advances: every slice is inside its budget, so a run
// finishes in its first, synchronous slice however busy the machine is.
const stoppedClock = () => 0;

const positions = (graph: ReturnType<typeof buildGraph>) =>
  Object.fromEntries(graph.mapNodes((node, { x, y }) => [node, [x, y]]));

describe("layout bounds", () => {
  it("clamps iterations", () => {
    expect(clampIterations(-5)).toBe(0);
    expect(clampIterations(12.7)).toBe(12);
    expect(clampIterations(1e9)).toBe(MAX_LAYOUT_ITERATIONS);
  });

  it("uses fewer, cheaper iterations for large graphs", () => {
    expect(fullLayoutIterations(LARGE_GRAPH + 1)).toBeLessThan(fullLayoutIterations(10));
    const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
    expect(layoutSettings(graph)).toMatchObject({ barnesHutOptimize: false, adjustSizes: true });
  });
});

describe("startLayout", () => {
  it("runs in slices within the budget and yields between them", async () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
    let clock = 0;
    const slices: number[] = [];
    const schedule = vi.fn((callback: () => void) => {
      setTimeout(callback, 0);
    });
    const run = startLayout(graph, {
      iterations: 40,
      budgetMs: 10,
      // Every reading of the clock advances it 1 ms: each slice stops at its budget.
      now: () => (clock += 1),
      schedule,
      onSlice: (iterations) => slices.push(iterations),
    });
    expect(run.finished).toBe(false);
    await run.done;
    expect(run.finished).toBe(true);
    expect(slices.reduce((a, b) => a + b, 0)).toBe(40);
    expect(slices.length).toBeGreaterThan(1);
    expect(schedule).toHaveBeenCalledTimes(slices.length - 1);
  });

  it("finishes small graphs in the first, synchronous slice", () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
    const slices: number[] = [];
    const run = startLayout(graph, {
      iterations: 50,
      now: stoppedClock,
      onSlice: (iterations) => slices.push(iterations),
    });
    expect(run.finished).toBe(true);
    expect(slices).toEqual([50]);
  });

  it("keeps fixed nodes in place and clears the flag afterwards", () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 50 });
    const moving = "asn:64496";
    const before = positions(graph);
    const fixed = new Set(graph.filterNodes((node) => node !== moving));
    startLayout(graph, { iterations: 30, fixed, now: stoppedClock });
    const after = positions(graph);
    for (const node of fixed) expect(after[node]).toEqual(before[node]);
    expect(after[moving]).not.toEqual(before[moving]);
    expect(graph.someNode((_node, attributes) => attributes.fixed === true)).toBe(false);
  });

  it("stops when cancelled", async () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
    let clock = 0;
    const run = startLayout(graph, { iterations: 400, budgetMs: 1, now: () => (clock += 1) });
    const before = positions(graph);
    run.cancel();
    await run.done;
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(positions(graph)).toEqual(before);
    run.cancel(); // idempotent
  });

  it("does nothing for fewer than two nodes", () => {
    const graph = buildGraph({ nodes: sampleGraph.nodes.slice(0, 1), edges: [] });
    expect(startLayout(graph, { iterations: 10 }).finished).toBe(true);
  });

  it("never moves pinned nodes, and leaves them fixed after a run", () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 20 });
    const pinned = "asn:64496";
    graph.mergeNodeAttributes(pinned, { pinned: true, fixed: true });
    const before = positions(graph)[pinned];
    // Even a run that lists the pinned node among its own fixed ones.
    startLayout(graph, { iterations: 30, fixed: new Set([pinned]), now: stoppedClock });
    startLayout(graph, { iterations: 30, now: stoppedClock });
    expect(positions(graph)[pinned]).toEqual(before);
    expect(graph.getNodeAttribute(pinned, "fixed")).toBe(true);
  });

  it("keeps a pinned node's exact position, beyond 32-bit precision", () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
    const pinned = "asn:64496";
    // Not representable as a 32-bit float: ForceAtlas2's Float32Array would round it.
    const exact = { x: 72.90586853027344 + 1e-9, y: -34.05120849609375 + 1e-9 };
    expect(Math.fround(exact.x)).not.toBe(exact.x);
    graph.mergeNodeAttributes(pinned, { ...exact, pinned: true, fixed: true });
    startLayout(graph, { iterations: 30, now: stoppedClock });
    layoutSync(graph, 30);
    expect(graph.getNodeAttribute(pinned, "x")).toBe(exact.x);
    expect(graph.getNodeAttribute(pinned, "y")).toBe(exact.y);
  });

  it("caps the iterations per slice, so nodes glide instead of jumping", async () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
    const slices: number[] = [];
    const run = startLayout(graph, {
      iterations: 10,
      maxPerSlice: 2,
      now: stoppedClock,
      onSlice: (iterations) => slices.push(iterations),
    });
    expect(run.finished).toBe(false);
    await run.done;
    expect(slices).toEqual([2, 2, 2, 2, 2]);
  });

  it("schedules slices on animation frames", async () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
    const frames = vi.spyOn(window, "requestAnimationFrame");
    const run = startLayout(graph, {
      iterations: 4,
      maxPerSlice: 1,
      now: stoppedClock,
      schedule: frameSchedule,
    });
    await run.done;
    expect(frames).toHaveBeenCalledTimes(3);
  });
});
