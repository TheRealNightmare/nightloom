/**
 * Demo investigation graph.
 *
 * Everything here is fictional and uses names and numbers reserved for
 * documentation, so it can never point at a real target:
 *
 * - Domains under `example.org` (RFC 2606)
 * - IPv4 addresses from the documentation ranges 192.0.2.0/24, 198.51.100.0/24
 *   and 203.0.113.0/24 (RFC 5737)
 * - AS numbers from the documentation range AS64496–AS64511 (RFC 5398)
 *
 * There are no person entities. The UI labels this data as a demo.
 */

import type {
  EntityNode,
  EntityType,
  InvestigationGraph,
  PropertyValue,
  Provenance,
  RelationEdge,
} from "../domain/entities";

type ProvenanceInput = Omit<Provenance, "last_seen"> & { last_seen?: string };

const LAST_SCAN = "2026-11-02T09:30:00Z";

function entity(
  id: string,
  label: string,
  type: EntityType,
  provenance: ProvenanceInput,
  properties?: Record<string, PropertyValue>,
): EntityNode {
  return {
    id,
    label,
    type,
    ...provenance,
    last_seen: provenance.last_seen ?? LAST_SCAN,
    ...(properties ? { properties } : {}),
  };
}

const dns = (first_seen: string, confidence = 0.95): ProvenanceInput => ({
  source: "passive-dns",
  first_seen,
  confidence,
});
const ct = (first_seen: string, confidence = 0.98): ProvenanceInput => ({
  source: "ct-logs",
  first_seen,
  confidence,
});
const rdap = (first_seen: string, confidence = 0.9): ProvenanceInput => ({
  source: "rdap",
  first_seen,
  confidence,
});
const fingerprint = (first_seen: string, confidence = 0.75): ProvenanceInput => ({
  source: "http-fingerprint",
  first_seen,
  confidence,
});

