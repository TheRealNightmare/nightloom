/**
 * A synthetic case graph for performance tests (dev only: this directory is
 * never imported by the app, so none of it reaches the production bundle).
 *
 * Shaped like a real attack-surface map: domains with subdomains that
 * resolve to addresses inside netblocks announced by a few ASNs, using only
 * documentation names and ranges.
 */
import type { EntityNode, InvestigationGraph, RelationEdge } from "../domain/entities";

const PROVENANCE = {
  source: "manual",
  first_seen: "2026-11-10T10:00:00Z",
  last_seen: "2026-11-10T10:00:00Z",
  confidence: 0.8,
};

export function largeGraph(nodeCount = 2000): InvestigationGraph {
  const nodes: EntityNode[] = [];
  const edges: RelationEdge[] = [];
  const add = (node: Omit<EntityNode, keyof typeof PROVENANCE>) => {
    nodes.push({ ...node, ...PROVENANCE });
    return node.id;
  };
  const link = (from: string, relation: string, to: string) => {
    edges.push({ id: `${from}|${relation}|${to}`, from, to, relation, ...PROVENANCE });
  };

  const asns = Array.from({ length: 8 }, (_, i) =>
    add({ id: `asn:${64496 + i}`, label: `AS${64496 + i}`, type: "asn" }),
  );
  const netblocks = Array.from({ length: 24 }, (_, i) => {
    const id = add({
      id: `netblock:198.51.${i}.0/24`,
      label: `198.51.${i}.0/24`,
      type: "netblock",
    });
    link(id, "ANNOUNCED_BY", asns[i % asns.length] ?? "");
    return id;
  });
  let i = 0;
  while (nodes.length < nodeCount) {
    const apex = add({
      id: `domain:site${i}.example.org`,
      label: `site${i}.example.org`,
      type: "domain",
    });
    for (let s = 0; s < 3 && nodes.length < nodeCount; s += 1) {
      const sub = add({
        id: `domain:s${s}.site${i}.example.org`,
        label: `s${s}.site${i}.example.org`,
        type: "domain",
      });
      link(apex, "HAS_SUBDOMAIN", sub);
      if (nodes.length >= nodeCount) break;
      const ip = add({
        id: `ip_address:198.51.${i % 24}.${(i * 3 + s) % 250}`,
        label: `198.51.${i % 24}.${(i * 3 + s) % 250}`,
        type: "ip_address",
      });
      link(sub, "RESOLVES_TO", ip);
      link(ip, "WITHIN", netblocks[i % netblocks.length] ?? "");
    }
    i += 1;
  }
  // Ids are unique by construction except for repeated addresses; keep the first.
  const seen = new Set<string>();
  const unique = nodes.filter((node) => !seen.has(node.id) && seen.add(node.id));
  const edgeIds = new Set<string>();
  return {
    nodes: unique,
    edges: edges.filter((edge) => !edgeIds.has(edge.id) && edgeIds.add(edge.id)),
  };
}
