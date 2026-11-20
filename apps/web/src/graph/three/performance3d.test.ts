/**
 * The 3D view must stay smooth with 2,000 entities and 4,000 relationships.
 * What runs each frame something moves (layout, drag, depth animation,
 * camera) is measured here without WebGL: copying positions, rewriting the
 * knot and thread buffers, projecting every knot and choosing the labels.
 * The GPU upload and draw are a handful of calls on top (real FPS: the e2e
 * run). Bounds are generous for CI; typical numbers are in apps/web/README.md.
 */
import { describe, expect, it } from "vitest";

import type { InvestigationGraph, RelationEdge } from "../../domain/entities";
import { largeGraph } from "../../test/largeGraph";
import { GRAPH_PALETTES } from "../../theme/graphPalette";
import { createGraph, syncGraph } from "../buildGraph";
import { layoutSync } from "../layout";
import { computeHighlight, INITIAL_VIEW_STATE, NO_PULSE } from "../view";
import { boundingSphere, distanceOf, frameSphere } from "./camera3d";
import { MAX_LABELS, selectLabels, type LabelCandidate } from "./labels3d";
import { knotScreenRadius } from "./picking";
import { clipPlanes, projectAll, viewProjection } from "./projection";
import {
  buildModel,
  ensureKnotCapacity,
  ensureThreadCapacity,
  knotBuffers,
  threadBuffers,
  updatePositions,
  writeKnotPositions,
  writeKnotStyles,
  writeThreadColors,
  writeThreadPositions,
} from "./scene";

const TIME_FACTOR = import.meta.env["CI"] ? 4 : 1;
const VIEWPORT = { width: 1280, height: 800 };
/** A frame at 60 Hz is 16.7 ms; the CPU part must leave most of it to the browser and GPU. */
const FRAME_BUDGET_MS = 8;

/** largeGraph(2000), with relationships added up to `edges` (deterministic). */
function graphData(edges = 4000): InvestigationGraph {
  const data = largeGraph(2000);
  const extra: RelationEdge[] = [];
  const ids = data.nodes.map((node) => node.id);
  const template = data.edges[0];
  if (!template) throw new Error("no relationships");
  for (let i = 0; data.edges.length + extra.length < edges; i += 1) {
    const from = ids[(i * 7919) % ids.length] ?? "";
    const to = ids[(i * 104_729 + 13) % ids.length] ?? "";
    if (from === to) continue;
    extra.push({
      ...template,
      id: `extra-${String(i)}`,
      from,
      to,
      relation: "LINKS_TO",
    });
  }
  return { nodes: data.nodes, edges: [...data.edges, ...extra] };
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
}

describe("3D: 2,000 entities and 4,000 relationships", { timeout: 60_000 }, () => {
  const data = graphData();
  const graph = createGraph();
  syncGraph(graph, data, GRAPH_PALETTES.dark);
  layoutSync(graph, 20);
  const context = { palette: GRAPH_PALETTES.dark, pulse: NO_PULSE, depth: "time" as const };

  it("has the size the requirement names", () => {
    expect(graph.order).toBe(2000);
    expect(graph.size).toBe(4000);
  });

  it("builds the scene quickly (a change of filters, hover or theme)", () => {
    const times: number[] = [];
    for (let run = 0; run < 5; run += 1) {
      const start = performance.now();
      const model = buildModel(
        graph,
        INITIAL_VIEW_STATE,
        computeHighlight(graph, INITIAL_VIEW_STATE),
        context,
      );
      const entities = ensureKnotCapacity(knotBuffers(16), model.count);
      writeKnotStyles(model, entities, false);
      writeThreadColors(model, ensureThreadCapacity(threadBuffers(16), model.edgeCount));
      times.push(performance.now() - start);
    }
    expect(median(times)).toBeLessThan(40 * TIME_FACTOR);
  });

  it("does a frame's work (buffers + labels) well within a frame", () => {
    const model = buildModel(
      graph,
      INITIAL_VIEW_STATE,
      computeHighlight(graph, INITIAL_VIEW_STATE),
      context,
    );
    const entities = ensureKnotCapacity(knotBuffers(16), model.count);
    const threads = ensureThreadCapacity(threadBuffers(16), model.edgeCount);
    writeKnotStyles(model, entities, false);
    writeThreadColors(model, threads);
    updatePositions(model, graph, 1);
    const camera = frameSphere(
      boundingSphere(model.positions, model.count),
      VIEWPORT.width / VIEWPORT.height,
    );
    const projected = new Float32Array(model.count * 3);
    const radii = new Float32Array(model.count);
    const scratch = new Float32Array(model.count);
    const candidates: LabelCandidate[] = [];
    let placed = 0;

    const frame = (t: number) => {
      // Positions (as during a layout or a depth animation).
      updatePositions(model, graph, t, undefined, scratch);
      writeKnotPositions(model, entities);
      writeThreadPositions(model, threads);
      // Projection and the label pass.
      const distance = distanceOf(camera);
      const { near, far } = clipPlanes(distance, model.span);
      const matrix = viewProjection(camera, VIEWPORT.width / VIEWPORT.height, near, far);
      projectAll(matrix, model.positions, model.count, VIEWPORT, projected);
      candidates.length = 0;
      for (let i = 0; i < model.count; i += 1) {
        radii[i] = knotScreenRadius(model.sizePx[i] ?? 0, projected[i * 3 + 2] ?? 0, distance);
        candidates.push({
          index: i,
          x: projected[i * 3] ?? 0,
          y: projected[i * 3 + 1] ?? 0,
          depth: projected[i * 3 + 2] ?? 0,
          radius: radii[i] ?? 0,
          chars: (model.labels[i] ?? "").length,
          degree: model.degree[i] ?? 0,
          mandatory: false,
          centered: false,
        });
      }
      placed = selectLabels(candidates, VIEWPORT, MAX_LABELS).length;
    };

    for (let i = 0; i < 10; i += 1) frame(i / 10); // warm up
    const times: number[] = [];
    for (let i = 0; i < 60; i += 1) {
      const start = performance.now();
      frame((i % 10) / 10);
      times.push(performance.now() - start);
    }
    // Labels were placed, and not more than allowed.
    expect(placed).toBeGreaterThan(10);
    expect(placed).toBeLessThanOrEqual(MAX_LABELS);
    expect(median(times)).toBeLessThan(FRAME_BUDGET_MS * TIME_FACTOR);
    console.info(
      `3D frame work, 2,000 / 4,000: median ${median(times).toFixed(2)} ms, max ${Math.max(...times).toFixed(2)} ms`,
    );
  });
});
