import { describe, expect, it } from "vitest";

import type { EntityType, InvestigationGraph } from "../domain/entities";
import { sampleGraph } from "../demo/sampleGraph";
import { DEFAULT_GRAPH_PALETTE, GRAPH_PALETTES } from "../theme/graphPalette";
import { buildGraph } from "./buildGraph";
import {
  CURVATURE,
  DIM_OPACITY,
  FOCUS_THREAD_WIDTH,
  GROUP_RING_WIDTH,
  HALO_WIDTH,
  LOW_CONFIDENCE_OPACITY,
  LOW_CONFIDENCE_WIDTH_FACTOR,
  OUTER_GAP,
  PIN_WIDTH,
  SELECTED_SCALE,
} from "./encoding";
import { collapseGroups } from "./groups";
import {
  computeHighlight,
  connectionsOf,
  countByType,
  edgeView,
  focusSummary,
  INITIAL_VIEW_STATE,
  neighborhoodOf,
  nodeView,
  PULSE_SCALE,
  searchNodes,
  SUMMARY_LIMIT,
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
    { id: "s", label: "www.example.org", type: "netblock", ...prov },
    { id: "i", label: "192.0.2.1", type: "ip_address", ...prov },
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
      computeHighlight(graph, state({ hoveredNode: "i", hiddenTypes: hidden("ip_address") })).focus,
    ).toBeNull();
  });
});

describe("neighborhoodOf", () => {
  it("includes the node and both in- and out-neighbors", () => {
    expect([...neighborhoodOf(graph, "s")].sort()).toEqual(["d", "i", "s"]);
  });
});

describe("nodeView", () => {
  it("draws every visible node as a knot in its type's color", () => {
    const view = state();
    const result = nodeView(graph, "d", view, computeHighlight(graph, view));
    const typeColor = DEFAULT_GRAPH_PALETTE.types.domain;
    expect(result).toMatchObject({
      size: graph.getNodeAttribute("d", "size"),
      ringColor: typeColor,
      centerColor: typeColor,
      faceColor: DEFAULT_GRAPH_PALETTE.surface,
      haloSize: 0,
      pinSize: 0,
      opacity: 1,
      label: "example.org",
      labelColor: DEFAULT_GRAPH_PALETTE.label,
      labelPosition: "right",
      labelAttachment: null,
    });
    expect(result.labelVisibility).toBeUndefined();
  });

  it("hides nodes of hidden types", () => {
    const view = state({ hiddenTypes: hidden("asn") });
    expect(nodeView(graph, "a", view, computeHighlight(graph, view))).toEqual({
      visibility: "hidden",
      labelVisibility: "hidden",
    });
  });

  it("keeps the hovered node's neighborhood at full strength and dims the rest", () => {
    const view = state({ hoveredNode: "s" });
    const highlight = computeHighlight(graph, view);
    expect(nodeView(graph, "i", view, highlight)).toMatchObject({
      labelVisibility: "visible",
      depth: "topNodes",
      opacity: 1,
    });
    const dimmed = nodeView(graph, "a", view, highlight);
    expect(dimmed.labelVisibility).toBe("hidden");
    expect(dimmed.opacity).toBe(DIM_OPACITY);
    // Dimming is opacity, so the colors stay the type's.
    expect(dimmed.ringColor).toBe(DEFAULT_GRAPH_PALETTE.types.asn);
  });

  it("enlarges the selected node, rings it in brass and labels it in brass text", () => {
    const view = state({ selectedNode: "d" });
    const result = nodeView(graph, "d", view, computeHighlight(graph, view));
    const base = graph.getNodeAttribute("d", "size");
    expect(result.size).toBeCloseTo(base * SELECTED_SCALE + HALO_WIDTH + OUTER_GAP);
    expect(result).toMatchObject({
      haloSize: HALO_WIDTH,
      haloColor: DEFAULT_GRAPH_PALETTE.selection,
      labelColor: DEFAULT_GRAPH_PALETTE.selectionText,
      labelVisibility: "visible",
      zIndex: 2,
    });
  });

  it("draws pinned nodes with a brass ring and the pin glyph", () => {
    const pinned = buildGraph(tiny, { layoutIterations: 0 });
    pinned.setNodeAttribute("i", "pinned", true);
    const view = state();
    const result = nodeView(pinned, "i", view, computeHighlight(pinned, view), {
      theme: "light",
      palette: GRAPH_PALETTES.light,
    });
    expect(result).toMatchObject({
      pinSize: PIN_WIDTH,
      pinColor: GRAPH_PALETTES.light.selection,
      labelAttachment: "pin:light",
      labelAttachmentPlacement: "left",
    });
    expect(result.size).toBeCloseTo(pinned.getNodeAttribute("i", "size") + PIN_WIDTH + OUTER_GAP);
  });

  it("emphasizes pulsing nodes and never dims them", () => {
    const view = state({ hoveredNode: "d" });
    const highlight = computeHighlight(graph, view);
    const pulse = new Set(["a"]);
    const result = nodeView(graph, "a", view, highlight, { pulse });
    expect(result).toMatchObject({ labelVisibility: "visible", zIndex: 2, opacity: 1 });
    expect(result.size).toBeCloseTo(graph.getNodeAttribute("a", "size") * PULSE_SCALE);
    // Hidden types stay hidden.
    const hiddenView = state({ hiddenTypes: hidden("asn") });
    expect(
      nodeView(graph, "a", hiddenView, computeHighlight(graph, hiddenView), { pulse }),
    ).toMatchObject({ visibility: "hidden" });
  });

  it("draws a group as a double-ringed knot with its count and type name", () => {
    const leaves = Array.from({ length: 30 }, (_, i) => ({
      id: `u${String(i)}`,
      label: `https://example.org/${String(i)}`,
      type: "url" as const,
      ...prov,
    }));
    const display = collapseGroups(
      {
        nodes: [...tiny.nodes, ...leaves],
        edges: [
          ...tiny.edges,
          ...leaves.map((leaf) => ({
            id: `h${leaf.id}`,
            from: "d",
            to: leaf.id,
            relation: "HOSTS",
            ...prov,
          })),
        ],
      },
      [{ id: "group:d|url", parent: "d", type: "url", members: leaves.map((leaf) => leaf.id) }],
    );
    const grouped = buildGraph(display, { layoutIterations: 0 });
    const view = state();
    const result = nodeView(grouped, "group:d|url", view, computeHighlight(grouped, view));
    expect(result).toMatchObject({
      label: "30",
      labelPosition: "over",
      labelVisibility: "visible",
      labelColor: DEFAULT_GRAPH_PALETTE.text,
      innerRingSize: GROUP_RING_WIDTH,
      centerColor: DEFAULT_GRAPH_PALETTE.surface,
      labelAttachment: "groupType:dark",
      labelAttachmentPlacement: "below",
    });
  });
});

