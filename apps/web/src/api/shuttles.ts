/**
 * Typed calls for Shuttles and jobs. Types mirror
 * apps/api/src/nightloom_api/jobs/schemas.py and jobs/models.py.
 *
 * Errors come from {@link apiRequest}. For `POST /jobs`: `403`
 * {@link ForbiddenError} with the server's reason in `detail` (an active or
 * person-related Shuttle the case does not allow), `409` {@link ConflictError}
 * (case not open), `422` {@link ValidationError}, `429` {@link RateLimitedError}
 * with the job limit's message in `detail`.
 */
import { apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";
import { isEntityType, SENSITIVITIES, type EntityType, type Sensitivity } from "../domain/entities";

/** Mirrors `ShuttleOut`. */
export interface ShuttleInfo {
  name: string;
  version: string;
  title: string;
  description: string;
  input_types: EntityType[];
  output_entity_types: EntityType[];
  output_relationship_types: string[];
  mode: "passive" | "active";
  sensitivity: Sensitivity;
  network: boolean;
  required_keys: string[];
  rate_limit: { requests: number; period_seconds: number } | null;
  timeout_seconds: number;
  terms_url: string | null;
  homepage: string | null;
  /**
   * Conditions of use of the source (plain text, at most 300 characters),
   * e.g. a non-commercial licence; shown before a run.
   */
  usage_note: string | null;
  default_admiralty: string;
  default_confidence: number;
}

/** Mirrors `JobStatus`. */
export const JOB_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export const ACTIVE_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(["queued", "running"]);

export function isActive(job: Job): boolean {
  return ACTIVE_JOB_STATUSES.has(job.status);
}

/** Mirrors `JobStats`. */
export interface JobStats {
  entities_created: number;
  entities_updated: number;
  relationships_created: number;
  relationships_updated: number;
  findings_rejected: number;
  rejected_reasons: Record<string, number>;
  /** Keys of the entities the job created or updated (at most 200). */
  entities_touched: string[];
  /**
   * Short remarks from the Shuttle (at most 10), e.g. that a result was capped.
   * Missing on jobs stored before notes existed.
   */
  notes?: string[];
}

/** Mirrors `JobOut`. */
export interface Job {
  id: string;
  case_id: string;
  shuttle_name: string;
  shuttle_version: string;
  input_key: string;
  requested_by: string | null;
  status: JobStatus;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** One of the API's fixed, user-safe messages; never raw error text. */
  error: string | null;
  stats: JobStats;
}

/** Mirrors `JobPage`. */
export interface JobPage {
  items: Job[];
  next_cursor: string | null;
}

// --- shape checks -------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const isString = (value: unknown): value is string => typeof value === "string";
const isNullableString = (value: unknown): value is string | null =>
  value === null || isString(value);
const isNumber = (value: unknown): value is number => typeof value === "number";
const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(isString);

export function isShuttleInfo(value: unknown): value is ShuttleInfo {
  return (
    isRecord(value) &&
    isString(value["name"]) &&
    isString(value["version"]) &&
    isString(value["title"]) &&
    isString(value["description"]) &&
    Array.isArray(value["input_types"]) &&
    value["input_types"].every((type) => isString(type) && isEntityType(type)) &&
    (value["mode"] === "passive" || value["mode"] === "active") &&
    isString(value["sensitivity"]) &&
    (SENSITIVITIES as readonly string[]).includes(value["sensitivity"]) &&
    typeof value["network"] === "boolean" &&
    isNullableString(value["terms_url"]) &&
    isNullableString(value["homepage"]) &&
    isNullableString(value["usage_note"])
  );
}

function isJobStats(value: unknown): value is JobStats {
  return (
    isRecord(value) &&
    isNumber(value["entities_created"]) &&
    isNumber(value["entities_updated"]) &&
    isNumber(value["relationships_created"]) &&
    isNumber(value["relationships_updated"]) &&
    isNumber(value["findings_rejected"]) &&
    isRecord(value["rejected_reasons"]) &&
    isStringArray(value["entities_touched"]) &&
    (value["notes"] === undefined || isStringArray(value["notes"]))
  );
}

export function isJob(value: unknown): value is Job {
  return (
    isRecord(value) &&
    isString(value["id"]) &&
    isString(value["case_id"]) &&
    isString(value["shuttle_name"]) &&
    isString(value["shuttle_version"]) &&
    isString(value["input_key"]) &&
    isString(value["status"]) &&
    (JOB_STATUSES as readonly string[]).includes(value["status"]) &&
    typeof value["cancel_requested"] === "boolean" &&
    isString(value["created_at"]) &&
    isNullableString(value["started_at"]) &&
    isNullableString(value["finished_at"]) &&
    isNullableString(value["error"]) &&
    isJobStats(value["stats"])
  );
}

function isJobPage(value: unknown): value is JobPage {
  return (
    isRecord(value) &&
    Array.isArray(value["items"]) &&
    value["items"].every(isJob) &&
    isNullableString(value["next_cursor"])
  );
}

function expect<T>(value: unknown, check: (value: unknown) => value is T): T {
  if (!check(value)) throw new UnexpectedResponseError(200);
  return value;
}

// --- calls --------------------------------------------------------------------------

export type CallOptions = Pick<ApiRequestOptions, "signal" | "fetchImpl" | "timeoutMs">;

const jobsPath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/jobs`;

/** How many jobs the history loads per page. */
export const JOB_PAGE_SIZE = 20;

/** GET /shuttles?input_type=…: installed Shuttles that accept the type, by name. */
export async function listShuttles(
  inputType: EntityType,
  options: CallOptions = {},
): Promise<ShuttleInfo[]> {
  const body = await apiRequest(`/shuttles?input_type=${encodeURIComponent(inputType)}`, options);
  if (!Array.isArray(body) || !body.every(isShuttleInfo)) throw new UnexpectedResponseError(200);
  return body;
}

// Installed Shuttles change only with a deployment: each type's list is
// fetched once per page load; a failed fetch is retried on the next call.
const shuttleLists = new Map<EntityType, Promise<ShuttleInfo[]>>();

/** {@link listShuttles}, cached per entity type. */
export function getShuttlesFor(inputType: EntityType): Promise<ShuttleInfo[]> {
  let pending = shuttleLists.get(inputType);
  if (pending === undefined) {
    const request = listShuttles(inputType);
    shuttleLists.set(inputType, request);
    request.catch(() => {
      if (shuttleLists.get(inputType) === request) shuttleLists.delete(inputType);
    });
    pending = request;
  }
  return pending;
}

/** Forgets the fetched Shuttle lists (tests). */
export function resetShuttleCache(): void {
  shuttleLists.clear();
}

/** POST /cases/{id}/jobs (editors, open case): queue a Shuttle run on an entity. */
export async function createJob(
  caseId: string,
  body: { shuttle: string; entity_key: string },
  options: CallOptions = {},
): Promise<Job> {
  return expect(await apiRequest(jobsPath(caseId), { ...options, method: "POST", body }), isJob);
}

/** GET /cases/{id}/jobs/{job_id}. */
export async function getJob(caseId: string, jobId: string, options: CallOptions = {}) {
  return expect(
    await apiRequest(`${jobsPath(caseId)}/${encodeURIComponent(jobId)}`, options),
    isJob,
  );
}

/** GET /cases/{id}/jobs: newest first; pass `next_cursor` back as `cursor`. */
export async function listJobs(
  caseId: string,
  { cursor = null, limit = JOB_PAGE_SIZE }: { cursor?: string | null; limit?: number } = {},
  options: CallOptions = {},
): Promise<JobPage> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor !== null) query.set("cursor", cursor);
  return expect(await apiRequest(`${jobsPath(caseId)}?${query.toString()}`, options), isJobPage);
}

/** POST /cases/{id}/jobs/{job_id}/cancel (editors; `409` once finished). */
export async function cancelJob(
  caseId: string,
  jobId: string,
  options: CallOptions = {},
): Promise<Job> {
  return expect(
    await apiRequest(`${jobsPath(caseId)}/${encodeURIComponent(jobId)}/cancel`, {
      ...options,
      method: "POST",
    }),
    isJob,
  );
}
