/**
 * The caller's personal layout of a case: the entities they pinned, by key.
 * Mirrors apps/api/src/nightloom_api/layouts (GET / PUT /cases/{id}/layout).
 */
import { apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";

export type LayoutPositions = Record<string, [number, number]>;

export interface ApiLayout {
  positions: LayoutPositions;
  updated_at: string | null;
}

type CallOptions = Pick<ApiRequestOptions, "signal" | "fetchImpl" | "timeoutMs">;

/** Browsers refuse keepalive bodies over 64 KiB; larger saves go without it. */
export const KEEPALIVE_MAX_BYTES = 60_000;

const layoutPath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/layout`;

function isPosition(value: unknown): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    value.every((n) => typeof n === "number" && Number.isFinite(n))
  );
}

export function isApiLayout(value: unknown): value is ApiLayout {
  if (typeof value !== "object" || value === null) return false;
  const { positions, updated_at } = value as Record<string, unknown>;
  return (
    typeof positions === "object" &&
    positions !== null &&
    !Array.isArray(positions) &&
    Object.values(positions).every(isPosition) &&
    (updated_at === null || typeof updated_at === "string")
  );
}

export async function getLayout(caseId: string, options: CallOptions = {}): Promise<ApiLayout> {
  const body = await apiRequest(layoutPath(caseId), options);
  if (!isApiLayout(body)) throw new UnexpectedResponseError(200);
  return body;
}

export async function saveLayout(
  caseId: string,
  positions: LayoutPositions,
  { keepalive = false, ...options }: CallOptions & { keepalive?: boolean } = {},
): Promise<void> {
  const body = { positions };
  const fits = !keepalive || JSON.stringify(body).length <= KEEPALIVE_MAX_BYTES;
  await apiRequest(layoutPath(caseId), {
    ...options,
    method: "PUT",
    body,
    keepalive: keepalive && fits,
  });
}
