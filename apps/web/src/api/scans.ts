/**
 * Typed calls for scan profiles (`/scan-profiles`) and profile runs
 * (`/cases/{id}/scan-runs`). Types mirror
 * apps/api/src/nightloom_api/scans/schemas.py.
 *
 * `POST /scan-runs` answers `409` {@link ConflictError} (case not open),
 * `422` (unknown profile, missing entities, nothing to run, too many jobs)
 * and `429` {@link RateLimitedError} (two runs already in progress), each
 * with a user-safe `detail`.
 */
import { apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";
import { isEntityType, type EntityType } from "../domain/entities";

type CallOptions = Pick<ApiRequestOptions, "signal" | "fetchImpl" | "timeoutMs">;

/** Mirrors `ProfileOut`. */
export interface ScanProfile {
  /** A built-in profile's slug, or a custom profile's UUID. */
  id: string;
  name: string;
  description: string;
  /** Shuttle names, in the order they run. */
  shuttles: string[];
  built_in: boolean;
  /** Entity types at least one of its installed Shuttles accepts. */
  input_types: EntityType[];
  /** Its Shuttles that are not installed (left out of runs). */
  missing_shuttles: string[];
  updated_at: string | null;
}

export const RUN_STATUSES = ["running", "finished", "cancelled", "stopped"] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

/** Mirrors `RunOut`. */
export interface ScanRun {
  id: string;
  case_id: string;
  profile_id: string;
  profile_name: string;
  requested_by: string | null;
  status: RunStatus;
  /** Why a stopped run stopped: `case_not_open` or `requester_not_allowed`. */
  stop_reason: string | null;
  entities: number;
  /** Jobs planned, queued so far, and still waiting for a free job slot. */
  total: number;
  released: number;
  waiting: number;
  /** (Shuttle, entity) pairs left out when planned. */
  skipped: number;
  jobs: Record<"queued" | "running" | "succeeded" | "failed" | "cancelled", number>;
  use_cache: boolean;
  /** A pivot also scans the infrastructure entities it finds, within these limits. */
  pivot: boolean;
  max_depth: number;
  max_jobs: number;
  max_entities: number;
  /** New entities a pivot found and planned, and the furthest hop queued so far. */
  discovered: number;
  depth: number;
  /** Which limit stopped a pivot from adding more: "jobs" or "entities". */
  capped: "jobs" | "entities" | null;
  created_at: string;
  finished_at: string | null;
}

/** At most this many entities per run (the API's limit). */
export const MAX_RUN_ENTITIES = 25;
/** A pivot's limits (the API's): hops from the starting entities, jobs, new entities. */
export const PIVOT_MAX_DEPTH = 2;
export const PIVOT_MAX_JOBS = 200;
export const PIVOT_MAX_ENTITIES = 500;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const isString = (value: unknown): value is string => typeof value === "string";
const isCount = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
const isStrings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(isString);

export function isScanProfile(value: unknown): value is ScanProfile {
  return (
    isRecord(value) &&
    isString(value["id"]) &&
    isString(value["name"]) &&
    isString(value["description"]) &&
    isStrings(value["shuttles"]) &&
    typeof value["built_in"] === "boolean" &&
    Array.isArray(value["input_types"]) &&
    value["input_types"].every((type) => isString(type) && isEntityType(type)) &&
    isStrings(value["missing_shuttles"]) &&
    (value["updated_at"] === null || isString(value["updated_at"]))
  );
}

export function isScanRun(value: unknown): value is ScanRun {
  if (!isRecord(value) || !isRecord(value["jobs"])) return false;
  const jobs = value["jobs"];
  return (
    isString(value["id"]) &&
    isString(value["case_id"]) &&
    isString(value["profile_id"]) &&
    isString(value["profile_name"]) &&
    isString(value["status"]) &&
    (RUN_STATUSES as readonly string[]).includes(value["status"]) &&
    (value["stop_reason"] === null || isString(value["stop_reason"])) &&
    [
      "entities",
      "total",
      "released",
      "waiting",
      "skipped",
      "max_depth",
      "max_jobs",
      "max_entities",
      "discovered",
      "depth",
    ].every((key) => isCount(value[key])) &&
    typeof value["pivot"] === "boolean" &&
    (value["capped"] === null || value["capped"] === "jobs" || value["capped"] === "entities") &&
    ["queued", "running", "succeeded", "failed", "cancelled"].every((key) => isCount(jobs[key])) &&
    typeof value["use_cache"] === "boolean" &&
    isString(value["created_at"]) &&
    (value["finished_at"] === null || isString(value["finished_at"]))
  );
}

function expect<T>(value: unknown, check: (value: unknown) => value is T): T {
  if (!check(value)) throw new UnexpectedResponseError(200);
  return value;
}

const runsPath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/scan-runs`;
const profilePath = (id: string) => `/scan-profiles/${encodeURIComponent(id)}`;

/** GET /scan-profiles: built-in profiles first, then the admins'. */
export async function listProfiles(options: CallOptions = {}): Promise<ScanProfile[]> {
  const body = await apiRequest("/scan-profiles", options);
  if (!Array.isArray(body) || !body.every(isScanProfile)) throw new UnexpectedResponseError(200);
  return body;
}

let profileList: Promise<ScanProfile[]> | null = null;

/** {@link listProfiles}, fetched once per page load (again after a failure or a change). */
export function getProfiles(): Promise<ScanProfile[]> {
  if (profileList === null) {
    const request = listProfiles();
    profileList = request;
    request.catch(() => {
      if (profileList === request) profileList = null;
    });
  }
  return profileList;
}

/** Forgets the fetched profiles (after an admin changed them; tests). */
export function resetProfileCache(): void {
  profileList = null;
}

export interface ProfileInput {
  name: string;
  description: string;
  shuttles: string[];
}

/** POST /scan-profiles (admins). */
export async function createProfile(
  input: ProfileInput,
  options: CallOptions = {},
): Promise<ScanProfile> {
  const body = await apiRequest("/scan-profiles", { ...options, method: "POST", body: input });
  resetProfileCache();
  return expect(body, isScanProfile);
}

/** PATCH /scan-profiles/{id} (admins; built-in profiles answer 403). */
export async function updateProfile(
  id: string,
  changes: Partial<ProfileInput>,
  options: CallOptions = {},
): Promise<ScanProfile> {
  const body = await apiRequest(profilePath(id), { ...options, method: "PATCH", body: changes });
  resetProfileCache();
  return expect(body, isScanProfile);
}

/** DELETE /scan-profiles/{id} (admins). */
export async function deleteProfile(id: string, options: CallOptions = {}): Promise<void> {
  await apiRequest(profilePath(id), { ...options, method: "DELETE" });
  resetProfileCache();
}

/** POST /cases/{id}/scan-runs (editors, open case): run a profile on entities. */
export async function createRun(
  caseId: string,
  body: { profile_id: string; entity_keys: string[]; fresh?: boolean; pivot?: boolean },
  options: CallOptions = {},
): Promise<ScanRun> {
  return expect(
    await apiRequest(runsPath(caseId), { ...options, method: "POST", body }),
    isScanRun,
  );
}

/** GET /cases/{id}/scan-runs: the newest runs first. */
export async function listRuns(caseId: string, options: CallOptions = {}): Promise<ScanRun[]> {
  const body = await apiRequest(`${runsPath(caseId)}?limit=20`, options);
  if (!isRecord(body) || !Array.isArray(body["items"]) || !body["items"].every(isScanRun)) {
    throw new UnexpectedResponseError(200);
  }
  return body["items"];
}

/** POST /cases/{id}/scan-runs/{run_id}/cancel (editors; `409` once ended). */
export async function cancelRun(
  caseId: string,
  runId: string,
  options: CallOptions = {},
): Promise<ScanRun> {
  return expect(
    await apiRequest(`${runsPath(caseId)}/${encodeURIComponent(runId)}/cancel`, {
      ...options,
      method: "POST",
    }),
    isScanRun,
  );
}

/** "Quick domain recon on 3 entities": what a run row is called. */
export function runLabel(run: ScanRun): string {
  return `${run.profile_name} on ${String(run.entities)} entit${run.entities === 1 ? "y" : "ies"}`;
}
