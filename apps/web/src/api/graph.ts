/**
 * Typed calls for a case's graph and for the entity schema. Types mirror
 * apps/api/src/nightloom_api/graph/schemas.py and routers/schema.py.
 *
 * The rules (sensitivity, which relationship connects which types, value
 * hints) come from `GET /schema`, generated from packages/core, so the UI
 * never keeps its own copy of them.
 */
import { apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";
import {
  isEntityType,
  SENSITIVITIES,
  type EntityType,
  type PropertyValue,
  type Sensitivity,
} from "../domain/entities";

/** Mirrors `ProvenanceOut`. */
export interface ApiProvenance {
  first_seen: string;
  last_seen: string;
  observation_count: number;
  sources: string[];
  max_confidence: number;
  admiralty: string | null;
  evidence_refs: string[];
  note: string | null;
}

/** Mirrors `EntityOut`. */
export interface ApiEntity {
  key: string;
  type: EntityType;
  value: string;
  sensitivity: Sensitivity;
  properties: Record<string, PropertyValue>;
  provenance: ApiProvenance;
}

/** Mirrors `EntityDetailOut`. */
export interface ApiEntityDetail extends ApiEntity {
  neighbours: number;
}

/** Mirrors `RelationshipOut`. */
export interface ApiRelationship {
  id: string;
  type: string;
  source_key: string;
  target_key: string;
  provenance: ApiProvenance;
}

/** Mirrors `GraphOut`. */
export interface ApiGraph {
  nodes: ApiEntity[];
  edges: ApiRelationship[];
  truncated: boolean;
}

/** Mirrors `EntityTypeOut`. */
export interface SchemaEntityType {
  type: EntityType;
  label: string;
  display_name: string;
  sensitivity: Sensitivity;
  hint: string;
  example: string;
}

/** Mirrors `RelationshipTypeOut`. */
export interface SchemaRelationshipType {
  type: string;
  note_required: boolean;
  pairs: [EntityType, EntityType][];
}

export interface Grade {
  code: string;
  label: string;
}

/** Mirrors `SchemaOut`. */
export interface Schema {
  entity_types: SchemaEntityType[];
  relationship_types: SchemaRelationshipType[];
  admiralty: { reliability: Grade[]; credibility: Grade[] };
  manual_defaults: {
    source: string;
    reliability: string;
    credibility: number;
    admiralty: string;
    confidence: number;
    note_max_length: number;
  };
}

/** Mirrors `EntityCreate`. */
export interface EntityCreate {
  type: EntityType;
  value: string;
  note?: string | null;
  phone_region?: string | null;
}

/** Mirrors `RelationshipCreate`. */
export interface RelationshipCreate {
  type: string;
  source_key: string;
  target_key: string;
  note?: string | null;
}

/** The API's default and maximum `limit` for `GET /graph` (routers/graph.py). */
export const DEFAULT_GRAPH_LIMIT = 2000;

// --- shape checks -------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const isString = (value: unknown): value is string => typeof value === "string";
const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(isString);
const isSensitivity = (value: unknown): value is Sensitivity =>
  isString(value) && (SENSITIVITIES as readonly string[]).includes(value);
const isType = (value: unknown): value is EntityType => isString(value) && isEntityType(value);

function isProvenance(value: unknown): value is ApiProvenance {
  return (
    isRecord(value) &&
    isString(value["first_seen"]) &&
    isString(value["last_seen"]) &&
    typeof value["observation_count"] === "number" &&
    isStringArray(value["sources"]) &&
    typeof value["max_confidence"] === "number" &&
    (value["admiralty"] === null || isString(value["admiralty"])) &&
    isStringArray(value["evidence_refs"]) &&
    (value["note"] === null || isString(value["note"]))
  );
}

export function isApiEntity(value: unknown): value is ApiEntity {
  return (
    isRecord(value) &&
    isString(value["key"]) &&
    isType(value["type"]) &&
    isString(value["value"]) &&
    isSensitivity(value["sensitivity"]) &&
    isRecord(value["properties"]) &&
    isProvenance(value["provenance"])
  );
}

function isEntityDetail(value: unknown): value is ApiEntityDetail {
  return (
    isApiEntity(value) &&
    typeof (value as unknown as Record<string, unknown>)["neighbours"] === "number"
  );
}

export function isApiRelationship(value: unknown): value is ApiRelationship {
  return (
    isRecord(value) &&
    isString(value["id"]) &&
    isString(value["type"]) &&
    isString(value["source_key"]) &&
    isString(value["target_key"]) &&
    isProvenance(value["provenance"])
  );
}

function isApiGraph(value: unknown): value is ApiGraph {
  return (
    isRecord(value) &&
    Array.isArray(value["nodes"]) &&
    value["nodes"].every(isApiEntity) &&
    Array.isArray(value["edges"]) &&
    value["edges"].every(isApiRelationship) &&
    typeof value["truncated"] === "boolean"
  );
}

function isGrades(value: unknown): value is Grade[] {
  return (
    Array.isArray(value) &&
    value.every((grade) => isRecord(grade) && isString(grade["code"]) && isString(grade["label"]))
  );
}

export function isSchema(value: unknown): value is Schema {
  if (!isRecord(value)) return false;
  const { entity_types, relationship_types, admiralty, manual_defaults } = value;
  return (
    Array.isArray(entity_types) &&
    entity_types.every(
      (item) =>
        isRecord(item) &&
        isType(item["type"]) &&
        isString(item["display_name"]) &&
        isSensitivity(item["sensitivity"]) &&
        isString(item["hint"]) &&
        isString(item["example"]),
    ) &&
    Array.isArray(relationship_types) &&
    relationship_types.every(
      (item) =>
        isRecord(item) &&
        isString(item["type"]) &&
        typeof item["note_required"] === "boolean" &&
        Array.isArray(item["pairs"]) &&
        item["pairs"].every(
          (pair) => Array.isArray(pair) && pair.length === 2 && isType(pair[0]) && isType(pair[1]),
        ),
    ) &&
    isRecord(admiralty) &&
    isGrades(admiralty["reliability"]) &&
    isGrades(admiralty["credibility"]) &&
    isRecord(manual_defaults) &&
    isString(manual_defaults["admiralty"]) &&
    typeof manual_defaults["note_max_length"] === "number"
  );
}

function expect<T>(value: unknown, check: (value: unknown) => value is T): T {
  if (!check(value)) throw new UnexpectedResponseError(200);
  return value;
}

// --- calls --------------------------------------------------------------------------

export type CallOptions = Pick<ApiRequestOptions, "signal" | "fetchImpl" | "timeoutMs">;

const casePath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}`;
/** Keys can contain `/` (URLs, netblocks): encode everything. */
const entityPath = (caseId: string, key: string) =>
  `${casePath(caseId)}/entities/${encodeURIComponent(key)}`;

/** GET /cases/{id}/graph: up to `limit` nodes (by key) and the edges between them. */
export async function getCaseGraph(
  caseId: string,
  { limit = DEFAULT_GRAPH_LIMIT }: { limit?: number } = {},
  options: CallOptions = {},
): Promise<ApiGraph> {
  const path = `${casePath(caseId)}/graph?limit=${String(limit)}`;
  // A large graph can take a moment to serialize.
  return expect(await apiRequest(path, { timeoutMs: 15_000, ...options }), isApiGraph);
}

/** GET /cases/{id}/entities/{key}: the entity and its number of neighbours. */
export async function getEntity(
  caseId: string,
  key: string,
  options: CallOptions = {},
): Promise<ApiEntityDetail> {
  return expect(await apiRequest(entityPath(caseId, key), options), isEntityDetail);
}

/** POST /cases/{id}/entities (editors, open case). Merges into an existing entity. */
export async function createEntity(
  caseId: string,
  body: EntityCreate,
  options: CallOptions = {},
): Promise<ApiEntity> {
  return expect(
    await apiRequest(`${casePath(caseId)}/entities`, { ...options, method: "POST", body }),
    isApiEntity,
  );
}

/** DELETE /cases/{id}/entities/{key} (editors, open case); its relationships go too. */
export async function deleteEntity(
  caseId: string,
  key: string,
  options: CallOptions = {},
): Promise<void> {
  await apiRequest(entityPath(caseId, key), { ...options, method: "DELETE" });
}

/** POST /cases/{id}/relationships (editors, open case). */
export async function createRelationship(
  caseId: string,
  body: RelationshipCreate,
  options: CallOptions = {},
): Promise<ApiRelationship> {
  return expect(
    await apiRequest(`${casePath(caseId)}/relationships`, { ...options, method: "POST", body }),
    isApiRelationship,
  );
}

let schemaPromise: Promise<Schema> | null = null;

/**
 * GET /schema, fetched once per page load (it only changes with a
 * deployment) and shared by every caller; a failed fetch is retried on the
 * next call.
 */
export function getSchema(): Promise<Schema> {
  if (schemaPromise === null) {
    const pending = apiRequest("/schema").then((body) => expect(body, isSchema));
    schemaPromise = pending;
    pending.catch(() => {
      if (schemaPromise === pending) schemaPromise = null;
    });
  }
  return schemaPromise;
}

/** Forgets the fetched schema (tests). */
export function resetSchemaCache(): void {
  schemaPromise = null;
}
