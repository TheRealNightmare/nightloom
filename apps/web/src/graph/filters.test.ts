import { describe, expect, it } from "vitest";

import type { EntityNode, InvestigationGraph, RelationEdge } from "../domain/entities";
import { buildGraph } from "./buildGraph";
import {
  adjacency,
  applyFilters,
  countActiveFilters,
  countSourceGroups,
  EMPTY_FILTERS,
  filtersFromParams,
  filtersToParams,
  filterSummary,
  isSeenActive,
  parseAdmiralty,
  passesEntityFilters,
  seenWindow,
  sourceGroup,
  sourcesOf,
  withinHops,
  type GraphFilters,
} from "./filters";
import { edgeView, INITIAL_VIEW_STATE, nodeView, computeHighlight } from "./view";

const NOW = Date.parse("2026-11-19T12:00:00Z");
const HOUR = 3600 * 1000;
const iso = (ms: number) => new Date(ms).toISOString();

function node(
  id: string,
  overrides: Partial<EntityNode> & { sources?: string[]; admiralty?: string | null } = {},
): EntityNode {
  const { sources = ["manual"], admiralty = "B2", ...rest } = overrides;
  return {
    id,
    label: id,
    type: "domain",
    source: sources.join(", "),
    first_seen: iso(NOW - 2 * HOUR),
    last_seen: iso(NOW - HOUR),
    confidence: 0.8,
    sensitivity: "infrastructure",
    details: {
      sources,
      observation_count: 1,
      admiralty,
      note: null,
      evidence_refs: [],
    },
    ...rest,
  };
}

function edge(from: string, to: string, relation = "HAS_SUBDOMAIN"): RelationEdge {
  return {
    id: `${from}|${relation}|${to}`,
    from,
    to,
    relation,
    source: "manual",
    first_seen: iso(NOW),
    last_seen: iso(NOW),
    confidence: 0.8,
  };
}

const filters = (overrides: Partial<GraphFilters> = {}): GraphFilters => ({
  ...EMPTY_FILTERS,
  ...overrides,
});

// a — b — c — d, plus e alone; f only reachable through a hidden ip.
const data: InvestigationGraph = {
  nodes: [
    node("a"),
    node("b", { sources: ["shuttle:dns@1.0.0"] }),
    node("c", { sources: ["manual", "shuttle:crtsh@1.2.0"], type: "certificate" }),
    node("d", { confidence: 0.4, admiralty: "C3" }),
    node("e", { last_seen: iso(NOW - 40 * 24 * HOUR), first_seen: iso(NOW - 50 * 24 * HOUR) }),
    node("p", { type: "email", sensitivity: "person", admiralty: null }),
  ],
  edges: [edge("a", "b"), edge("b", "c", "COVERS"), edge("c", "d"), edge("a", "p", "HAS_EMAIL")],
};

const shown = (f: Partial<GraphFilters>) =>
  [...applyFilters(data, filters(f), { now: NOW })].sort();

describe("sources", () => {
  it("groups Shuttle sources by name", () => {
    expect(sourceGroup("shuttle:dns@1.0.0")).toBe("dns");
    expect(sourceGroup("shuttle:reverse-dns@2.0.1")).toBe("reverse-dns");
    expect(sourceGroup("manual")).toBe("manual");
    expect(sourceGroup("something else")).toBe("something else");
  });

  it("reads the full list for case data, else splits the joined string", () => {
    expect(sourcesOf(data.nodes[2]!)).toEqual(["manual", "shuttle:crtsh@1.2.0"]);
    const demo: EntityNode = { ...node("x"), source: "a, b", details: undefined as never };
    delete (demo as { details?: unknown }).details;
    expect(sourcesOf(demo)).toEqual(["a", "b"]);
  });

  it("counts entities per group, manual first", () => {
    expect(countSourceGroups(data.nodes)).toEqual([
      { group: "manual", count: 5 },
      { group: "crtsh", count: 1 },
      { group: "dns", count: 1 },
    ]);
  });

  it("hides an entity only when all its sources are hidden", () => {
    expect(shown({ hiddenSources: new Set(["manual"]) })).toEqual(["b", "c"]);
    expect(shown({ hiddenSources: new Set(["manual", "crtsh"]) })).toEqual(["b"]);
  });
});

