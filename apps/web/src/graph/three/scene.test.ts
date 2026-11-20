import { describe, expect, it } from "vitest";

import type { InvestigationGraph } from "../../domain/entities";
import { GRAPH_PALETTES } from "../../theme/graphPalette";
import { createGraph, syncGraph } from "../buildGraph";
import { DIM_OPACITY, HALO_WIDTH, PIN_WIDTH, SELECTED_SCALE } from "../encoding";
import {
  computeHighlight,
  INITIAL_VIEW_STATE,
  NO_PULSE,
  PULSE_SCALE,
  type ViewState,
} from "../view";
import { depthSpan, UNDATED_GAP_RATIO } from "./depth";
import {
  buildModel,
  ensureKnotCapacity,
  ensureThreadCapacity,
  hexToRgb,
  knotBuffers,
  THREAD_SAG,
  THREAD_SEGMENTS,
  threadBuffers,
  updatePositions,
  writeKnotPositions,
  writeKnotStyles,
  writeThreadColors,
  writeThreadPositions,
  type BuildContext,
} from "./scene";

const palette = GRAPH_PALETTES.dark;
const provenance = (first_seen: string) => ({
  source: "manual",
  first_seen,
  last_seen: "2026-11-10T00:00:00Z",
  confidence: 0.9,
});

const DATA: InvestigationGraph = {
  nodes: [
    { id: "domain:a.example", label: "a.example", type: "domain", ...provenance("2026-01-01") },
    { id: "domain:b.example", label: "b.example", type: "domain", ...provenance("2026-12-31") },
    { id: "ip_address:192.0.2.1", label: "192.0.2.1", type: "ip_address", ...provenance("") },
  ],
  edges: [
    {
      id: "e1",
      from: "domain:a.example",
      to: "ip_address:192.0.2.1",
      relation: "RESOLVES_TO",
      ...provenance("2026-01-01"),
    },
    {
      id: "e2",
      from: "domain:b.example",
      to: "ip_address:192.0.2.1",
      relation: "RESOLVES_TO",
      ...provenance("2026-01-01"),
      confidence: 0.2,
    },
  ],
};

function setup() {
  const graph = createGraph();
  syncGraph(graph, DATA, palette);
  graph.mergeNodeAttributes("domain:a.example", { x: -100, y: 0 });
  graph.mergeNodeAttributes("domain:b.example", { x: 100, y: 0 });
  graph.mergeNodeAttributes("ip_address:192.0.2.1", { x: 0, y: 50 });
  return graph;
}

const context = (depth: BuildContext["depth"] = "time"): BuildContext => ({
  palette,
  pulse: NO_PULSE,
  depth,
});

describe("colors", () => {
  it("reads token hex colors as sRGB floats", () => {
    expect(hexToRgb("#ff8000")).toEqual([1, 128 / 255, 0]);
    expect(hexToRgb("#fff")).toEqual([1, 1, 1]);
    expect(hexToRgb("nonsense")).toEqual([0, 0, 0]);
  });
});

