/**
 * Entity and relationship shapes shared by the UI.
 *
 * The entity types are those of `EntityType` in packages/core (in the same
 * order). The rules about them (sensitivity, which relationships connect
 * which types, value hints) are not repeated here: the UI reads them from
 * `GET /api/v1/schema`. Only presentation lives here: a label and a color per
 * type, which the WebGL renderer and the legend both use.
 */

export const ENTITY_TYPES = [
  "domain",
  "ip_address",
  "netblock",
  "asn",
  "certificate",
  "url",
  "service",
  "technology",
  "organization",
  "email",
  "username",
  "person",
  "phone",
  "social_profile",
  "location",
  "crypto_wallet",
  "document",
  "image",
  "breach",
] as const;

export type EntityType = (typeof ENTITY_TYPES)[number];

/** Mirrors `Sensitivity` in packages/core. */
export const SENSITIVITIES = ["infrastructure", "organization", "person"] as const;
export type Sensitivity = (typeof SENSITIVITIES)[number];

export interface EntityTypeInfo {
  /** Human-readable name (the same as `display_name` in the schema). */
  label: string;
  /** Node color on the dark theme (≥ 3:1 contrast against the canvas). */
  color: string;
}

/** The one palette: graph nodes, legend swatches, search results and chips. */
export const ENTITY_TYPE_INFO: Readonly<Record<EntityType, EntityTypeInfo>> = {
  domain: { label: "Domain", color: "#6ea8fe" },
  ip_address: { label: "IP address", color: "#ff9f6e" },
  netblock: { label: "Netblock", color: "#b794f6" },
  asn: { label: "ASN", color: "#ff6b81" },
  certificate: { label: "Certificate", color: "#5fd39b" },
  url: { label: "URL", color: "#a3b8cc" },
  service: { label: "Port / Service", color: "#c3e88d" },
  technology: { label: "Technology", color: "#e9a8f2" },
  organization: { label: "Organization", color: "#e5e9f0" },
  email: { label: "Email", color: "#8bd5ca" },
  username: { label: "Username", color: "#91d7e3" },
  person: { label: "Person", color: "#f7a8b8" },
  phone: { label: "Phone", color: "#eed49f" },
  social_profile: { label: "Social profile", color: "#7dc4e4" },
  location: { label: "Location", color: "#a6da95" },
  crypto_wallet: { label: "Crypto wallet", color: "#f5a97f" },
  document: { label: "Document", color: "#b8c0e0" },
  image: { label: "Image", color: "#c6a0f6" },
  breach: { label: "Breach", color: "#ed8796" },
};

/** Provenance carried by every node and edge (see docs/ARCHITECTURE.md#provenance). */
export interface Provenance {
  /** Where it came from (several sources are joined with ", "). */
  source: string;
  /** ISO 8601 timestamp of the first observation. */
  first_seen: string;
  /** ISO 8601 timestamp of the most recent observation. */
  last_seen: string;
  /** 0–1 (for case data: the highest confidence seen). */
  confidence: number;
}

/** The full provenance summary the API keeps for case data. */
export interface ProvenanceDetails {
  sources: readonly string[];
  observation_count: number;
  /** Admiralty rating of the most recent observation, e.g. "B2". */
  admiralty: string | null;
  note: string | null;
  evidence_refs: readonly string[];
}

export type PropertyValue = string | number | boolean | readonly string[] | readonly number[];

export interface EntityNode extends Provenance {
  /** Unique id; for case data, the canonical key. */
  id: string;
  label: string;
  type: EntityType;
  /** Type-specific details (e.g. an ASN's registered name). */
  properties?: Readonly<Record<string, PropertyValue>>;
  /** Only for case data (the demo fixture has neither). */
  sensitivity?: Sensitivity;
  details?: ProvenanceDetails;
}

export interface RelationEdge extends Provenance {
  id: string;
  /** Id of the node the relationship starts from. */
  from: string;
  /** Id of the node the relationship points to. */
  to: string;
  /** Relationship kind, e.g. `resolves_to` or `RESOLVES_TO`. */
  relation: string;
  details?: ProvenanceDetails;
}

export interface InvestigationGraph {
  nodes: readonly EntityNode[];
  edges: readonly RelationEdge[];
}

export function isEntityType(value: string): value is EntityType {
  return (ENTITY_TYPES as readonly string[]).includes(value);
}

/** `RESOLVES_TO` / `resolves_to` → "resolves to". */
export function relationLabel(relation: string): string {
  return relation.toLowerCase().replaceAll("_", " ");
}
