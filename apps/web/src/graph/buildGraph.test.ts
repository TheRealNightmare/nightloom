import { describe, expect, it } from "vitest";

import { ENTITY_TYPE_INFO } from "../domain/entities";
import { sampleGraph } from "../demo/sampleGraph";
import { buildGraph, MAX_LAYOUT_ITERATIONS } from "./buildGraph";

describe("buildGraph", () => {
  const graph = buildGraph(sampleGraph);

  it("contains every node and edge of the fixture", () => {
    expect(graph.order).toBe(sampleGraph.nodes.length);
    expect(graph.size).toBe(sampleGraph.edges.length);
    for (const edge of sampleGraph.edges) {
      expect(graph.hasDirectedEdge(edge.from, edge.to)).toBe(true);
      expect(graph.getEdgeAttribute(edge.id, "label")).toBe(edge.relation.replaceAll("_", " "));
    }
  });

  it("colors nodes by entity type and sizes them by degree", () => {
    graph.forEachNode((node, attributes) => {
      expect(attributes.color).toBe(ENTITY_TYPE_INFO[attributes.entityType].color);
      expect(attributes.size).toBeCloseTo(5 + Math.sqrt(graph.degree(node)) * 2.4);
      expect(attributes.entity.id).toBe(node);
    });
  });

  it("lays out every node at a finite position", () => {
    graph.forEachNode((_node, { x, y }) => {
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
    });
  });

  it("is deterministic", () => {
    const again = buildGraph(sampleGraph);
    graph.forEachNode((node, { x, y }) => {
      expect(again.getNodeAttribute(node, "x")).toBe(x);
      expect(again.getNodeAttribute(node, "y")).toBe(y);
    });
  });

  it("keeps the circular start positions when the layout is disabled", () => {
    const flat = buildGraph(sampleGraph, { layoutIterations: 0 });
    flat.forEachNode((_node, { x, y }) => {
      expect(Math.hypot(x, y)).toBeCloseTo(100);
    });
  });

  it("bounds the number of layout iterations", () => {
    const capped = buildGraph(sampleGraph, { layoutIterations: 1e9 });
    const max = buildGraph(sampleGraph, { layoutIterations: MAX_LAYOUT_ITERATIONS });
    capped.forEachNode((node, { x }) => {
      expect(x).toBe(max.getNodeAttribute(node, "x"));
    });
  });

  it("handles an empty graph", () => {
    expect(buildGraph({ nodes: [], edges: [] }).order).toBe(0);
  });
});
