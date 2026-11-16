/**
 * Minimal typed client for the Nightloom API.
 *
 * Requests use relative `/api/...` URLs: the Vite dev server and the nginx
 * container both proxy them to the API, so the browser never needs to know
 * where the API runs (and no CORS setup is needed).
 */

export const API_BASE = "/api/v1";
export const DEFAULT_TIMEOUT_MS = 5000;

/** Mirrors `DependencyName` in apps/api/src/nightloom_api/infra/__init__.py. */
export type DependencyName = "postgres" | "neo4j" | "valkey" | "storage";

/** Mirrors `CheckResult` in apps/api/src/nightloom_api/routers/readiness.py. */
export interface CheckResult {
  ok: boolean;
  latency_ms: number;
  error?: string | null;
}

/** Mirrors `ReadyResponse` in apps/api/src/nightloom_api/routers/readiness.py. */
export interface ReadyResponse {
  status: "ready" | "not_ready";
  checks: Partial<Record<DependencyName, CheckResult>>;
}

export interface FailedCheck {
  name: string;
  error: string;
}

/** What the status indicator shows. */
export type Readiness =
  | { kind: "ready"; checks: ReadyResponse["checks"] }
  | { kind: "degraded"; failed: FailedCheck[] }
  | { kind: "offline"; reason: string };

export interface RequestOptions {
  /** Abort the request after this many milliseconds. */
  timeoutMs?: number;
  /** Cancels the request early, e.g. when the component unmounts. */
  signal?: AbortSignal;
  /** Injected in tests; defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
}

export interface ApiResponse<T> {
  status: number;
  body: T;
}

export type ApiErrorKind = "timeout" | "aborted" | "network" | "http" | "invalid_body";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | undefined;

  constructor(message: string, kind: ApiErrorKind, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

/**
 * GET a JSON document. Resolves for every HTTP status whose body is JSON and
 * leaves the status check to the caller (readiness uses 503 bodies);
 * rejects with {@link ApiError} on timeout, abort, network failure or a
 * non-JSON body.
 */
export async function getJson(
  path: string,
  { timeoutMs = DEFAULT_TIMEOUT_MS, signal, fetchImpl = fetch }: RequestOptions = {},
): Promise<ApiResponse<unknown>> {
  if (signal?.aborted) throw new ApiError("Request aborted", "aborted");

  const controller = new AbortController();
  // An object, not a `let`, so the flag set inside the timer callback is not
  // narrowed away by control-flow analysis.
  const timeout = { fired: false };
  const timer = setTimeout(() => {
    timeout.fired = true;
    controller.abort();
  }, timeoutMs);
  const forwardAbort = () => {
    controller.abort();
  };
  signal?.addEventListener("abort", forwardAbort, { once: true });

  try {
    let response: Response;
    try {
      response = await fetchImpl(`${API_BASE}${path}`, {
        method: "GET",
        headers: { Accept: "application/json" },
        signal: controller.signal,
        cache: "no-store",
      });
    } catch (error) {
      if (timeout.fired) throw new ApiError(`No answer within ${timeoutMs} ms`, "timeout");
      if (controller.signal.aborted) throw new ApiError("Request aborted", "aborted");
      throw new ApiError(error instanceof Error ? error.message : "Network error", "network");
    }

    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) {
      throw new ApiError(`Unexpected response (HTTP ${response.status})`, "http", response.status);
    }
    try {
      return { status: response.status, body: (await response.json()) as unknown };
    } catch {
      if (timeout.fired) throw new ApiError(`No answer within ${timeoutMs} ms`, "timeout");
      throw new ApiError("Response is not valid JSON", "invalid_body", response.status);
    }
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCheckResult(value: unknown): value is CheckResult {
  return (
    isRecord(value) &&
    typeof value["ok"] === "boolean" &&
    typeof value["latency_ms"] === "number" &&
    (value["error"] === undefined || value["error"] === null || typeof value["error"] === "string")
  );
}

export function isReadyResponse(value: unknown): value is ReadyResponse {
  if (!isRecord(value)) return false;
  const { status, checks } = value;
  return (
    (status === "ready" || status === "not_ready") &&
    isRecord(checks) &&
    Object.values(checks).every(isCheckResult)
  );
}

/** Turns a `/ready` answer into what the UI shows. */
export function interpretReady(response: ApiResponse<unknown>): Readiness {
  const { status, body } = response;
  if ((status !== 200 && status !== 503) || !isReadyResponse(body)) {
    return { kind: "offline", reason: `Unexpected response from the API (HTTP ${status})` };
  }
  const failed: FailedCheck[] = Object.entries(body.checks)
    .filter(([, check]) => !check.ok)
    .map(([name, check]) => ({ name, error: check.error ?? "failed" }));

  if (status === 200 && body.status === "ready" && failed.length === 0) {
    return { kind: "ready", checks: body.checks };
  }
  return {
    kind: "degraded",
    failed: failed.length > 0 ? failed : [{ name: "api", error: "reported not ready" }],
  };
}

/** GET /api/v1/ready. Never rejects except when `signal` aborts it. */
export async function fetchReadiness(options: RequestOptions = {}): Promise<Readiness> {
  try {
    return interpretReady(await getJson("/ready", options));
  } catch (error) {
    if (error instanceof ApiError && error.kind === "aborted") throw error;
    const reason = error instanceof Error ? error.message : "Unknown error";
    return { kind: "offline", reason };
  }
}
