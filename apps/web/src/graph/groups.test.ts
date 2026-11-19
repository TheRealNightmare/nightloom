import { describe, expect, it } from "vitest";

import type { EntityNode, EntityType, InvestigationGraph, RelationEdge } from "../domain/entities";
import { DEFAULT_GRAPH_PALETTE } from "../theme/graphPalette";
import { createGraph, syncGraph } from "./buildGraph";
import { groupKnotSize, KNOT_MAX_SIZE } from "./encoding";
import { applyFilters, EMPTY_FILTERS } from "./filters";
import {
  collapseGroups,
  computeGroups,
  GROUP_THRESHOLD,
  groupContaining,
  groupId,
  groupLabel,
  isGroupId,
  MIXED_RELATION,
} from "./groups";

const prov = {
  source: "manual",
  first_seen: "2026-11-10T10:00:00Z",
  last_seen: "2026-11-12T10:00:00Z",
  confidence: 0.8,
};

function entity(id: string, type: EntityType = "url", extra: Partial<EntityNode> = {}): EntityNode {
  return { id, label: id, type, ...prov, ...extra };
}

function link(from: string, to: string, relation = "HOSTS"): RelationEdge {
  return { id: `${from}|${relation}|${to}`, from, to, relation, ...prov };
}

/** A domain hosting `count` URLs (leaves), linked to an IP that resolves elsewhere. */
function hub(count: number, relation = "HOSTS"): InvestigationGraph {
  const urls = Array.from({ length: count }, (_, i) =>
    entity(`url:https://example.org/${String(i)}`, "url", {
      first_seen: `2026-11-${String(10 + (i % 5)).padStart(2, "0")}T00:00:00Z`,
      confidence: i === 3 ? 0.95 : 0.5,
      source: i === 0 ? "shuttle:wayback@1.0.0" : "manual",
    }),
  );
  return {
    nodes: [
      entity("domain:example.org", "domain"),
      entity("ip_address:192.0.2.1", "ip_address"),
      entity("asn:64496", "asn"),
      ...urls,
    ],
    edges: [
      link("domain:example.org", "ip_address:192.0.2.1", "RESOLVES_TO"),
      link("ip_address:192.0.2.1", "asn:64496", "ANNOUNCED_BY"),
      ...urls.map((url) => link("domain:example.org", url.id, relation)),
    ],
  };
}

const all = (data: InvestigationGraph) => new Set(data.nodes.map((node) => node.id));

describe("computeGroups", () => {
  it("groups more than 25 leaves of one type around one entity", () => {
    expect(computeGroups(hub(GROUP_THRESHOLD), all(hub(GROUP_THRESHOLD)))).toEqual([]);
    const data = hub(GROUP_THRESHOLD + 1);
    const [group, ...rest] = computeGroups(data, all(data));
    expect(rest).toEqual([]);
    expect(group?.id).toBe(groupId("domain:example.org", "url"));
    expect(group?.parent).toBe("domain:example.org");
    expect(group?.type).toBe("url");
    expect(group?.members).toHaveLength(26);
    expect(isGroupId(group?.id ?? "")).toBe(true);
    expect(isGroupId("domain:example.org")).toBe(false);
  });

  it("keeps different types apart and ignores non-leaves", () => {
    const data = hub(30);
    const domains = Array.from({ length: 20 }, (_, i) =>
      entity(`domain:s${String(i)}.example.org`, "domain"),
    );
    // One URL is also linked to the IP: no longer a leaf.
    const extra: InvestigationGraph = {
      nodes: [...data.nodes, ...domains],
      edges: [
        ...data.edges,
        ...domains.map((d) => link("domain:example.org", d.id, "HAS_SUBDOMAIN")),
        link("ip_address:192.0.2.1", "url:https://example.org/0", "RELATED_TO"),
      ],
    };
    const groups = computeGroups(extra, all(extra));
    expect(groups.map((g) => [g.type, g.members.length])).toEqual([["url", 29]]);
    expect(computeGroups(extra, all(extra), 10).map((g) => g.type)).toEqual(["url", "domain"]);
  });

  it("is computed after filters", () => {
    const data = hub(40);
    // Hiding 20 URLs leaves 20: no group.
    const hidden = data.nodes.slice(3, 23).map((n) => n.id);
    const visible = new Set([...all(data)].filter((id) => !hidden.includes(id)));
    expect(computeGroups(data, visible)).toEqual([]);
    // Hiding the IP turns nothing into a leaf that was not one, but hiding the
    // domain's other link makes the domain itself... still a parent.
    const filtered = applyFilters(
      data,
      { ...EMPTY_FILTERS, hiddenTypes: new Set(["ip_address"]) },
      { now: 0 },
    );
    expect(computeGroups(data, filtered)[0]?.members).toHaveLength(40);
  });

  it("does not treat two leaves linked to each other as a group", () => {
    const pair: InvestigationGraph = {
      nodes: [entity("a"), entity("b")],
      edges: [link("a", "b")],
    };
    expect(computeGroups(pair, all(pair), 0)).toEqual([]);
  });
});