describe("the scene model", () => {
  it("has the visible nodes with their 2D sizes, colors and labels", () => {
    const graph = setup();
    const view: ViewState = { ...INITIAL_VIEW_STATE, hiddenTypes: new Set() };
    const model = buildModel(graph, view, computeHighlight(graph, view), context());
    expect(model.count).toBe(3);
    const at = model.index.get("ip_address:192.0.2.1") ?? -1;
    expect(model.sizePx[at]).toBeCloseTo(graph.getNodeAttribute("ip_address:192.0.2.1", "size"), 4);
    expect(model.degree[at]).toBe(2);
    expect(model.labels[at]).toBe("192.0.2.1");
    expect([...model.color.slice(at * 3, at * 3 + 3)]).toEqual(
      hexToRgb(palette.types.ip_address).map((v) => Math.fround(v)),
    );
    expect(model.edgeCount).toBe(2);
  });

  it("leaves out what is hidden, with its threads", () => {
    const graph = setup();
    const view: ViewState = { ...INITIAL_VIEW_STATE, hiddenTypes: new Set(["ip_address"]) };
    const model = buildModel(graph, view, computeHighlight(graph, view), context());
    expect(model.ids).toEqual(["domain:a.example", "domain:b.example"]);
    expect(model.edgeCount).toBe(0);
  });

  it("draws selection, pins and pulses like 2D, and dims what is not around the focus", () => {
    const graph = setup();
    graph.setNodeAttribute("domain:b.example", "pinned", true);
    const view: ViewState = { ...INITIAL_VIEW_STATE, selectedNode: "domain:a.example" };
    const model = buildModel(graph, view, computeHighlight(graph, view), {
      ...context(),
      pulse: new Set(["domain:b.example"]),
    });
    const a = model.index.get("domain:a.example") ?? -1;
    const b = model.index.get("domain:b.example") ?? -1;
    const ip = model.index.get("ip_address:192.0.2.1") ?? -1;
    expect(model.sizePx[a]).toBeCloseTo(
      graph.getNodeAttribute("domain:a.example", "size") * SELECTED_SCALE,
      4,
    );
    expect(model.haloPx[a]).toBe(HALO_WIDTH);
    expect(model.selected[a]).toBe(1);
    expect(model.pinPx[b]).toBe(PIN_WIDTH);
    expect(model.sizePx[b]).toBeCloseTo(
      graph.getNodeAttribute("domain:b.example", "size") * PULSE_SCALE,
      4,
    );
    // b is not a neighbour of a, but pulsing: never dimmed.
    expect(model.dim[b]).toBe(1);
    expect(model.dim[ip]).toBe(1);
    expect(model.near[ip]).toBe(1);

    const dimmed = buildModel(graph, view, computeHighlight(graph, view), context());
    expect(dimmed.dim[dimmed.index.get("domain:b.example") ?? -1]).toBeCloseTo(DIM_OPACITY, 6);
  });

  it("colors threads: brass around the focus, faint when uncertain, dimmed elsewhere", () => {
    const graph = setup();
    const rest = buildModel(
      graph,
      INITIAL_VIEW_STATE,
      computeHighlight(graph, INITIAL_VIEW_STATE),
      context(),
    );
    const hair = hexToRgb(palette.edge);
    expect(rest.edgeColor[0]).toBeCloseTo(hair[0], 5);
    // e2 is uncertain: blended toward the background.
    const background = hexToRgb(palette.background);
    expect(Math.abs((rest.edgeColor[3] ?? 0) - background[0])).toBeLessThan(
      Math.abs(hair[0] - background[0]),
    );

    const view: ViewState = { ...INITIAL_VIEW_STATE, hoveredNode: "domain:a.example" };
    const focused = buildModel(graph, view, computeHighlight(graph, view), context());
    const brass = hexToRgb(palette.selection);
    expect(focused.edgeColor[0]).toBeCloseTo(brass[0], 5);
  });

  it("names group knots by their count", () => {
    const graph = createGraph();
    syncGraph(
      graph,
      {
        nodes: [
          {
            id: "group:domain:a.example|url",
            label: "12 URLs",
            type: "url",
            ...provenance(""),
            group: {
              parent: "domain:a.example",
              members: Array.from({ length: 1234 }, (_, i) => `u${String(i)}`),
            },
          },
        ],
        edges: [],
      },
      palette,
    );
    const model = buildModel(
      graph,
      INITIAL_VIEW_STATE,
      computeHighlight(graph, INITIAL_VIEW_STATE),
      context(),
    );
    expect(model.labels[0]).toBe("1.2k");
    expect(model.isGroup[0]).toBe(1);
  });
});