const nodes: EntityNode[] = [
  entity("org:example-org", "Example Org", "organization", rdap("2025-03-14T00:00:00Z", 0.85), {
    country: "Fictional",
    note: "Registrant organization of example.org",
  }),

  entity("domain:example.org", "example.org", "domain", rdap("2025-03-14T00:00:00Z", 1), {
    registrar: "Example Registrar",
    created: "2019-06-01",
  }),

  entity("sub:www.example.org", "www.example.org", "domain", dns("2025-03-15T10:00:00Z")),
  entity("sub:api.example.org", "api.example.org", "domain", dns("2025-04-02T08:12:00Z")),
  entity("sub:mail.example.org", "mail.example.org", "domain", dns("2025-03-15T10:00:00Z")),
  entity("sub:vpn.example.org", "vpn.example.org", "domain", ct("2025-08-21T13:40:00Z", 0.9)),
  entity("sub:dev.example.org", "dev.example.org", "domain", ct("2026-01-09T17:05:00Z", 0.8)),
  entity("sub:status.example.org", "status.example.org", "domain", dns("2025-11-30T06:00:00Z")),
  entity("sub:cdn.example.org", "cdn.example.org", "domain", dns("2025-05-18T12:00:00Z")),
  entity("sub:docs.example.org", "docs.example.org", "domain", ct("2025-09-03T09:25:00Z")),

  entity("ip:192.0.2.10", "192.0.2.10", "ip_address", dns("2025-03-15T10:00:00Z"), { version: 4 }),
  entity("ip:192.0.2.11", "192.0.2.11", "ip_address", dns("2025-04-02T08:12:00Z"), { version: 4 }),
  entity("ip:192.0.2.25", "192.0.2.25", "ip_address", dns("2025-03-15T10:00:00Z"), { version: 4 }),
  entity("ip:198.51.100.7", "198.51.100.7", "ip_address", dns("2025-08-21T13:40:00Z", 0.85), {
    version: 4,
  }),
  entity("ip:198.51.100.20", "198.51.100.20", "ip_address", dns("2026-01-09T17:05:00Z", 0.7), {
    version: 4,
  }),
  entity("ip:203.0.113.5", "203.0.113.5", "ip_address", dns("2025-05-18T12:00:00Z"), {
    version: 4,
  }),
  entity("ip:203.0.113.80", "203.0.113.80", "ip_address", dns("2025-11-30T06:00:00Z"), {
    version: 4,
  }),

  entity("net:192.0.2.0/24", "192.0.2.0/24", "netblock", rdap("2025-03-16T00:00:00Z"), {
    name: "EXAMPLE-NET-1",
  }),
  entity("net:198.51.100.0/24", "198.51.100.0/24", "netblock", rdap("2025-08-22T00:00:00Z"), {
    name: "EXAMPLE-NET-2",
  }),
  entity("net:203.0.113.0/24", "203.0.113.0/24", "netblock", rdap("2025-05-19T00:00:00Z"), {
    name: "EXAMPLE-CDN-NET",
  }),

  entity("asn:64496", "AS64496", "asn", rdap("2025-03-16T00:00:00Z"), {
    name: "Example Transit (fictional)",
  }),
  entity("asn:64500", "AS64500", "asn", rdap("2025-05-19T00:00:00Z"), {
    name: "Example Hosting (fictional)",
  }),

  entity("cert:wildcard", "*.example.org", "certificate", ct("2026-06-01T00:00:00Z", 1), {
    issuer: "Example Test CA",
    not_before: "2026-06-01",
    not_after: "2026-12-01",
    sha256: "3f1c…a9e2 (fictional)",
  }),
  entity("cert:mail", "mail.example.org", "certificate", ct("2026-04-11T00:00:00Z", 1), {
    issuer: "Example Test CA",
    not_before: "2026-04-11",
    not_after: "2026-10-09",
  }),

  entity(
    "svc:203.0.113.5:443",
    "203.0.113.5:443 (https)",
    "service",
    fingerprint("2025-05-20T00:00:00Z", 0.9),
    {
      port: 443,
      protocol: "tcp",
    },
  ),
  entity(
    "svc:198.51.100.7:22",
    "198.51.100.7:22 (ssh)",
    "service",
    fingerprint("2025-08-23T00:00:00Z", 0.9),
    {
      port: 22,
      protocol: "tcp",
    },
  ),
  entity(
    "svc:192.0.2.25:25",
    "192.0.2.25:25 (smtp)",
    "service",
    fingerprint("2025-03-17T00:00:00Z", 0.9),
    {
      port: 25,
      protocol: "tcp",
    },
  ),

  entity("tech:nginx", "nginx", "technology", fingerprint("2025-05-20T00:00:00Z")),
  entity("tech:openssh", "OpenSSH", "technology", fingerprint("2025-08-23T00:00:00Z", 0.85)),
  entity("tech:postfix", "Postfix", "technology", fingerprint("2025-03-17T00:00:00Z", 0.7)),
  entity("tech:react", "React", "technology", fingerprint("2025-09-04T00:00:00Z", 0.6)),

  entity(
    "url:www-home",
    "https://www.example.org/",
    "url",
    fingerprint("2025-03-15T10:00:00Z", 0.95),
  ),
  entity(
    "url:docs-login",
    "https://docs.example.org/login",
    "url",
    fingerprint("2025-09-04T00:00:00Z", 0.8),
  ),
];

let edgeCount = 0;
function link(
  from: string,
  relation: string,
  to: string,
  provenance: ProvenanceInput,
): RelationEdge {
  edgeCount += 1;
  return {
    id: `e${edgeCount}`,
    from,
    to,
    relation,
    ...provenance,
    last_seen: provenance.last_seen ?? LAST_SCAN,
  };
}