describe("per-entity filters", () => {
  it("shows everything without filters", () => {
    expect(shown({})).toEqual(["a", "b", "c", "d", "e", "p"]);
  });

  it("filters by type and sensitivity", () => {
    expect(shown({ hiddenTypes: new Set(["certificate", "email"]) })).toEqual(["a", "b", "d", "e"]);
    expect(shown({ hiddenSensitivities: new Set(["person"]) })).toEqual(["a", "b", "c", "d", "e"]);
  });

  it("filters by minimum confidence", () => {
    expect(shown({ minConfidence: 0.5 })).toEqual(["a", "b", "c", "e", "p"]);
    expect(shown({ minConfidence: 0.8 })).toEqual(["a", "b", "c", "e", "p"]);
    expect(shown({ minConfidence: 0.85 })).toEqual([]);
  });

  it("filters by Admiralty grades; no rating counts as F6", () => {
    expect(parseAdmiralty("B2")).toEqual({ reliability: "B", credibility: 2 });
    expect(parseAdmiralty("G7")).toBeNull();
    expect(parseAdmiralty(null)).toBeNull();
    expect(shown({ minReliability: "B" })).toEqual(["a", "b", "c", "e"]);
    expect(shown({ minReliability: "C" })).toEqual(["a", "b", "c", "d", "e"]);
    expect(shown({ minCredibility: 2 })).toEqual(["a", "b", "c", "e"]);
    expect(shown({ minReliability: "A" })).toEqual([]);
    expect(shown({ minCredibility: 6, minReliability: "F" })).toHaveLength(6);
  });

  it("filters by time: presets count back from now", () => {
    expect(seenWindow({ kind: "all" }, NOW)).toBeNull();
    expect(seenWindow({ kind: "preset", preset: "24h" }, NOW)).toEqual({
      from: NOW - 24 * HOUR,
      to: null,
    });
    expect(shown({ seen: { kind: "preset", preset: "24h" } })).toEqual(["a", "b", "c", "d", "p"]);
    expect(shown({ seen: { kind: "preset", preset: "7d" } })).toEqual(["a", "b", "c", "d", "p"]);
    expect(shown({ seen: { kind: "preset", preset: "30d" } })).toEqual(["a", "b", "c", "d", "p"]);
  });

  it("filters by a range of whole days; either end may be open", () => {
    const e = data.nodes[4]!;
    const window = seenWindow({ kind: "range", from: "2026-09-25", to: "2026-10-05" }, NOW);
    expect(window).toEqual({
      from: Date.parse("2026-09-25T00:00:00Z"),
      to: Date.parse("2026-10-05T23:59:59.999Z"),
    });
    expect(passesEntityFilters(e, EMPTY_FILTERS, window)).toBe(true);
    expect(shown({ seen: { kind: "range", from: null, to: "2026-10-31" } })).toEqual(["e"]);
    expect(shown({ seen: { kind: "range", from: "2026-11-19", to: null } })).toEqual([
      "a",
      "b",
      "c",
      "d",
      "p",
    ]);
    // Overlap: seen from 50 to 40 days ago, the window ends 45 days ago.
    expect(shown({ seen: { kind: "range", from: null, to: "2026-10-05" } })).toEqual(["e"]);
    expect(seenWindow({ kind: "range", from: "nonsense", to: null }, NOW)).toEqual({
      from: null,
      to: null,
    });
  });
});

