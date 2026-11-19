/**
 * Case exports. The download itself is a plain link to
 * `GET /cases/{id}/export` (the session cookie authenticates it, and a GET
 * needs no CSRF token). A link cannot report an error back to the page, so
 * the UI first asks `GET /cases/{id}/export/check`, which runs the same
 * checks (403, 413, 422, 429) without exporting anything. Mirrors
 * apps/api/src/nightloom_api/routers/export.py.
 */
import { API_BASE, apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";

export const EXPORT_FORMATS = ["json", "graphml", "csv"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

/** The API's minimum reason length for exporting person-related data. */
export const EXPORT_REASON_MIN_LENGTH = 10;
export const EXPORT_REASON_MAX_LENGTH = 500;

export interface ExportRequest {
  format: ExportFormat;
  includePersonData: boolean;
  /** Required (at least 10 characters) when including person-related data. */
  reason?: string;
}

/** Mirrors `ExportCheckOut`. */
export interface ExportCheck {
  entities: number;
  person_entities: number;
  relationships: number;
  max_entities: number;
  include_person_data: boolean;
  exports_remaining: number;
}

function exportQuery({ format, includePersonData, reason }: ExportRequest): string {
  const params = new URLSearchParams({ format });
  if (includePersonData) {
    params.set("include_person_data", "true");
    params.set("reason", (reason ?? "").trim());
  }
  return params.toString();
}

const exportPath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/export`;

/** The download link (relative, same origin). */
export function exportUrl(caseId: string, request: ExportRequest): string {
  return `${API_BASE}${exportPath(caseId)}?${exportQuery(request)}`;
}

function isExportCheck(value: unknown): value is ExportCheck {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    ["entities", "person_entities", "relationships", "max_entities", "exports_remaining"].every(
      (key) => typeof record[key] === "number",
    ) && typeof record["include_person_data"] === "boolean"
  );
}

/** GET /cases/{id}/export/check: would this export be allowed? Throws the API's refusal. */
export async function checkExport(
  caseId: string,
  request: ExportRequest,
  options: Pick<ApiRequestOptions, "signal" | "fetchImpl" | "timeoutMs"> = {},
): Promise<ExportCheck> {
  const body = await apiRequest(`${exportPath(caseId)}/check?${exportQuery(request)}`, options);
  if (!isExportCheck(body)) throw new UnexpectedResponseError(200);
  return body;
}

/** Starts a download by following a normal link (a separate function, so tests can replace it). */
export const downloader = {
  start(url: string): void {
    const link = document.createElement("a");
    link.href = url;
    link.rel = "noopener";
    link.style.display = "none";
    document.body.append(link);
    link.click();
    link.remove();
  },
};