const edges: RelationEdge[] = [
  link("org:example-org", "owns", "domain:example.org", rdap("2025-03-14T00:00:00Z", 0.85)),
  link("org:example-org", "registered", "net:192.0.2.0/24", rdap("2025-03-16T00:00:00Z", 0.8)),

  ...["www", "api", "mail", "vpn", "dev", "status", "cdn", "docs"].map((name) =>
    link(
      "domain:example.org",
      "has_subdomain",
      `sub:${name}.example.org`,
      dns("2025-03-15T10:00:00Z"),
    ),
  ),

  link("sub:www.example.org", "resolves_to", "ip:192.0.2.10", dns("2025-03-15T10:00:00Z")),
  link("sub:api.example.org", "resolves_to", "ip:192.0.2.11", dns("2025-04-02T08:12:00Z")),
  link("sub:mail.example.org", "resolves_to", "ip:192.0.2.25", dns("2025-03-15T10:00:00Z")),
  link("sub:vpn.example.org", "resolves_to", "ip:198.51.100.7", dns("2025-08-21T13:40:00Z", 0.85)),
  link("sub:dev.example.org", "resolves_to", "ip:198.51.100.20", dns("2026-01-09T17:05:00Z", 0.7)),
  link("sub:status.example.org", "resolves_to", "ip:203.0.113.80", dns("2025-11-30T06:00:00Z")),
  link("sub:cdn.example.org", "resolves_to", "ip:203.0.113.5", dns("2025-05-18T12:00:00Z")),
  link("sub:docs.example.org", "resolves_to", "ip:203.0.113.5", dns("2025-09-03T09:25:00Z", 0.9)),
  link("sub:www.example.org", "resolves_to", "ip:203.0.113.5", dns("2026-02-10T00:00:00Z", 0.6)),

  link("ip:192.0.2.10", "within", "net:192.0.2.0/24", rdap("2025-03-16T00:00:00Z", 1)),
  link("ip:192.0.2.11", "within", "net:192.0.2.0/24", rdap("2025-04-03T00:00:00Z", 1)),
  link("ip:192.0.2.25", "within", "net:192.0.2.0/24", rdap("2025-03-16T00:00:00Z", 1)),
  link("ip:198.51.100.7", "within", "net:198.51.100.0/24", rdap("2025-08-22T00:00:00Z", 1)),
  link("ip:198.51.100.20", "within", "net:198.51.100.0/24", rdap("2026-01-10T00:00:00Z", 1)),
  link("ip:203.0.113.5", "within", "net:203.0.113.0/24", rdap("2025-05-19T00:00:00Z", 1)),
  link("ip:203.0.113.80", "within", "net:203.0.113.0/24", rdap("2025-12-01T00:00:00Z", 1)),

  link("net:192.0.2.0/24", "announced_by", "asn:64496", rdap("2025-03-16T00:00:00Z")),
  link("net:198.51.100.0/24", "announced_by", "asn:64496", rdap("2025-08-22T00:00:00Z")),
  link("net:203.0.113.0/24", "announced_by", "asn:64500", rdap("2025-05-19T00:00:00Z")),

  link("cert:wildcard", "covers", "domain:example.org", ct("2026-06-01T00:00:00Z", 1)),
  link("cert:wildcard", "covers", "sub:www.example.org", ct("2026-06-01T00:00:00Z", 1)),
  link("cert:wildcard", "covers", "sub:docs.example.org", ct("2026-06-01T00:00:00Z", 1)),
  link("cert:wildcard", "covers", "sub:vpn.example.org", ct("2026-06-01T00:00:00Z", 1)),
  link("cert:mail", "covers", "sub:mail.example.org", ct("2026-04-11T00:00:00Z", 1)),
  link(
    "svc:203.0.113.5:443",
    "presents",
    "cert:wildcard",
    fingerprint("2026-06-02T00:00:00Z", 0.95),
  ),

  link(
    "ip:203.0.113.5",
    "exposes",
    "svc:203.0.113.5:443",
    fingerprint("2025-05-20T00:00:00Z", 0.9),
  ),
  link(
    "ip:198.51.100.7",
    "exposes",
    "svc:198.51.100.7:22",
    fingerprint("2025-08-23T00:00:00Z", 0.9),
  ),
  link("ip:192.0.2.25", "exposes", "svc:192.0.2.25:25", fingerprint("2025-03-17T00:00:00Z", 0.9)),

  link("svc:203.0.113.5:443", "runs", "tech:nginx", fingerprint("2025-05-20T00:00:00Z")),
  link("svc:198.51.100.7:22", "runs", "tech:openssh", fingerprint("2025-08-23T00:00:00Z", 0.85)),
  link("svc:192.0.2.25:25", "runs", "tech:postfix", fingerprint("2025-03-17T00:00:00Z", 0.7)),

  link("sub:www.example.org", "serves", "url:www-home", fingerprint("2025-03-15T10:00:00Z", 0.95)),
  link(
    "sub:docs.example.org",
    "serves",
    "url:docs-login",
    fingerprint("2025-09-04T00:00:00Z", 0.8),
  ),
  link("url:docs-login", "uses", "tech:react", fingerprint("2025-09-04T00:00:00Z", 0.6)),
  link("url:www-home", "uses", "tech:nginx", fingerprint("2025-03-15T10:00:00Z", 0.7)),
];

export const sampleGraph: InvestigationGraph = { nodes, edges };
