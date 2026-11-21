/**
 * Typed calls for the result cache (cached Shuttle results). Types mirror
 * `ResultCacheOut`, `ShuttleCacheStatsOut` and `ResultCacheCleared` in
 * apps/api/src/nightloom_api/jobs/schemas.py; routes are in
 * apps/api/src/nightloom_api/routers/result_cache.py.
 */
import { apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";

type CallOptions = Pick<ApiRequestOptions, "signal" | "timeoutMs" | "fetchImpl">;

/** Who shares cached results: each case on its own, the whole instance, or nobody. */
export type ResultCacheScope = "case" | "instance" | "off";

export interface ShuttleCacheStats {
  shuttle: string;
  /** The installed Shuttle's cache lifetime; null if it is no longer installed. */
  cache_ttl_seconds: number | null;
  entries: number;
  size_bytes: number;
  /** Times entries were reused. */
  hits: number;
  /** Jobs that succeeded in the last 30 days, and how many came from the cache. */
  jobs: number;
  jobs_from_cache: number;
}

export interface ResultCacheInfo {
  scope: ResultCacheScope;
  max_bytes: number;
  max_entry_bytes: number;
  entries: number;
  size_bytes: number;
  shuttles: ShuttleCacheStats[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const isCount = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;

function isShuttleCacheStats(value: unknown): value is ShuttleCacheStats {
  return (
    isRecord(value) &&
    typeof value["shuttle"] === "string" &&
    (value["cache_ttl_seconds"] === null || isCount(value["cache_ttl_seconds"])) &&
    isCount(value["entries"]) &&
    isCount(value["size_bytes"]) &&
    isCount(value["hits"]) &&
    isCount(value["jobs"]) &&
    isCount(value["jobs_from_cache"])
  );
}

export function isResultCacheInfo(value: unknown): value is ResultCacheInfo {
  return (
    isRecord(value) &&
    (value["scope"] === "case" || value["scope"] === "instance" || value["scope"] === "off") &&
    isCount(value["max_bytes"]) &&
    isCount(value["max_entry_bytes"]) &&
    isCount(value["entries"]) &&
    isCount(value["size_bytes"]) &&
    Array.isArray(value["shuttles"]) &&
    value["shuttles"].every(isShuttleCacheStats)
  );
}

function deletedCount(body: unknown): number {
  if (!isRecord(body) || !isCount(body["deleted"])) throw new UnexpectedResponseError(200);
  return body["deleted"];
}

/** GET /admin/result-cache (admins): settings, and per Shuttle what is cached. */
export async function getResultCache(options: CallOptions = {}): Promise<ResultCacheInfo> {
  const body = await apiRequest("/admin/result-cache", options);
  if (!isResultCacheInfo(body)) throw new UnexpectedResponseError(200);
  return body;
}

/** DELETE /admin/result-cache[?shuttle=]: clears everything, or one Shuttle's. Returns how many. */
export async function clearResultCache(
  shuttle: string | null = null,
  options: CallOptions = {},
): Promise<number> {
  const query = shuttle === null ? "" : `?shuttle=${encodeURIComponent(shuttle)}`;
  return deletedCount(
    await apiRequest(`/admin/result-cache${query}`, { ...options, method: "DELETE" }),
  );
}

/** DELETE /cases/{id}/result-cache (case owners): clears the case's cached results. */
export async function clearCaseResultCache(
  caseId: string,
  options: CallOptions = {},
): Promise<number> {
  return deletedCount(
    await apiRequest(`/cases/${encodeURIComponent(caseId)}/result-cache`, {
      ...options,
      method: "DELETE",
    }),
  );
}

/** "0 B", "980 B", "12.3 KB", "4.5 MB", "1.2 GB" (powers of 1024). */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit] ?? "TB"}`;
}
