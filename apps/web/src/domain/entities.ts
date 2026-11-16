/**
 * Entity and relationship shapes shared by the UI.
 *
 * They mirror the planned data model in docs/ARCHITECTURE.md (entity types
 * inspired by STIX 2.1, provenance on every node and edge) so the demo fixture
 * already looks like what the API will return.
 */

export const ENTITY_TYPES = [
  // Infrastructure
  "domain",
  "subdomain",
  "ip",
  "asn",
  "netblock",
  "certificate",
  "url",
  "service",
  "technology",
  // Organizations and people
  "organization",
  "person",
  "email",
  "username",
  "social_profile",
  "phone",
  // Other
  "breach",
  "location",
  "image",
  "document",
  "crypto_wallet",
] as const;

export type EntityType = (typeof ENTITY_TYPES)[number];

export interface EntityTypeInfo {
  /** Human-readable name, as used in docs/ARCHITECTURE.md. */
  label: string;
  /** Node color on the dark theme (≥ 3:1 contrast against the canvas). */
  color: string;
}

export const ENTITY_TYPE_INFO: Readonly<Record<EntityType, EntityTypeInfo>> = {
  domain: { label: "Domain", color: "#6ea8fe" },
  subdomain: { label: "Subdomain", color: "#4fd1e8" },
  ip: { label: "IP address", color: "#ff9f6e" },
  asn: { label: "ASN", color: "#ff6b81" },
  netblock: { label: "Netblock", color: "#b794f6" },
  certificate: { label: "Certificate", color: "#5fd39b" },
  url: { label: "URL", color: "#a3b8cc" },
  service: { label: "Port / Service", color: "#c3e88d" },
  technology: { label: "Technology", color: "#e9a8f2" },
  organization: { label: "Organization", color: "#e5e9f0" },
  person: { label: "Person", color: "#f7a8b8" },
  email: { label: "Email", color: "#8bd5ca" },
  username: { label: "Username", color: "#91d7e3" },
  social_profile: { label: "Social profile", color: "#7dc4e4" },
  phone: { label: "Phone", color: "#eed49f" },
  breach: { label: "Breach", color: "#ed8796" },
  location: { label: "Location", color: "#a6da95" },
  image: { label: "Image", color: "#c6a0f6" },
  document: { label: "Document", color: "#b8c0e0" },
  crypto_wallet: { label: "Crypto wallet", color: "#f5a97f" },
};

/** Provenance carried by every node and edge (see docs/ARCHITECTURE.md#provenance). */
export interface Provenance {
  /** The Shuttle (or other producer) that reported it. */
  source: string;
  /** ISO 8601 timestamp of the first observation. */
  first_seen: string;
  /** ISO 8601 timestamp of the most recent observation. */
  last_seen: string;
  /** 0–1. */
  confidence: number;
}

export type PropertyValue = string | number | boolean;

export interface EntityNode extends Provenance {
  id: string;
  label: string;
  type: EntityType;
  /** Type-specific details (e.g. an ASN's registered name). */
  properties?: Readonly<Record<string, PropertyValue>>;
}

export interface RelationEdge extends Provenance {
  id: string;
  /** Id of the node the relationship starts from. */
  from: string;
  /** Id of the node the relationship points to. */
  to: string;
  /** Relationship kind, e.g. `resolves_to`. */
  relation: string;
}

export interface InvestigationGraph {
  nodes: readonly EntityNode[];
  edges: readonly RelationEdge[];
}

export function isEntityType(value: string): value is EntityType {
  return (ENTITY_TYPES as readonly string[]).includes(value);
}
