/**
 * Typed calls for Certificate Transparency monitoring. Types mirror `WatchOut`,
 * `AlertOut` and `AlertList` in apps/api/src/nightloom_api/monitoring/schemas.py;
 * routes are in apps/api/src/nightloom_api/routers/monitoring.py.
 */
import { apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";

type CallOptions = Pick<ApiRequestOptions, "signal" | "timeoutMs" | "fetchImpl">;

/** Mirrors `WatchOut`. */
export interface CtWatch {
  id: string;
  domain: string;
  created_at: string;
  /** A check is queued or running. */
  checking: boolean;
  checked_at: string | null;
  next_check_at: string;
  /** The first successful check recorded what existed; alerts come after it. */
  baseline_at: string | null;
  last_job_id: string | null;
  problem: string | null;
  problem_message: string | null;
}

/** Mirrors `AlertOut`. */
export interface CtAlert {
  id: string;
  domain: string;
  created_at: string;
  job_id: string | null;
  certificates: number;
  /** The first new names (at most 20); `names_total` counts them all. */
  names: string[];
  names_total: number;
  /** Graph keys of what is new (at most 200). */
  keys: string[];
  acknowledged_at: string | null;
}

export interface CtAlertList {
  items: CtAlert[];
  /** How many are not seen yet (may exceed the items listed). */
  unseen: number;
}

/** At most this many per case (the API's limit). */
export const MAX_CT_WATCHES = 20;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const isString = (value: unknown): value is string => typeof value === "string";
const isNullableString = (value: unknown): value is string | null =>
  value === null || isString(value);
const isStrings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(isString);

export function isCtWatch(value: unknown): value is CtWatch {
  return (
    isRecord(value) &&
    isString(value["id"]) &&
    isString(value["domain"]) &&
    isString(value["created_at"]) &&
    typeof value["checking"] === "boolean" &&
    isString(value["next_check_at"]) &&
    ["checked_at", "baseline_at", "last_job_id", "problem", "problem_message"].every((key) =>
      isNullableString(value[key]),
    )
  );
}

export function isCtAlert(value: unknown): value is CtAlert {
  return (
    isRecord(value) &&
    isString(value["id"]) &&
    isString(value["domain"]) &&
    isString(value["created_at"]) &&
    isNullableString(value["job_id"]) &&
    typeof value["certificates"] === "number" &&
    isStrings(value["names"]) &&
    typeof value["names_total"] === "number" &&
    isStrings(value["keys"]) &&
    isNullableString(value["acknowledged_at"])
  );
}

function expectWatch(body: unknown): CtWatch {
  if (!isCtWatch(body)) throw new UnexpectedResponseError(200);
  return body;
}

const watchesPath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/ct-watches`;
const alertsPath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/ct-alerts`;

/** GET /cases/{id}/ct-watches (members). */
export async function listCtWatches(caseId: string, options: CallOptions = {}): Promise<CtWatch[]> {
  const body = await apiRequest(watchesPath(caseId), options);
  if (!isRecord(body) || !Array.isArray(body["items"]) || !body["items"].every(isCtWatch)) {
    throw new UnexpectedResponseError(200);
  }
  return body["items"];
}

/** POST /cases/{id}/ct-watches (owners, open case; the domain must be in the graph). */
export async function addCtWatch(
  caseId: string,
  domain: string,
  options: CallOptions = {},
): Promise<CtWatch> {
  return expectWatch(
    await apiRequest(watchesPath(caseId), { ...options, method: "POST", body: { domain } }),
  );
}

/** POST /cases/{id}/ct-watches/{wid}/check (owners, open case): check at the next sweep. */
export async function checkCtWatch(
  caseId: string,
  id: string,
  options: CallOptions = {},
): Promise<CtWatch> {
  return expectWatch(
    await apiRequest(`${watchesPath(caseId)}/${encodeURIComponent(id)}/check`, {
      ...options,
      method: "POST",
    }),
  );
}

/** DELETE /cases/{id}/ct-watches/{wid} (owners). */
export async function removeCtWatch(
  caseId: string,
  id: string,
  options: CallOptions = {},
): Promise<void> {
  await apiRequest(`${watchesPath(caseId)}/${encodeURIComponent(id)}`, {
    ...options,
    method: "DELETE",
  });
}

/** GET /cases/{id}/ct-alerts (members); `unseen` lists only alerts nobody marked seen. */
export async function listCtAlerts(
  caseId: string,
  { unseen = false, ...options }: CallOptions & { unseen?: boolean } = {},
): Promise<CtAlertList> {
  const body = await apiRequest(`${alertsPath(caseId)}${unseen ? "?unseen=true" : ""}`, options);
  if (
    !isRecord(body) ||
    !Array.isArray(body["items"]) ||
    !body["items"].every(isCtAlert) ||
    typeof body["unseen"] !== "number"
  ) {
    throw new UnexpectedResponseError(200);
  }
  return { items: body["items"], unseen: body["unseen"] };
}

/** POST /cases/{id}/ct-alerts/acknowledge (editors, owners): the given alerts, or all unseen. */
export async function acknowledgeCtAlerts(
  caseId: string,
  ids?: string[],
  options: CallOptions = {},
): Promise<number> {
  const body = await apiRequest(`${alertsPath(caseId)}/acknowledge`, {
    ...options,
    method: "POST",
    body: ids === undefined ? {} : { ids },
  });
  if (!isRecord(body) || typeof body["acknowledged"] !== "number") {
    throw new UnexpectedResponseError(200);
  }
  return body["acknowledged"];
}
