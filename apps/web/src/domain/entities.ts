/**
 * Entity and relationship shapes shared by the UI.
 *
 * The entity types are those of `EntityType` in packages/core (in the same
 * order). The rules about them (sensitivity, which relationships connect
 * which types, value hints) are not repeated here: the UI reads them from
 * `GET /api/v1/schema`. Only presentation lives here: a label and a color per
 * type. The colors are design tokens (`--type-*` in src/styles/tokens.css);
 * the WebGL renderer reads their values through src/theme/graphPalette.ts.
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
  "favicon",
  "repository",
] as const;

export type EntityType = (typeof ENTITY_TYPES)[number];

/** Mirrors `Sensitivity` in packages/core. */
export const SENSITIVITIES = ["infrastructure", "organization", "person"] as const;
export type Sensitivity = (typeof SENSITIVITIES)[number];

export interface EntityTypeInfo {
  /** Human-readable name (the same as `display_name` in the schema). */
  label: string;
  /** The name of several, for group nodes ("312 URLs"). */
  plural: string;
  /**
   * The type's color as CSS (`var(--type-…)`), for swatches and chips. Types
   * without a color of their own use --muted.
   */
  color: string;
}

/** The design token of a type's color: `ip_address` → `--type-ip-address`. */
export function typeToken(type: EntityType): string {
  return `--type-${type.replaceAll("_", "-")}`;
}

const colorOf = (type: EntityType) => `var(${typeToken(type)})`;

/** The one palette: graph nodes, legend swatches, search results and chips. */
export const ENTITY_TYPE_INFO: Readonly<Record<EntityType, EntityTypeInfo>> = {
  domain: { label: "Domain", plural: "Domains", color: colorOf("domain") },
  ip_address: { label: "IP address", plural: "IP addresses", color: colorOf("ip_address") },
  netblock: { label: "Netblock", plural: "Netblocks", color: colorOf("netblock") },
  asn: { label: "ASN", plural: "ASNs", color: colorOf("asn") },
  certificate: { label: "Certificate", plural: "Certificates", color: colorOf("certificate") },
  url: { label: "URL", plural: "URLs", color: colorOf("url") },
  service: { label: "Port / Service", plural: "Ports / services", color: colorOf("service") },
  technology: { label: "Technology", plural: "Technologies", color: colorOf("technology") },
  organization: { label: "Organization", plural: "Organizations", color: colorOf("organization") },
  email: { label: "Email", plural: "Emails", color: colorOf("email") },
  username: { label: "Username", plural: "Usernames", color: colorOf("username") },
  person: { label: "Person", plural: "People", color: colorOf("person") },
  phone: { label: "Phone", plural: "Phones", color: colorOf("phone") },
  social_profile: {
    label: "Social profile",
    plural: "Social profiles",
    color: colorOf("social_profile"),
  },
  location: { label: "Location", plural: "Locations", color: colorOf("location") },
  crypto_wallet: {
    label: "Crypto wallet",
    plural: "Crypto wallets",
    color: colorOf("crypto_wallet"),
  },
  document: { label: "Document", plural: "Documents", color: colorOf("document") },
  image: { label: "Image", plural: "Images", color: colorOf("image") },
  breach: { label: "Breach", plural: "Breaches", color: colorOf("breach") },
  favicon: { label: "Favicon", plural: "Favicons", color: colorOf("favicon") },
  repository: { label: "Repository", plural: "Repositories", color: colorOf("repository") },
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
  /** Set only on a group node standing in for many leaves (see graph/groups.ts). */
  group?: EntityGroup;
}

/** Leaves of one type around one entity, drawn as a single node. */
export interface EntityGroup {
  /** The entity every member is connected to. */
  parent: string;
  /** Ids of the entities in the group. */
  members: readonly string[];
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
