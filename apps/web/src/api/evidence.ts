/**
 * Evidence files (screenshots) and look-alike pages. Routes are in
 * apps/api/src/nightloom_api/routers/evidence.py.
 */
import { API_BASE, apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";

type CallOptions = Pick<ApiRequestOptions, "signal" | "timeoutMs" | "fetchImpl">;

/** An evidence name: the file's SHA-256 and extension. */
const EVIDENCE_NAME = /^[0-9a-f]{64}\.(png|jpg)$/;

export function isEvidenceName(value: unknown): value is string {
  return typeof value === "string" && EVIDENCE_NAME.test(value);
}

/** Where the browser loads a case's evidence file (members only; the session cookie is sent). */
export function evidenceUrl(caseId: string, name: string): string {
  return `${API_BASE}/cases/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(name)}`;
}

/** Mirrors `SimilarItem`. */
export interface SimilarPage {
  key: string;
  type: string;
  value: string;
  /** How many of the hash's 64 bits differ (0: the same picture as far as it can tell). */
  distance: number;
  screenshot: string | null;
}

function isSimilarPage(value: unknown): value is SimilarPage {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item["key"] === "string" &&
    typeof item["type"] === "string" &&
    typeof item["value"] === "string" &&
    typeof item["distance"] === "number" &&
    (item["screenshot"] === null || typeof item["screenshot"] === "string")
  );
}

/** GET /cases/{id}/screenshots/similar?key=… (members): pages that look alike, closest first. */
export async function similarPages(
  caseId: string,
  key: string,
  options: CallOptions = {},
): Promise<SimilarPage[]> {
  const body = await apiRequest(
    `/cases/${encodeURIComponent(caseId)}/screenshots/similar?key=${encodeURIComponent(key)}`,
    options,
  );
  const items = (body as { items?: unknown } | null)?.items;
  if (!Array.isArray(items) || !items.every(isSimilarPage)) {
    throw new UnexpectedResponseError(200);
  }
  return items;
}