describe("collapseGroups", () => {
  it("replaces the members by one group node and one edge", () => {
    const data = hub(312);
    const groups = computeGroups(data, all(data));
    const collapsed = collapseGroups(data, groups);
    expect(collapsed.nodes).toHaveLength(4);
    const group = collapsed.nodes.find((n) => n.group);
    expect(group).toMatchObject({
      id: groupId("domain:example.org", "url"),
      label: "312 URLs",
      type: "url",
      first_seen: "2026-11-10T00:00:00Z",
      last_seen: "2026-11-12T10:00:00Z",
      confidence: 0.95,
      source: "manual, shuttle:wayback@1.0.0",
    });
    expect(group?.group?.members).toHaveLength(312);
    expect(group?.group?.parent).toBe("domain:example.org");
    const edge = collapsed.edges.find((e) => e.to === group?.id);
    expect(edge).toMatchObject({ from: "domain:example.org", relation: "HOSTS" });
    expect(collapsed.edges).toHaveLength(3);
  });

  it("points the edge at the parent when every member points at it, and marks mixed links", () => {
    const base = hub(30);
    const reversed: InvestigationGraph = {
      nodes: base.nodes,
      edges: base.edges.map((e) =>
        e.relation === "HOSTS" ? { ...link(e.to, e.from, "MENTIONS") } : e,
      ),
    };
    const [group] = computeGroups(reversed, all(reversed));
    const edge = collapseGroups(reversed, group ? [group] : []).edges.at(-1);
    expect(edge).toMatchObject({ from: group?.id, to: "domain:example.org", relation: "MENTIONS" });

    const mixed: InvestigationGraph = {
      nodes: base.nodes,
      edges: base.edges.map((e, i) =>
        e.relation === "HOSTS" && i % 2 === 0 ? { ...e, relation: "MENTIONS" } : e,
      ),
    };
    const groups = computeGroups(mixed, all(mixed));
    expect(collapseGroups(mixed, groups).edges.at(-1)?.relation).toBe(MIXED_RELATION);
  });

  it("returns the data unchanged without groups", () => {
    const data = hub(3);
    expect(collapseGroups(data, [])).toBe(data);
  });

  it("finds the group of a collapsed entity", () => {
    const data = hub(30);
    const groups = computeGroups(data, all(data));
    expect(groupContaining(groups, "url:https://example.org/7")?.id).toBe(groups[0]?.id);
    expect(groupContaining(groups, "domain:example.org")).toBeUndefined();
  });
});

describe("drawing groups", () => {
  it("labels groups with the type's plural", () => {
    expect(groupLabel("url", 312)).toBe("312 URLs");
    expect(groupLabel("ip_address", 1500)).toBe("1,500 IP addresses");
    expect(groupLabel("domain", 1)).toBe("1 Domain");
  });

  it("sizes group nodes by count, in their type's color", () => {
    const data = hub(312);
    const graph = createGraph();
    syncGraph(graph, collapseGroups(data, computeGroups(data, all(data))));
    const id = groupId("domain:example.org", "url");
    expect(graph.getNodeAttribute(id, "size")).toBe(groupKnotSize(312));
    expect(graph.getNodeAttribute(id, "color")).toBe(DEFAULT_GRAPH_PALETTE.types.url);
    // Entities are capped too, so a hub whose leaves are filtered out stays readable.
    expect(graph.getNodeAttribute("domain:example.org", "size")).toBeLessThanOrEqual(KNOT_MAX_SIZE);
  });
});
