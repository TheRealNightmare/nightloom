import { describe, expect, it } from "vitest";

import type { EntityType, InvestigationGraph } from "../domain/entities";
import { sampleGraph } from "../demo/sampleGraph";
import { buildGraph, EDGE_COLOR } from "./buildGraph";
import {
  CANVAS_BACKGROUND,
  computeHighlight,
  connectionsOf,
  countByType,
  edgeView,
  HIGHLIGHT_EDGE_COLOR,
  INITIAL_VIEW_STATE,
  mixColor,
  neighborhoodOf,
  nodeView,
  searchNodes,
  toggleType,
  type ViewState,
} from "./view";

const prov = {
  source: "test",
  first_seen: "2026-01-01T00:00:00Z",
  last_seen: "2026-01-02T00:00:00Z",
  confidence: 1,
};
const tiny: InvestigationGraph = {
  nodes: [
    { id: "d", label: "example.org", type: "domain", ...prov },
    { id: "s", label: "www.example.org", type: "subdomain", ...prov },
    { id: "i", label: "192.0.2.1", type: "ip", ...prov },
    { id: "a", label: "AS64496", type: "asn", ...prov },
  ],
  edges: [
    { id: "e1", from: "d", to: "s", relation: "has_subdomain", ...prov },
    { id: "e2", from: "s", to: "i", relation: "resolves_to", ...prov },
    { id: "e3", from: "i", to: "a", relation: "announced_by", ...prov },
  ],
};
const graph = buildGraph(tiny, { layoutIterations: 0 });

const state = (overrides: Partial<ViewState> = {}): ViewState => ({
  ...INITIAL_VIEW_STATE,
  ...overrides,
});
const hidden = (...types: EntityType[]) => new Set<EntityType>(types);

