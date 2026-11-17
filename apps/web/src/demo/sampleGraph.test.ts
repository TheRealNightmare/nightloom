import { describe, expect, it } from "vitest";

import { isEntityType, type EntityType } from "../domain/entities";
import { sampleGraph } from "./sampleGraph";

/** The demo may only show infrastructure and the organization, never people. */
const ALLOWED_TYPES: ReadonlySet<EntityType> = new Set([
  "organization",
  "domain",
  "ip_address",
  "asn",
  "netblock",
  "certificate",
  "url",
  "service",
  "technology",
]);

/** RFC 5737 documentation ranges. */
const DOCUMENTATION_PREFIXES = ["192.0.2.", "198.51.100.", "203.0.113."];
const IPV4 = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;

function allStrings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(allStrings);
  if (typeof value === "object" && value !== null) return Object.values(value).flatMap(allStrings);
  return [];
}

const { nodes, edges } = sampleGraph;

describe("sampleGraph fixture", () => {
  it("is a reasonably sized demo", () => {
    expect(nodes.length).toBeGreaterThanOrEqual(25);
    expect(nodes.length).toBeLessThanOrEqual(40);
    expect(edges.length).toBeGreaterThan(nodes.length);
  });

  it("has unique node and edge ids", () => {
    expect(new Set(nodes.map((node) => node.id)).size).toBe(nodes.length);
    expect(new Set(edges.map((edge) => edge.id)).size).toBe(edges.length);
  });

  it("only links nodes that exist, without self-loops", () => {
    const ids = new Set(nodes.map((node) => node.id));
    for (const edge of edges) {
      expect(ids, `${edge.id}.from`).toContain(edge.from);
      expect(ids, `${edge.id}.to`).toContain(edge.to);
      expect(edge.from).not.toBe(edge.to);
    }
  });

  it("uses only allowed entity types and no person entities", () => {
    for (const node of nodes) {
      expect(isEntityType(node.type)).toBe(true);
      expect(ALLOWED_TYPES, node.id).toContain(node.type);
    }
    expect(nodes.some((node) => node.type === "person")).toBe(false);
  });

  it("uses only RFC 5737 documentation IPv4 addresses anywhere", () => {
    const addresses = allStrings(sampleGraph).flatMap((text) => text.match(IPV4) ?? []);
    expect(addresses.length).toBeGreaterThan(0);
    for (const address of addresses) {
      expect(
        DOCUMENTATION_PREFIXES.some((prefix) => address.startsWith(prefix)),
        address,
      ).toBe(true);
    }
  });

  it("uses only example.org domains", () => {
    for (const node of nodes.filter((n) => n.type === "domain")) {
      expect(node.label === "example.org" || node.label.endsWith(".example.org")).toBe(true);
    }
    for (const node of nodes.filter((n) => n.type === "url")) {
      expect(new URL(node.label).hostname.endsWith("example.org")).toBe(true);
    }
  });

  it("uses only documentation AS numbers (AS64496–AS64511)", () => {
    const asns = nodes.filter((node) => node.type === "asn");
    expect(asns.length).toBeGreaterThan(0);
    for (const node of asns) {
      const number = Number(/^AS(\d+)$/.exec(node.label)?.[1]);
      expect(number).toBeGreaterThanOrEqual(64496);
      expect(number).toBeLessThanOrEqual(64511);
    }
    const mentioned = allStrings(sampleGraph).flatMap((text) => text.match(/\bAS\d+\b/g) ?? []);
    for (const asn of mentioned) {
      const number = Number(asn.slice(2));
      expect(number >= 64496 && number <= 64511, asn).toBe(true);
    }
  });

  it("names the fictional organization Example Org", () => {
    const orgs = nodes.filter((node) => node.type === "organization");
    expect(orgs.map((org) => org.label)).toEqual(["Example Org"]);
  });

  it("carries valid provenance on every node and edge", () => {
    for (const item of [...nodes, ...edges]) {
      expect(item.source, item.id).toMatch(/^[a-z][a-z0-9-]*$/);
      expect(item.confidence).toBeGreaterThanOrEqual(0);
      expect(item.confidence).toBeLessThanOrEqual(1);
      const first = Date.parse(item.first_seen);
      const last = Date.parse(item.last_seen);
      expect(Number.isNaN(first), item.id).toBe(false);
      expect(Number.isNaN(last), item.id).toBe(false);
      expect(first, item.id).toBeLessThanOrEqual(last);
    }
  });
});