describe("edgeView", () => {
  it("draws threads in --hair, curved, labels hidden", () => {
    const view = state();
    expect(edgeView(graph, "e1", view, computeHighlight(graph, view))).toEqual({
      color: DEFAULT_GRAPH_PALETTE.edge,
      size: 1,
      opacity: 1,
      curvature: CURVATURE,
      zIndex: 0,
      labelVisibility: "hidden",
    });
  });

  it("hides edges touching a hidden type", () => {
    const view = state({ hiddenTypes: hidden("ip_address") });
    const highlight = computeHighlight(graph, view);
    expect(edgeView(graph, "e2", view, highlight)).toEqual({ visibility: "hidden" });
    expect(edgeView(graph, "e3", view, highlight)).toEqual({ visibility: "hidden" });
    expect(edgeView(graph, "e1", view, highlight).visibility).toBeUndefined();
  });

  it("turns the focused node's threads brass, 2 px, and shows only their labels", () => {
    const view = state({ hoveredNode: "i" });
    const highlight = computeHighlight(graph, view);
    expect(edgeView(graph, "e2", view, highlight)).toMatchObject({
      color: DEFAULT_GRAPH_PALETTE.selection,
      size: FOCUS_THREAD_WIDTH,
      label: "resolves to",
      labelVisibility: "visible",
      labelColor: DEFAULT_GRAPH_PALETTE.label,
      depth: "topEdges",
    });
    expect(edgeView(graph, "e1", view, highlight)).toMatchObject({
      color: DEFAULT_GRAPH_PALETTE.edge,
      opacity: DIM_OPACITY,
      labelVisibility: "hidden",
    });
  });

  it("draws uncertain threads thinner and faint", () => {
    const shaky = buildGraph(
      {
        nodes: tiny.nodes,
        edges: [
          { ...tiny.edges[0]!, confidence: 0.5 },
          {
            ...tiny.edges[1]!,
            relation: "HAS_PTR",
            details: {
              sources: ["shuttle:dns@1"],
              observation_count: 1,
              admiralty: "B2",
              note: "forward_confirmed: false. PTR not confirmed by forward DNS.",
              evidence_refs: [],
            },
          },
          tiny.edges[2]!,
        ],
      },
      { layoutIterations: 0 },
    );
    const view = state();
    const highlight = computeHighlight(shaky, view);
    for (const edge of ["e1", "e2"]) {
      expect(edgeView(shaky, edge, view, highlight)).toMatchObject({
        size: LOW_CONFIDENCE_WIDTH_FACTOR,
        opacity: LOW_CONFIDENCE_OPACITY,
      });
    }
    expect(edgeView(shaky, "e3", view, highlight)).toMatchObject({ size: 1, opacity: 1 });
  });
});

