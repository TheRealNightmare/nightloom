import { describe, expect, it } from "vitest";

import { ENTITY_TYPE_INFO, type EntityNode, type RelationEdge } from "../domain/entities";
import { sampleGraph } from "../demo/sampleGraph";
import {
  buildGraph,
  createGraph,
  MAX_LAYOUT_ITERATIONS,
  nodeSize,
  syncGraph,
  unitHash,
} from "./buildGraph";

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

describe("syncGraph", () => {
  const node = (id: string, type: EntityNode["type"] = "domain"): EntityNode => ({
    id,
    label: id,
    type,
    source: "manual",
    first_seen: "2026-11-10T10:00:00Z",
    last_seen: "2026-11-10T10:00:00Z",
    confidence: 0.8,
  });
  const edge = (from: string, to: string, relation = "HAS_SUBDOMAIN"): RelationEdge => ({
    id: `${from}|${relation}|${to}`,
    from,
    to,
    relation,
    source: "manual",
    first_seen: "2026-11-10T10:00:00Z",
    last_seen: "2026-11-10T10:00:00Z",
    confidence: 0.8,
  });
  const a = node("a");
  const b = node("b");
  const c = node("c");

  it("adds a new node next to its neighbours and leaves the others in place", () => {
    const graph = createGraph();
    syncGraph(graph, { nodes: [a, b], edges: [edge("a", "b")] });
    graph.mergeNodeAttributes("a", { x: 500, y: 500 });
    graph.mergeNodeAttributes("b", { x: -500, y: -500 });

    const result = syncGraph(graph, { nodes: [a, b, c], edges: [edge("a", "b"), edge("a", "c")] });
    expect(result.addedNodes).toEqual(["c"]);
    expect(result.addedEdges).toEqual(["a|HAS_SUBDOMAIN|c"]);
    expect(graph.getNodeAttributes("a")).toMatchObject({ x: 500, y: 500 });
    const { x, y } = graph.getNodeAttributes("c");
    expect(Math.hypot(x - 500, y - 500)).toBeLessThan(30);
    expect(graph.getNodeAttribute("a", "size")).toBeCloseTo(nodeSize(2));
  });

  it("resizes nodes without attribute hints, so sigma re-indexes new nodes", () => {
    // With hints, sigma repaints in place and throws for a node it has not indexed yet.
    const graph = createGraph();
    syncGraph(graph, { nodes: [a], edges: [] });
    const events: unknown[] = [];
    graph.on("eachNodeAttributesUpdated", (event: { hints?: unknown }) => {
      events.push(event.hints);
    });
    syncGraph(graph, { nodes: [a, b], edges: [edge("a", "b")] });
    expect(events).toHaveLength(1);
    expect(events[0] ?? null).toBeNull();
  });

  it("places an unconnected new node just outside the others", () => {
    const graph = createGraph();
    syncGraph(graph, { nodes: [a, b], edges: [] });
    syncGraph(graph, { nodes: [a, b, c], edges: [] });
    const radius = (id: string) =>
      Math.hypot(graph.getNodeAttribute(id, "x"), graph.getNodeAttribute(id, "y"));
    expect(radius("c")).toBeGreaterThan(Math.max(radius("a"), radius("b")));
  });

  it("removes what is gone and updates what changed", () => {
    const graph = createGraph();
    const ab = edge("a", "b");
    syncGraph(graph, { nodes: [a, b, c], edges: [ab, edge("b", "c")] });
    const renamed = { ...b, label: "B!", type: "ip_address" as const };
    const result = syncGraph(graph, { nodes: [a, renamed], edges: [ab] });
    expect(result).toMatchObject({ removedNodes: 1, removedEdges: 1, changed: true });
    expect(graph.hasNode("c")).toBe(false);
    expect(graph.getNodeAttributes("b")).toMatchObject({
      label: "B!",
      entityType: "ip_address",
      color: ENTITY_TYPE_INFO.ip_address.color,
    });
    expect(syncGraph(graph, { nodes: [a, renamed], edges: [ab] }).changed).toBe(false);
  });

  it("updates an edge's attributes in place", () => {
    const graph = createGraph();
    const first = edge("a", "b");
    syncGraph(graph, { nodes: [a, b], edges: [first] });
    const again = { ...first, confidence: 1 };
    expect(syncGraph(graph, { nodes: [a, b], edges: [again] }).changed).toBe(true);
    expect(graph.getEdgeAttribute(first.id, "relation")).toBe(again);
  });

  it("keeps several relationship types between the same two entities", () => {
    const graph = createGraph();
    syncGraph(graph, {
      nodes: [a, b],
      edges: [edge("a", "b", "HAS_MX"), edge("a", "b", "HAS_NS")],
    });
    expect(graph.size).toBe(2);
  });

  it("skips edges whose ends are missing and self-loops", () => {
    const graph = createGraph();
    syncGraph(graph, { nodes: [a], edges: [edge("a", "zz"), edge("a", "a")] });
    expect(graph.size).toBe(0);
  });

  it("hashes strings to [0, 1) deterministically", () => {
    expect(unitHash("x")).toBe(unitHash("x"));
    expect(unitHash("x")).not.toBe(unitHash("y"));
    for (const text of ["", "a", "domain:example.org"]) {
      expect(unitHash(text)).toBeGreaterThanOrEqual(0);
      expect(unitHash(text)).toBeLessThan(1);
    }
  });
});
