/**
 * ForceAtlas2 layout that never blocks the page for long.
 *
 * A run has a hard iteration cap and works in time slices: each slice runs
 * as many iterations as fit in its budget, then yields to the browser
 * (rendering, input) before the next one. Small graphs finish within the
 * first, synchronous slice; a 2,000-node graph settles over a second or two
 * while the page stays responsive. Nodes in `fixed` do not move, which is how
 * a newly added entity settles next to its neighbours without a full relayout.
 */
import forceAtlas2, { type ForceAtlas2Settings } from "graphology-layout-forceatlas2";

import type { NightloomGraph } from "./buildGraph";

/** Upper bound on the iterations of one run. */
export const MAX_LAYOUT_ITERATIONS = 500;
/** Iterations for a full layout of a freshly loaded graph. */
export const FULL_LAYOUT_ITERATIONS = 300;
/** Iterations for a large graph's full layout (each one costs more). */
export const LARGE_LAYOUT_ITERATIONS = 150;
/** Iterations after adding entities or relationships (only the new items move). */
export const INCREMENTAL_LAYOUT_ITERATIONS = 60;
/** Above this many nodes, use the Barnes-Hut approximation and fewer iterations. */
export const LARGE_GRAPH = 500;
/** Milliseconds of layout work per slice before yielding to the browser. */
export const SLICE_BUDGET_MS = 12;

export function clampIterations(iterations: number): number {
  return Math.min(Math.max(Math.floor(iterations), 0), MAX_LAYOUT_ITERATIONS);
}

export function fullLayoutIterations(order: number): number {
  return order > LARGE_GRAPH ? LARGE_LAYOUT_ITERATIONS : FULL_LAYOUT_ITERATIONS;
}

export function layoutSettings(graph: NightloomGraph): ForceAtlas2Settings {
  const large = graph.order > LARGE_GRAPH;
  return {
    ...forceAtlas2.inferSettings(graph),
    // Size-aware repulsion is only exact without Barnes-Hut.
    adjustSizes: !large,
    barnesHutOptimize: large,
  };
}

/** Runs `iterations` (clamped) synchronously; deterministic for the same input. */
export function layoutSync(graph: NightloomGraph, iterations: number): void {
  const count = clampIterations(iterations);
  if (count === 0 || graph.order < 2) return;
  forceAtlas2.assign(graph, { iterations: count, settings: layoutSettings(graph) });
}

export interface LayoutRun {
  /** Stops the run; nodes keep their current positions. */
  cancel: () => void;
  readonly finished: boolean;
  /** Resolves when the run finishes or is cancelled. */
  readonly done: Promise<void>;
}

export interface LayoutOptions {
  iterations: number;
  /** Nodes that keep their position during this run. */
  fixed?: ReadonlySet<string>;
  budgetMs?: number;
  /** Schedules the next slice (default: a zero-delay timeout). */
  schedule?: (callback: () => void) => void;
  /** Milliseconds clock for the slice budget (default: `performance.now`); tests pass a fake one. */
  now?: () => number;
  /** Called after every slice, e.g. to measure. */
  onSlice?: (iterations: number, elapsedMs: number) => void;
}

const defaultSchedule = (callback: () => void) => {
  setTimeout(callback, 0);
};

/** Starts a time-sliced layout run; the first slice runs before this returns. */
export function startLayout(
  graph: NightloomGraph,
  {
    iterations,
    fixed = new Set(),
    budgetMs = SLICE_BUDGET_MS,
    schedule = defaultSchedule,
    now = () => performance.now(),
    onSlice,
  }: LayoutOptions,
): LayoutRun {
  let remaining = graph.order < 2 ? 0 : clampIterations(iterations);
  // Objects, so flags set in callbacks are not narrowed away.
  const state = { cancelled: false, finished: false };
  let resolve: () => void = () => undefined;
  const done = new Promise<void>((r) => {
    resolve = r;
  });
  const pinned = [...fixed].filter((node) => graph.hasNode(node));
  for (const node of pinned) graph.setNodeAttribute(node, "fixed", true);
  // Iterations per call: grows while calls stay well under the budget.
  let batch = 1;

  const finish = () => {
    if (state.finished) return;
    state.finished = true;
    for (const node of pinned) {
      if (graph.hasNode(node)) graph.removeNodeAttribute(node, "fixed");
    }
    resolve();
  };

  const slice = () => {
    if (state.cancelled || graph.order < 2) {
      finish();
      return;
    }
    const settings = layoutSettings(graph);
    const start = now();
    let ran = 0;
    while (remaining > 0) {
      const count = Math.min(batch, remaining);
      const before = now();
      forceAtlas2.assign(graph, { iterations: count, settings });
      const took = now() - before;
      remaining -= count;
      ran += count;
      if (took < budgetMs / 4) batch *= 2;
      else if (took > budgetMs && batch > 1) batch = Math.max(1, Math.floor(batch / 2));
      if (now() - start >= budgetMs) break;
    }
    onSlice?.(ran, now() - start);
    if (remaining > 0) schedule(slice);
    else finish();
  };

  const run: LayoutRun = {
    cancel: () => {
      state.cancelled = true;
      finish();
    },
    get finished() {
      return state.finished;
    },
    done,
  };
  slice();
  return run;
}