describe("mixColor", () => {
  it("blends between two colors", () => {
    expect(mixColor("#ffffff", "#000000", 0)).toBe("#ffffff");
    expect(mixColor("#ffffff", "#000000", 1)).toBe("#000000");
    expect(mixColor("#ff0000", "#0000ff", 0.5)).toBe("#800080");
  });

  it("clamps the amount", () => {
    expect(mixColor("#102030", "#000000", 2)).toBe("#000000");
    expect(mixColor("#102030", "#000000", -1)).toBe("#102030");
  });

  it("rejects colors it cannot parse", () => {
    expect(() => mixColor("red", "#000000", 0.5)).toThrow(/#rrggbb/);
  });
});

describe("computeHighlight", () => {
  it("is empty without hover or selection", () => {
    expect(computeHighlight(graph, state())).toEqual({ focus: null, neighborhood: new Set() });
  });

  it("prefers the hovered node over the selected one", () => {
    const highlight = computeHighlight(graph, state({ hoveredNode: "i", selectedNode: "d" }));
    expect(highlight.focus).toBe("i");
    expect([...highlight.neighborhood].sort()).toEqual(["a", "i", "s"]);
  });

  it("falls back to the selected node", () => {
    expect(computeHighlight(graph, state({ selectedNode: "d" })).focus).toBe("d");
  });

  it("ignores unknown or hidden nodes", () => {
    expect(computeHighlight(graph, state({ hoveredNode: "nope" })).focus).toBeNull();
    expect(
      computeHighlight(graph, state({ hoveredNode: "i", hiddenTypes: hidden("ip") })).focus,
    ).toBeNull();
  });
});

describe("neighborhoodOf", () => {
  it("includes the node and both in- and out-neighbors", () => {
    expect([...neighborhoodOf(graph, "s")].sort()).toEqual(["d", "i", "s"]);
  });
});

describe("nodeView", () => {
  it("leaves nodes alone when nothing is focused", () => {
    const view = state();
    expect(nodeView(graph, "d", view, computeHighlight(graph, view))).toEqual({});
  });

  it("hides nodes of hidden types", () => {
    const view = state({ hiddenTypes: hidden("asn") });
    expect(nodeView(graph, "a", view, computeHighlight(graph, view))).toMatchObject({
      visibility: "hidden",
    });
  });

  it("highlights the hovered node's neighborhood and dims the rest", () => {
    const view = state({ hoveredNode: "s" });
    const highlight = computeHighlight(graph, view);
    expect(nodeView(graph, "i", view, highlight)).toMatchObject({
      labelVisibility: "visible",
      depth: "topNodes",
    });
    const dimmed = nodeView(graph, "a", view, highlight);
    expect(dimmed.labelVisibility).toBe("hidden");
    expect(dimmed.color).toBe(
      mixColor(graph.getNodeAttribute("a", "color"), CANVAS_BACKGROUND, 0.78),
    );
  });

  it("enlarges and marks the selected node", () => {
    const view = state({ selectedNode: "d" });
    const result = nodeView(graph, "d", view, computeHighlight(graph, view));
    expect(result.highlighted).toBe(true);
    expect(result.size).toBeCloseTo(graph.getNodeAttribute("d", "size") * 1.35);
  });
});

describe("edgeView", () => {
  it("leaves edges alone when nothing is focused", () => {
    const view = state();
    expect(edgeView(graph, "e1", view, computeHighlight(graph, view))).toEqual({});
  });

  it("hides edges touching a hidden type", () => {
    const view = state({ hiddenTypes: hidden("ip") });
    const highlight = computeHighlight(graph, view);
    expect(edgeView(graph, "e2", view, highlight)).toEqual({ visibility: "hidden" });
    expect(edgeView(graph, "e3", view, highlight)).toEqual({ visibility: "hidden" });
    expect(edgeView(graph, "e1", view, highlight)).toEqual({});
  });

  it("highlights edges of the focused node and labels them", () => {
    const view = state({ hoveredNode: "i" });
    const highlight = computeHighlight(graph, view);
    expect(edgeView(graph, "e2", view, highlight)).toMatchObject({
      color: HIGHLIGHT_EDGE_COLOR,
      label: "resolves to",
      labelVisibility: "visible",
    });
    expect(edgeView(graph, "e1", view, highlight)).toEqual({
      color: mixColor(EDGE_COLOR, CANVAS_BACKGROUND, 0.6),
      zIndex: 0,
    });
  });
});

describe("toggleType", () => {
  it("adds and removes a type without mutating the input", () => {
    const original = hidden("ip");
    expect([...toggleType(original, "asn")].sort()).toEqual(["asn", "ip"]);
    expect([...toggleType(original, "ip")]).toEqual([]);
    expect([...original]).toEqual(["ip"]);
  });
});

describe("countByType", () => {
  it("counts types in canonical order", () => {
    expect(countByType(tiny.nodes)).toEqual([
      { type: "domain", count: 1 },
      { type: "subdomain", count: 1 },
      { type: "ip", count: 1 },
      { type: "asn", count: 1 },
    ]);
  });

  it("covers every type in the demo", () => {
    const total = countByType(sampleGraph.nodes).reduce((sum, { count }) => sum + count, 0);
    expect(total).toBe(sampleGraph.nodes.length);
  });
});

describe("searchNodes", () => {
  const nodes = sampleGraph.nodes;

  it("returns nothing for an empty query", () => {
    expect(searchNodes(nodes, "   ")).toEqual([]);
  });

  it("ranks exact, then prefix, then substring matches", () => {
    const labels = searchNodes(nodes, "example.org", new Set(), 20).map((node) => node.label);
    expect(labels[0]).toBe("example.org");
    expect(labels).toContain("www.example.org");
    expect(labels.indexOf("example.org")).toBeLessThan(labels.indexOf("www.example.org"));
  });

  it("is case-insensitive and trims the query", () => {
    expect(searchNodes(nodes, "  OPENssh ").map((node) => node.id)).toEqual(["tech:openssh"]);
  });

  it("prefers prefix matches over longer substring matches", () => {
    const ids = searchNodes(nodes, "203.0.113.5").map((node) => node.id);
    expect(ids[0]).toBe("ip:203.0.113.5");
    expect(ids).toContain("svc:203.0.113.5:443");
  });

  it("also matches ids", () => {
    expect(searchNodes(nodes, "cert:mail").map((node) => node.id)).toEqual(["cert:mail"]);
  });

  it("skips hidden types and respects the limit", () => {
    expect(
      searchNodes(nodes, "example", hidden("subdomain"), 50).some((n) => n.type === "subdomain"),
    ).toBe(false);
    expect(searchNodes(nodes, "example", new Set(), 3)).toHaveLength(3);
  });
});

describe("connectionsOf", () => {
  it("lists outgoing then incoming relationships", () => {
    expect(
      connectionsOf(graph, "s").map(({ edge, direction, other }) => [edge.id, direction, other.id]),
    ).toEqual([
      ["e2", "out", "i"],
      ["e1", "in", "d"],
    ]);
  });

  it("returns nothing for unknown nodes", () => {
    expect(connectionsOf(graph, "missing")).toEqual([]);
  });
});
