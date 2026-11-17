import { describe, expect, it } from "vitest";

import { buildGraph } from "../../graph/buildGraph";
import { ENTITY_TYPE_INFO } from "../../domain/entities";
import { makeEntity, makeProvenance, makeRelationship } from "../../test/caseFixtures";
import {
  toEntityNode,
  toInvestigationGraph,
  toRelationEdge,
  withEntity,
  withoutEntity,
  withRelationship,
} from "./mapping";

const apex = makeEntity("asn", "AS64496", {
  key: "asn:64496",
  properties: { name: "Example Net", data_classes: ["a", "b"] },
  provenance: makeProvenance({
    sources: ["manual", "shuttle:rdap@1.0"],
    max_confidence: 0.9,
    observation_count: 4,
    admiralty: "A1",
    evidence_refs: ["s3://evidence/1"],
    note: "Seen in RDAP",
  }),
});
const net = makeEntity("netblock", "192.0.2.0/24");
const announced = makeRelationship("ANNOUNCED_BY", net, apex);

describe("API → graph attributes", () => {
  it("maps an entity onto a node: key as id, value as label, full provenance as details", () => {
    expect(toEntityNode(apex)).toEqual({
      id: "asn:64496",
      label: "AS64496",
      type: "asn",
      sensitivity: "infrastructure",
      properties: { name: "Example Net", data_classes: ["a", "b"] },
      source: "manual, shuttle:rdap@1.0",
      first_seen: "2026-11-10T10:00:00Z",
      last_seen: "2026-11-12T08:30:00Z",
      confidence: 0.9,
      details: {
        sources: ["manual", "shuttle:rdap@1.0"],
        observation_count: 4,
        admiralty: "A1",
        note: "Seen in RDAP",
        evidence_refs: ["s3://evidence/1"],
      },
    });
  });

  it("maps a relationship onto an edge", () => {
    expect(toRelationEdge(announced)).toMatchObject({
      id: announced.id,
      from: net.key,
      to: "asn:64496",
      relation: "ANNOUNCED_BY",
      confidence: 0.8,
    });
  });

  it("produces graph attributes with the shared palette and readable edge labels", () => {
    const data = toInvestigationGraph({ nodes: [apex, net], edges: [announced], truncated: false });
    const graph = buildGraph(data, { layoutIterations: 0 });
    expect(graph.getNodeAttributes("asn:64496")).toMatchObject({
      label: "AS64496",
      entityType: "asn",
      color: ENTITY_TYPE_INFO.asn.color,
    });
    expect(graph.getEdgeAttribute(announced.id, "label")).toBe("announced by");
  });
});

describe("local updates", () => {
  const data = toInvestigationGraph({ nodes: [apex, net], edges: [announced], truncated: false });

  it("adds a new entity and replaces an existing one, keeping the others as they were", () => {
    const ip = toEntityNode(makeEntity("ip_address", "192.0.2.1"));
    const added = withEntity(data, ip);
    expect(added.nodes.map((n) => n.id)).toEqual(["asn:64496", net.key, ip.id]);
    expect(added.nodes[0]).toBe(data.nodes[0]);

    const merged = toEntityNode({ ...apex, provenance: makeProvenance({ observation_count: 5 }) });
    const replaced = withEntity(data, merged);
    expect(replaced.nodes).toHaveLength(2);
    expect(replaced.nodes[0]).toBe(merged);
    expect(replaced.nodes[1]).toBe(data.nodes[1]);
    expect(replaced.edges).toBe(data.edges);
  });

  it("removes an entity with its relationships", () => {
    const removed = withoutEntity(data, net.key);
    expect(removed.nodes.map((n) => n.id)).toEqual(["asn:64496"]);
    expect(removed.edges).toEqual([]);
  });

  it("adds or replaces a relationship", () => {
    const again = toRelationEdge({
      ...announced,
      provenance: makeProvenance({ observation_count: 2 }),
    });
    expect(withRelationship(data, again).edges).toEqual([again]);
    const other = toRelationEdge(makeRelationship("RELATED_TO", net, apex));
    expect(withRelationship(data, other).edges).toHaveLength(2);
  });
});