describe("positions and depth", () => {
  it("takes x / y from the layout and z from the depth mode, scaled to the layout", () => {
    const graph = setup();
    const model = buildModel(
      graph,
      INITIAL_VIEW_STATE,
      computeHighlight(graph, INITIAL_VIEW_STATE),
      context("time"),
    );
    const extent = updatePositions(model, graph, 1);
    expect(extent.radius).toBeCloseTo(Math.hypot(200, 50) / 2, 4);
    const span = depthSpan(extent.radius);
    expect(model.span).toBe(span);
    const z = (id: string) => model.positions[(model.index.get(id) ?? 0) * 3 + 2] ?? NaN;
    expect(z("domain:a.example")).toBeCloseTo(-span / 2, 3);
    expect(z("domain:b.example")).toBeCloseTo(span / 2, 3);
    expect(z("ip_address:192.0.2.1")).toBeCloseTo(span / 2 + span * UNDATED_GAP_RATIO, 3);
    expect(model.positions[0]).toBe(-100);
  });

  it("animates from the previous depth, and keeps a frozen range during a drag", () => {
    const graph = setup();
    const time = buildModel(
      graph,
      INITIAL_VIEW_STATE,
      computeHighlight(graph, INITIAL_VIEW_STATE),
      context("time"),
    );
    updatePositions(time, graph, 1);
    const flat = buildModel(
      graph,
      INITIAL_VIEW_STATE,
      computeHighlight(graph, INITIAL_VIEW_STATE),
      context("flat"),
      time,
    );
    updatePositions(flat, graph, 0, time.span);
    expect([...flat.positions.filter((_, i) => i % 3 === 2)]).toEqual([
      ...time.positions.filter((_, i) => i % 3 === 2),
    ]);
    updatePositions(flat, graph, 1, 500);
    expect(flat.span).toBe(500);
    expect([...flat.positions.filter((_, i) => i % 3 === 2)]).toEqual([0, 0, 0]);
  });
});

describe("GPU buffers", () => {
  it("splits knots by shape and writes their styles and positions", () => {
    const graph = setup();
    graph.setNodeAttribute("domain:a.example", "pinned", true);
    const model = buildModel(
      graph,
      INITIAL_VIEW_STATE,
      computeHighlight(graph, INITIAL_VIEW_STATE),
      context("flat"),
    );
    updatePositions(model, graph, 1);
    const entities = ensureKnotCapacity(knotBuffers(1), model.count);
    expect(entities.capacity).toBeGreaterThanOrEqual(3);
    writeKnotStyles(model, entities, false);
    writeKnotPositions(model, entities);
    expect(entities.count).toBe(3);
    expect(entities.matrix[12]).toBe(-100);
    expect(entities.matrix[15]).toBe(1);
    expect(entities.style[2]).toBe(PIN_WIDTH);
    const groups = knotBuffers(4);
    writeKnotStyles(model, groups, true);
    expect(groups.count).toBe(0);
    // Large enough already: kept.
    expect(ensureKnotCapacity(entities, 2)).toBe(entities);
  });

  it("writes each thread as a sagging curve of THREAD_SEGMENTS segments", () => {
    const graph = setup();
    const model = buildModel(
      graph,
      INITIAL_VIEW_STATE,
      computeHighlight(graph, INITIAL_VIEW_STATE),
      context("flat"),
    );
    updatePositions(model, graph, 1);
    const threads = ensureThreadCapacity(threadBuffers(1), model.edgeCount);
    writeThreadColors(model, threads);
    writeThreadPositions(model, threads);
    expect(threads.vertices).toBe(model.edgeCount * THREAD_SEGMENTS * 2);
    const p = threads.positions;
    // Starts at the source, ends at the target, and is continuous.
    expect([p[0], p[1], p[2]]).toEqual([-100, 0, 0]);
    const last = (THREAD_SEGMENTS * 2 - 1) * 3;
    expect(p[last]).toBeCloseTo(0, 4);
    expect(p[last + 1]).toBeCloseTo(50, 4);
    expect(p[3]).toBe(p[6]);
    // The middle sags below the straight line by THREAD_SAG of its length.
    const middle = (THREAD_SEGMENTS / 2) * 2 * 3;
    const length = Math.hypot(100, 50);
    expect(p[middle + 1]).toBeCloseTo(25 - THREAD_SAG * length, 3);
    expect(ensureThreadCapacity(threads, 1)).toBe(threads);
  });
});