describe("the theme's palette", () => {
  it.each(["dark", "light"] as const)(
    "paints knots, threads and labels from %s tokens",
    (theme) => {
      const palette = GRAPH_PALETTES[theme];
      const context = { palette, theme };
      const view = state({ hoveredNode: "i", selectedNode: "i" });
      const highlight = computeHighlight(graph, view);
      expect(nodeView(graph, "i", view, highlight, context)).toMatchObject({
        ringColor: palette.types.ip_address,
        faceColor: palette.surface,
        haloColor: palette.selection,
        labelColor: palette.selectionText,
      });
      expect(edgeView(graph, "e2", view, highlight, context)).toMatchObject({
        color: palette.selection,
        labelColor: palette.label,
      });
      expect(edgeView(graph, "e1", view, highlight, context).color).toBe(palette.edge);
    },
  );

  it("switches every color with the theme", () => {
    const view = state({ selectedNode: "d" });
    const highlight = computeHighlight(graph, view);
    const dark = nodeView(graph, "d", view, highlight, { palette: GRAPH_PALETTES.dark });
    const light = nodeView(graph, "d", view, highlight, { palette: GRAPH_PALETTES.light });
    expect(light.faceColor).not.toBe(dark.faceColor);
    expect(light.haloColor).not.toBe(dark.haloColor);
    expect(light.labelColor).not.toBe(dark.labelColor);
  });
});

describe("focusSummary", () => {
  it("is empty when nothing is hovered or selected", () => {
    const view = state();
    expect(focusSummary(graph, view, computeHighlight(graph, view))).toBe("");
  });

  it("lists the relationships whose labels are drawn", () => {
    const view = state({ hoveredNode: "s" });
    expect(focusSummary(graph, view, computeHighlight(graph, view))).toBe(
      "www.example.org: resolves to 192.0.2.1; example.org has subdomain it",
    );
  });

  it("leaves out hidden neighbours and caps long lists", () => {
    const view = state({ hoveredNode: "s", hiddenTypes: hidden("domain") });
    expect(focusSummary(graph, view, computeHighlight(graph, view))).toBe(
      "www.example.org: resolves to 192.0.2.1",
    );
    const leaves = Array.from({ length: SUMMARY_LIMIT + 3 }, (_, i) => ({
      id: `n${String(i)}`,
      label: `n${String(i)}`,
      type: "ip_address" as const,
      ...prov,
    }));
    const hub = buildGraph(
      {
        nodes: [tiny.nodes[0]!, ...leaves],
        edges: leaves.map((leaf) => ({
          id: `x${leaf.id}`,
          from: "d",
          to: leaf.id,
          relation: "resolves_to",
          ...prov,
        })),
      },
      { layoutIterations: 0 },
    );
    const hubView = state({ selectedNode: "d" });
    expect(focusSummary(hub, hubView, computeHighlight(hub, hubView))).toMatch(/; and 3 more$/);
  });
});

describe("toggleType", () => {
  it("adds and removes a type without mutating the input", () => {
    const original = hidden("ip_address");
    expect([...toggleType(original, "asn")].sort()).toEqual(["asn", "ip_address"]);
    expect([...toggleType(original, "ip_address")]).toEqual([]);
    expect([...original]).toEqual(["ip_address"]);
  });
});

describe("countByType", () => {
  it("counts types in canonical order", () => {
    expect(countByType(tiny.nodes)).toEqual([
      { type: "domain", count: 1 },
      { type: "ip_address", count: 1 },
      { type: "netblock", count: 1 },
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
      searchNodes(nodes, "example", hidden("netblock"), 50).some((n) => n.type === "netblock"),
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