describe("focus and isolated entities", () => {
  it("walks hops in both directions", () => {
    const neighbours = adjacency(data, new Set(data.nodes.map((n) => n.id)));
    expect([...withinHops(neighbours, "a", 0)]).toEqual(["a"]);
    expect([...withinHops(neighbours, "a", 1)].sort()).toEqual(["a", "b", "p"]);
    expect([...withinHops(neighbours, "d", 2)].sort()).toEqual(["b", "c", "d"]);
    expect([...withinHops(neighbours, "d", 3)].sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("shows only entities within N hops of the focused one", () => {
    expect(shown({ focus: { id: "a", hops: 1 } })).toEqual(["a", "b", "p"]);
    expect(shown({ focus: { id: "a", hops: 2 } })).toEqual(["a", "b", "c", "p"]);
    expect(shown({ focus: { id: "a", hops: 3 } })).toEqual(["a", "b", "c", "d", "p"]);
  });

  it("counts hops only through entities the other filters show", () => {
    expect(shown({ focus: { id: "a", hops: 3 }, hiddenTypes: new Set(["certificate"]) })).toEqual([
      "a",
      "b",
      "p",
    ]);
  });

  it("always shows the focused entity, and ignores a focus that is gone", () => {
    expect(shown({ focus: { id: "d", hops: 1 }, minConfidence: 0.5 })).toEqual(["c", "d"]);
    expect(shown({ focus: { id: "missing", hops: 1 } })).toHaveLength(6);
  });

  it("hides isolated entities, after the other filters", () => {
    expect(shown({ hideIsolated: true })).toEqual(["a", "b", "c", "d", "p"]);
    // d loses its only link when c is hidden.
    expect(shown({ hideIsolated: true, hiddenTypes: new Set(["certificate"]) })).toEqual([
      "a",
      "b",
      "p",
    ]);
  });
});

describe("edge visibility", () => {
  const graph = buildGraph(data, { layoutIterations: 0 });

  it("draws an edge only when both ends are shown", () => {
    const visible = applyFilters(data, filters({ hiddenSensitivities: new Set(["person"]) }), {
      now: NOW,
    });
    const hiddenNodes = new Set(data.nodes.map((n) => n.id).filter((id) => !visible.has(id)));
    const state = { ...INITIAL_VIEW_STATE, hiddenNodes };
    const highlight = computeHighlight(graph, state);
    expect(nodeView(graph, "p", state, highlight)).toMatchObject({ visibility: "hidden" });
    expect(nodeView(graph, "a", state, highlight).visibility).toBeUndefined();
    expect(edgeView(graph, "a|HAS_EMAIL|p", state, highlight)).toEqual({ visibility: "hidden" });
    expect(edgeView(graph, "a|HAS_SUBDOMAIN|b", state, highlight)).toEqual({});
  });

  it("never highlights a hidden node", () => {
    const state = { ...INITIAL_VIEW_STATE, hoveredNode: "p", hiddenNodes: new Set(["p"]) };
    expect(computeHighlight(graph, state).focus).toBeNull();
  });
});

describe("summary", () => {
  it("counts each kind of active filter once", () => {
    expect(countActiveFilters(EMPTY_FILTERS)).toBe(0);
    expect(
      countActiveFilters(
        filters({
          hiddenTypes: new Set(["url", "domain"]),
          hiddenSources: new Set(["dns"]),
          hiddenSensitivities: new Set(["person"]),
          seen: { kind: "preset", preset: "7d" },
          minConfidence: 0.5,
          minReliability: "B",
          minCredibility: 3,
          focus: { id: "a", hops: 2 },
          hideIsolated: true,
        }),
      ),
    ).toBe(9);
    // A custom range without dates limits nothing yet.
    expect(isSeenActive({ kind: "range", from: null, to: null })).toBe(false);
    expect(countActiveFilters(filters({ seen: { kind: "range", from: null, to: null } }))).toBe(0);
  });

  it("writes the summary line", () => {
    expect(filterSummary(120, 1340, 3)).toBe("Showing 120 of 1,340 entities · 3 filters active");
    expect(filterSummary(1, 1, 1)).toBe("Showing 1 of 1 entity · 1 filter active");
    expect(filterSummary(5, 5, 0)).toBe("Showing 5 of 5 entities");
  });
});

describe("URL query string", () => {
  const full = filters({
    hiddenTypes: new Set(["url", "domain"]),
    hiddenSources: new Set(["dns", "crtsh"]),
    hiddenSensitivities: new Set(["person"]),
    seen: { kind: "preset", preset: "30d" },
    minConfidence: 0.55,
    minReliability: "C",
    minCredibility: 2,
    focus: { id: "url:https://example.org/a?b=c&d", hops: 3 },
    hideIsolated: true,
  });

  it("round-trips every filter", () => {
    const params = filtersToParams(full);
    expect(params.get("hide")).toBe("domain,url");
    expect(params.get("xsrc")).toBe("crtsh,dns");
    expect(filtersFromParams(new URLSearchParams(params.toString()))).toEqual(full);
  });

  it("round-trips ranges, including an empty one", () => {
    for (const seen of [
      { kind: "range", from: "2026-01-01", to: null },
      { kind: "range", from: null, to: "2026-02-01" },
      { kind: "range", from: null, to: null },
    ] as const) {
      const params = filtersToParams(filters({ seen }));
      expect(filtersFromParams(params).seen).toEqual(seen);
    }
  });

  it("leaves no parameters for no filters, and keeps unrelated ones", () => {
    expect(filtersToParams(EMPTY_FILTERS).toString()).toBe("");
    const base = new URLSearchParams("tab=x&hide=url&conf=50");
    expect(filtersToParams(EMPTY_FILTERS, base).toString()).toBe("tab=x");
    expect(filtersToParams(filters({ focus: { id: "a", hops: 1 } })).toString()).toBe("focus=a");
  });

  it("ignores malformed values", () => {
    const parsed = filtersFromParams(
      new URLSearchParams(
        "hide=url,bogus&xsens=person,nope&seen=yesterday&conf=abc&rel=Z&cred=9&hops=7&focus=a&isolated=yes",
      ),
    );
    expect(parsed).toEqual(
      filters({
        hiddenTypes: new Set(["url"]),
        hiddenSensitivities: new Set(["person"]),
        focus: { id: "a", hops: 3 },
      }),
    );
    expect(filtersFromParams(new URLSearchParams("conf=250")).minConfidence).toBe(1);
    expect(filtersFromParams(new URLSearchParams("focus=a&hops=1.5")).focus?.hops).toBe(1);
    expect(filtersFromParams(new URLSearchParams("seen=bad~worse")).seen).toEqual({
      kind: "range",
      from: null,
      to: null,
    });
  });
});
