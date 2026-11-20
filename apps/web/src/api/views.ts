/**
 * Saved views of a case's graph, shared with its members.
 * Mirrors apps/api/src/nightloom_api/views (/cases/{id}/views).
 */
import { apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";

type CallOptions = Pick<ApiRequestOptions, "signal" | "fetchImpl" | "timeoutMs">;

/** Limits from views/schemas.py. */
export const VIEW_LIMITS = {
  nameMax: 80,
  viewsPerCase: 50,
  expandedGroups: 500,
  positions: 10_000,
  hiddenSources: 100,
  sourceMax: 100,
} as const;

export interface ApiCamera {
  /** The graph point at the center of the screen (graph coordinates). */
  x: number;
  y: number;
  /** Graph units per screen pixel. */
  ratio: number;
  /** Radians. */
  angle: number;
}

export type ApiSeen =
  | { kind: "all" }
  | { kind: "preset"; preset: "24h" | "7d" | "30d" }
  | { kind: "range"; from: string | null; to: string | null };

/** Mirrors `Filters`: the web's URL filter state as JSON. */
export interface ApiFilters {
  hidden_types: string[];
  hidden_sources: string[];
  hidden_sensitivities: string[];
  seen: ApiSeen;
  min_confidence: number;
  min_reliability: string | null;
  min_credibility: number | null;
  focus: { key: string; hops: number } | null;
  hide_isolated: boolean;
}

/** Mirrors `ViewState` (version 1). */
export interface ApiViewState {
  version: 1;
  mode: "2d";
  camera: ApiCamera;
  filters: ApiFilters;
  groups: { expanded: string[] };
  positions: Record<string, [number, number]>;
}

export interface ApiAuthor {
  id: string;
  display_name: string;
}

/** Mirrors `ViewSummary`: a view in the list, without its state. */
export interface ViewSummary {
  id: string;
  name: string;
  created_by: ApiAuthor | null;
  updated_by: ApiAuthor | null;
  created_at: string;
  updated_at: string;
}

/** Mirrors `ViewOut`. */
export interface SavedView extends ViewSummary {
  state: ApiViewState;
}

const viewsPath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/views`;
const viewPath = (caseId: string, viewId: string) =>
  `${viewsPath(caseId)}/${encodeURIComponent(viewId)}`;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isAuthor(value: unknown): value is ApiAuthor | null {
  return (
    value === null ||
    (isRecord(value) &&
      typeof value["id"] === "string" &&
      typeof value["display_name"] === "string")
  );
}

export function isViewSummary(value: unknown): value is ViewSummary {
  return (
    isRecord(value) &&
    typeof value["id"] === "string" &&
    typeof value["name"] === "string" &&
    isAuthor(value["created_by"]) &&
    isAuthor(value["updated_by"]) &&
    typeof value["created_at"] === "string" &&
    typeof value["updated_at"] === "string"
  );
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isSeen(value: unknown): value is ApiSeen {
  if (!isRecord(value)) return false;
  switch (value["kind"]) {
    case "all":
      return true;
    case "preset":
      return ["24h", "7d", "30d"].includes(value["preset"] as string);
    case "range":
      return (
        (value["from"] === null || typeof value["from"] === "string") &&
        (value["to"] === null || typeof value["to"] === "string")
      );
    default:
      return false;
  }
}

function isFilters(value: unknown): value is ApiFilters {
  if (!isRecord(value)) return false;
  const focus = value["focus"];
  return (
    isStringList(value["hidden_types"]) &&
    isStringList(value["hidden_sources"]) &&
    isStringList(value["hidden_sensitivities"]) &&
    isSeen(value["seen"]) &&
    isFiniteNumber(value["min_confidence"]) &&
    (value["min_reliability"] === null || typeof value["min_reliability"] === "string") &&
    (value["min_credibility"] === null || isFiniteNumber(value["min_credibility"])) &&
    (focus === null ||
      (isRecord(focus) && typeof focus["key"] === "string" && isFiniteNumber(focus["hops"]))) &&
    typeof value["hide_isolated"] === "boolean"
  );
}

function isPosition(value: unknown): value is [number, number] {
  return Array.isArray(value) && value.length === 2 && value.every(isFiniteNumber);
}

export function isViewState(value: unknown): value is ApiViewState {
  if (!isRecord(value) || value["version"] !== 1 || value["mode"] !== "2d") return false;
  const { camera, groups, positions } = value;
  return (
    isRecord(camera) &&
    ["x", "y", "ratio", "angle"].every((key) => isFiniteNumber(camera[key])) &&
    isFilters(value["filters"]) &&
    isRecord(groups) &&
    isStringList(groups["expanded"]) &&
    isRecord(positions) &&
    Object.values(positions).every(isPosition)
  );
}

export function isSavedView(value: unknown): value is SavedView {
  return isRecord(value) && isViewSummary(value) && isViewState(value["state"]);
}

export async function listViews(caseId: string, options: CallOptions = {}): Promise<ViewSummary[]> {
  const body = await apiRequest(viewsPath(caseId), options);
  if (!Array.isArray(body) || !body.every(isViewSummary)) throw new UnexpectedResponseError(200);
  return body;
}

export async function getView(
  caseId: string,
  viewId: string,
  options: CallOptions = {},
): Promise<SavedView> {
  const body = await apiRequest(viewPath(caseId, viewId), options);
  if (!isSavedView(body)) throw new UnexpectedResponseError(200);
  return body;
}

export async function createView(
  caseId: string,
  name: string,
  state: ApiViewState,
  options: CallOptions = {},
): Promise<SavedView> {
  const body = await apiRequest(viewsPath(caseId), {
    ...options,
    method: "POST",
    body: { name, state },
  });
  if (!isSavedView(body)) throw new UnexpectedResponseError(201);
  return body;
}

export async function updateView(
  caseId: string,
  viewId: string,
  change: { name?: string; state?: ApiViewState },
  options: CallOptions = {},
): Promise<SavedView> {
  const body = await apiRequest(viewPath(caseId, viewId), {
    ...options,
    method: "PATCH",
    body: change,
  });
  if (!isSavedView(body)) throw new UnexpectedResponseError(200);
  return body;
}

export async function deleteView(
  caseId: string,
  viewId: string,
  options: CallOptions = {},
): Promise<void> {
  await apiRequest(viewPath(caseId, viewId), { ...options, method: "DELETE" });
}
