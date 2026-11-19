/**
 * The graph view must stay responsive with 2,000 entities. These checks use
 * the dev-only generator in src/test/largeGraph.ts and generous bounds (CI
 * machines are slow); typical numbers on a laptop are noted in
 * apps/web/README.md.
 */
import { describe, expect, it } from "vitest";

import type { InvestigationGraph } from "../domain/entities";
import { largeGraph } from "../test/largeGraph";
import { createGraph, syncGraph } from "./buildGraph";
import { applyFilters, EMPTY_FILTERS } from "./filters";
import { collapseGroups, computeGroups, groupContaining } from "./groups";
import {
  fullLayoutIterations,
  INCREMENTAL_LAYOUT_ITERATIONS,
  LARGE_LAYOUT_ITERATIONS,
  startLayout,
} from "./layout";
import {
  computeHighlight,
  countByType,
  edgeView,
  INITIAL_VIEW_STATE,
  nodeView,
  searchNodes,
} from "./view";

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
    // The budget is 12 ms; one iteration can overrun it a little on a slow
    // machine. The median, not the maximum: on a busy machine the scheduler
    // can pause any one slice for hundreds of milliseconds, which says nothing
    // about the layout. That every slice stops at its budget is checked with a
    // fake clock in layout.test.ts.
    const sorted = [...slices].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)] ?? Infinity;
    expect(median).toBeLessThan(250 * TIME_FACTOR);
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

// sigma runs the reducers for every node and edge on each refresh (a hover,
// a selection, a theme change, a zoom step), so they must stay cheap.
describe("reducers for 2,000 nodes and 4,000 edges", { timeout: 30_000 }, () => {
  const graph = createGraph();
  syncGraph(graph, data);
  // Top up to 4,000 edges between existing nodes.
  const ids = graph.nodes();
  for (let i = 0; graph.size < 4000; i += 1) {
    const source = ids[(i * 7919) % ids.length] ?? "";
    const target = ids[(i * 104_729 + 13) % ids.length] ?? "";
    if (source === target) continue;
    graph.addDirectedEdgeWithKey(`extra-${String(i)}`, source, target, {
      label: "related to",
      size: 1,
      color: "transparent",
      relation: { ...data.edges[0]!, id: `extra-${String(i)}`, from: source, to: target },
      uncertain: i % 5 === 0,
    });
  }

  it("repaint the whole graph quickly, focused or not", () => {
    expect(graph.order).toBe(2000);
    expect(graph.size).toBe(4000);
    const hub = graph.nodes().reduce((a, b) => (graph.degree(a) >= graph.degree(b) ? a : b));
    const states = [
      INITIAL_VIEW_STATE,
      { ...INITIAL_VIEW_STATE, hoveredNode: hub },
      { ...INITIAL_VIEW_STATE, selectedNode: hub, hoveredNode: ids[1] ?? null },
    ];
    // Several full refreshes, the median one timed: one slow pass on a busy
    // machine says nothing about the reducers.
    const timings: number[] = [];
    for (let round = 0; round < 5; round += 1) {
      for (const state of states) {
        const start = performance.now();
        const highlight = computeHighlight(graph, state);
        const context = { cameraRatio: 0.5, theme: round % 2 ? "light" : "dark" } as const;
        graph.forEachNode((node) => nodeView(graph, node, state, highlight, context));
        graph.forEachEdge((edge) => edgeView(graph, edge, state, highlight, context));
        timings.push(performance.now() - start);
      }
    }
    timings.sort((a, b) => a - b);
    const median = timings[Math.floor(timings.length / 2)] ?? Infinity;
    // About 2 ms on a desktop CPU; a frame is 16 ms.
    expect(median).toBeLessThan(50 * TIME_FACTOR);
  });
});

// One domain with 1,500 archived URLs (what the Wayback Shuttle can add):
// the URLs are leaves of one type around one entity, so they collapse into a
// single group node and the graph the layout sees stays small.
const LEAVES = 1500;
const archive = "domain:archive.example.org";
const leafy: InvestigationGraph = {
  nodes: [
    ...data.nodes,
    { ...data.nodes[0]!, id: archive, label: "archive.example.org", type: "domain" },
    ...Array.from({ length: LEAVES }, (_, i) => ({
      ...data.nodes[0]!,
      id: `url:https://archive.example.org/page/${String(i)}`,
      label: `https://archive.example.org/page/${String(i)}`,
      type: "url" as const,
    })),
  ],
  edges: [
    ...data.edges,
    {
      ...data.edges[0]!,
      id: "anchor-edge",
      from: data.nodes[100]?.id ?? "",
      to: archive,
    },
    ...Array.from({ length: LEAVES }, (_, i) => ({
      ...data.edges[0]!,
      id: `leaf-edge-${String(i)}`,
      from: archive,
      to: `url:https://archive.example.org/page/${String(i)}`,
      relation: "HOSTS",
    })),
  ],
};

describe("a 1,500-leaf group", { timeout: 30_000 }, () => {
  it("collapses into one node quickly", () => {
    const start = performance.now();
    const visible = applyFilters(leafy, EMPTY_FILTERS, { now: 0 });
    const groups = computeGroups(leafy, visible);
    const display = collapseGroups(leafy, groups);
    const graph = createGraph();
    syncGraph(graph, display);
    expect(performance.now() - start).toBeLessThan(500 * TIME_FACTOR);

    expect(groups.map((group) => group.members.length)).toEqual([LEAVES]);
    // The 2,000 entities, the archive domain and one group node.
    expect(graph.order).toBe(2000 + 2);
    expect(graph.getNodeAttribute(groups[0]?.id ?? "", "label")).toBe("1,500 URLs");
  });

  it("filters and regroups quickly when a filter changes", () => {
    const start = performance.now();
    for (let i = 0; i < 10; i += 1) {
      const filters = { ...EMPTY_FILTERS, minConfidence: i / 20, hideIsolated: i % 2 === 0 };
      collapseGroups(leafy, computeGroups(leafy, applyFilters(leafy, filters, { now: 0 })));
    }
    expect(performance.now() - start).toBeLessThan(1000 * TIME_FACTOR);
  });

  it("expands and collapses again quickly", () => {
    const visible = applyFilters(leafy, EMPTY_FILTERS, { now: 0 });
    const groups = computeGroups(leafy, visible);
    const graph = createGraph();
    syncGraph(graph, collapseGroups(leafy, groups));

    let start = performance.now();
    const expanded = syncGraph(graph, collapseGroups(leafy, []));
    expect(performance.now() - start).toBeLessThan(500 * TIME_FACTOR);
    expect(expanded.addedNodes).toHaveLength(LEAVES);
    expect(graph.order).toBe(2000 + 1 + LEAVES);

    start = performance.now();
    syncGraph(graph, collapseGroups(leafy, groups));
    expect(performance.now() - start).toBeLessThan(500 * TIME_FACTOR);
    expect(graph.order).toBe(2000 + 2);
  });

  it("finds a collapsed entity by search", () => {
    const start = performance.now();
    const visible = applyFilters(leafy, EMPTY_FILTERS, { now: 0 });
    const searchable = leafy.nodes.filter((node) => visible.has(node.id));
    const [hit] = searchNodes(searchable, "page/1499");
    expect(performance.now() - start).toBeLessThan(200 * TIME_FACTOR);
    const group = groupContaining(computeGroups(leafy, visible), hit?.id ?? "");
    expect(group?.parent).toBe(archive);
  });
});
