/**
 * The graph view must stay responsive with 2,000 entities. These checks use
 * the dev-only generator in src/test/largeGraph.ts and generous bounds (CI
 * machines are slow); typical numbers on a laptop are noted in
 * apps/web/README.md.
 */
import { describe, expect, it } from "vitest";

import { largeGraph } from "../test/largeGraph";
import { createGraph, syncGraph } from "./buildGraph";
import {
  fullLayoutIterations,
  INCREMENTAL_LAYOUT_ITERATIONS,
  LARGE_LAYOUT_ITERATIONS,
  startLayout,
} from "./layout";
import { countByType, searchNodes } from "./view";

const data = largeGraph(2000);

// Shared CI runners execute every test file in parallel on a few slow cores,
// so wall-clock bounds get 4x headroom there. They still catch the regressions
// that matter (orders of magnitude), without flaking on a busy runner.
const TIME_FACTOR = import.meta.env["CI"] ? 4 : 1;

// Enough layout iterations for several slices on any machine, without running
// a whole large-graph layout: the slice test is about the longest slice, not
// the total runtime.
const SLICE_TEST_ITERATIONS = 60;

// Building and laying out 2,000 entities takes a second or two locally, but
// far longer on a shared CI runner under parallel load, so the block gets a
// generous timeout instead of Vitest's default 5 s.
describe("2,000 entities", { timeout: 30_000 }, () => {
  it("generates a realistic graph", () => {
    expect(data.nodes).toHaveLength(2000);
    expect(data.edges.length).toBeGreaterThan(2000);
  });

  it("loads into the graph quickly", () => {
    const graph = createGraph();
    const start = performance.now();
    syncGraph(graph, data);
    expect(performance.now() - start).toBeLessThan(500 * TIME_FACTOR);
    expect(graph.order).toBe(2000);
  });

  it("uses the large-graph iteration count", () => {
    expect(fullLayoutIterations(data.nodes.length)).toBe(LARGE_LAYOUT_ITERATIONS);
  });

  it("lays out in short slices, never blocking for long", async () => {
    const graph = createGraph();
    syncGraph(graph, data);
    const slices: number[] = [];
    const run = startLayout(graph, {
      iterations: SLICE_TEST_ITERATIONS,
      onSlice: (_iterations, elapsed) => slices.push(elapsed),
    });
    await run.done;
    expect(slices.length).toBeGreaterThan(3);
    // The budget is 12 ms; one iteration can overrun it a little on a slow machine.
    expect(Math.max(...slices)).toBeLessThan(250);
  });

  it("adds one entity without moving the others", async () => {
    const graph = createGraph();
    syncGraph(graph, data);
    const before = new Map(graph.mapNodes((node, { x, y }) => [node, { x, y }]));
    const anchor = data.nodes[100];
    const template = data.edges[0];
    if (!anchor || !template) throw new Error("no anchor");
    const added = { ...anchor, id: "domain:new.example.org", label: "new.example.org" };
    const edge = { ...template, id: "new-edge", from: anchor.id, to: added.id };

    const start = performance.now();
    const result = syncGraph(graph, {
      nodes: [...data.nodes, added],
      edges: [...data.edges, edge],
    });
    expect(performance.now() - start).toBeLessThan(200 * TIME_FACTOR);
    expect(result.addedNodes).toEqual([added.id]);
    const placed = { ...graph.getNodeAttributes(added.id) };

    const fixed = new Set(graph.filterNodes((node) => node !== added.id));
    await startLayout(graph, { iterations: INCREMENTAL_LAYOUT_ITERATIONS, fixed }).done;
    // ForceAtlas2 works in float32, so "in place" means within rounding.
    const moved = graph.filterNodes((node, { x, y }) => {
      const old = before.get(node) ?? placed;
      return Math.hypot(x - old.x, y - old.y) > 1e-3;
    });
    expect(moved).toEqual([added.id]);
  });

  it("searches and counts quickly", () => {
    const start = performance.now();
    for (let i = 0; i < 20; i += 1) searchNodes(data.nodes, `site${i}`);
    countByType(data.nodes);
    expect(performance.now() - start).toBeLessThan(200 * TIME_FACTOR);
  });
});
