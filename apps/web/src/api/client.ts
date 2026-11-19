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
        credentials: "same-origin",
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

// --- Authenticated requests -------------------------------------------------

/** Names from apps/api/src/nightloom_api/auth/sessions.py. */
export const CSRF_COOKIE = "nightloom_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** Mirrors `Role` in apps/api/src/nightloom_api/auth/models.py. */
export type Role = "admin" | "analyst" | "viewer";

/** Mirrors `UserOut` in apps/api/src/nightloom_api/auth/schemas.py. */
export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  /** Signed in with a temporary password: only a password change is allowed. */
  must_change_password: boolean;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}

/** Mirrors `LoginResponse`. */
export interface LoginResponse {
  user: User;
}

/** One message from a `422` answer; `field` is null when it is not tied to a field. */
export interface FieldMessage {
  field: string | null;
  message: string;
}

/**
 * Base class of every error the authenticated client throws. Messages are
 * written here, never copied from the server, except for validation messages.
 */
export abstract class RequestError extends Error {
  abstract readonly kind:
    | "invalid_credentials"
    | "session_expired"
    | "rate_limited"
    | "validation"
    | "forbidden"
    | "password_change_required"
    | "not_found"
    | "conflict"
    | "too_large"
    | "network"
    | "timeout"
    | "aborted"
    | "unexpected";
}

/** `401` from `POST /auth/login`. */
export class InvalidCredentialsError extends RequestError {
  readonly kind = "invalid_credentials";
  constructor() {
    super("Invalid email or password.");
    this.name = "InvalidCredentialsError";
  }
}

/** `401` from any other request: the session is gone. */
export class SessionExpiredError extends RequestError {
  readonly kind = "session_expired";
  constructor() {
    super("Your session has ended. Please sign in again.");
    this.name = "SessionExpiredError";
  }
}

/**
 * `429`, with the server's `Retry-After` in seconds when it sent a usable one,
 * and its `detail` when it was a string (e.g. the job limit's fixed message).
 */
export class RateLimitedError extends RequestError {
  readonly kind = "rate_limited";
  readonly retryAfterSeconds: number | null;
  readonly detail: string | null;
  constructor(retryAfterSeconds: number | null, detail: string | null = null) {
    super("Too many attempts.");
    this.name = "RateLimitedError";
    this.retryAfterSeconds = retryAfterSeconds;
    this.detail = detail;
  }
}

/** `422`: the server rejected the input; `messages` are safe to show. */
export class ValidationError extends RequestError {
  readonly kind = "validation";
  readonly messages: FieldMessage[];
  constructor(messages: FieldMessage[]) {
    super(messages.map((m) => m.message).join(" ") || "The request was not valid.");
    this.name = "ValidationError";
    this.messages = messages;
  }
}

/**
 * `403`: signed in, but not allowed to do this. `detail` is the server's
 * string `detail`, shown only where the API sends fixed, user-safe reasons
 * (e.g. a person-related Shuttle in a case that does not allow them).
 */
export class ForbiddenError extends RequestError {
  readonly kind = "forbidden";
  readonly detail: string | null;
  constructor(detail: string | null = null) {
    super("You don't have permission to do this.");
    this.name = "ForbiddenError";
    this.detail = detail;
  }
}

/** Mirrors `PASSWORD_CHANGE_REQUIRED_CODE` in apps/api/src/nightloom_api/auth/deps.py. */
export const PASSWORD_CHANGE_REQUIRED_CODE = "password_change_required";

/**
 * `403` with the code `password_change_required`: the user signed in with a
 * temporary password and must choose a new one before anything else.
 */
export class PasswordChangeRequiredError extends RequestError {
  readonly kind = "password_change_required";
  constructor() {
    super("Choose a new password before you continue.");
    this.name = "PasswordChangeRequiredError";
  }
}

/**
 * `404`. For a case this means "no such case, or you are not a member": the
 * server answers the same way for both, and so does the UI.
 */
export class NotFoundError extends RequestError {
  readonly kind = "not_found";
  /** The server's `detail`, when it sent a string (e.g. "No active user with that email."). */
  readonly detail: string | null;
  constructor(detail: string | null = null) {
    super(detail ?? "Not found.");
    this.name = "NotFoundError";
    this.detail = detail;
  }
}

/** `409`: the change conflicts with the current state; `message` is the server's explanation. */
export class ConflictError extends RequestError {
  readonly kind = "conflict";
  constructor(message: string | null) {
    super(message ?? "This change conflicts with the current state. Reload and try again.");
    this.name = "ConflictError";
  }
}

/** `413`: the request asks for more than the server will do; `detail` is its explanation. */
export class ContentTooLargeError extends RequestError {
  readonly kind = "too_large";
  readonly detail: string | null;
  constructor(detail: string | null = null) {
    super(detail ?? "This is too large to do in one request.");
    this.name = "ContentTooLargeError";
    this.detail = detail;
  }
}

/** The request never got an answer (offline, DNS, proxy down, CORS...). */
export class NetworkError extends RequestError {
  readonly kind = "network";
  constructor() {
    super("Cannot reach Nightloom. Check your connection and try again.");
    this.name = "NetworkError";
  }
}

export class RequestTimeoutError extends RequestError {
  readonly kind = "timeout";
  constructor() {
    super("Nightloom did not answer in time. Try again.");
    this.name = "RequestTimeoutError";
  }
}

/** Cancelled by the caller's `signal`; not shown to users. */
export class RequestAbortedError extends RequestError {
  readonly kind = "aborted";
  constructor() {
    super("Request aborted");
    this.name = "RequestAbortedError";
  }
}

/** Anything else: other statuses, non-JSON bodies, bodies of the wrong shape. */
export class UnexpectedResponseError extends RequestError {
  readonly kind = "unexpected";
  readonly status: number | undefined;
  constructor(status?: number) {
    super("Something went wrong. Try again in a moment.");
    this.name = "UnexpectedResponseError";
    this.status = status;
  }
}

/**
 * Reads one cookie from a `document.cookie` string. Returns null when it is
 * missing or empty; a value that is not valid percent-encoding is returned as is.
 */
export function readCookie(name: string, cookies: string): string | null {
  for (const part of cookies.split(";")) {
    const eq = part.indexOf("=");
    if (eq === -1) continue;
    if (part.slice(0, eq).trim() !== name) continue;
    let value = part.slice(eq + 1).trim();
    if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
      value = value.slice(1, -1);
    }
    if (value === "") return null;
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  }
  return null;
}

/**
 * Parses `Retry-After` (delay in seconds or an HTTP date) into whole seconds,
 * at least 1. Returns null when it is missing or unusable.
 */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Math.max(1, Number.parseInt(trimmed, 10));
  // An HTTP date always names the weekday and month; Date.parse alone also
  // accepts bare numbers such as "-5" or "1.5".
  if (!/[a-z]{3}/i.test(trimmed)) return null;
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return null;
  return Math.max(1, Math.ceil((date - now) / 1000));
}

/** The `detail` of an error body when it is a plain string, else null. */
function detailText(body: unknown): string | null {
  return isRecord(body) && typeof body["detail"] === "string" ? body["detail"] : null;
}

/** Pydantic prefixes messages raised from validators with "Value error, ". */
function cleanMessage(message: string): string {
  return message.replace(/^Value error, /, "");
}

function messagesFrom422(body: unknown): FieldMessage[] {
  if (!isRecord(body)) return [];
  const { detail } = body;
  // HTTPException(422, "...") from the password policy.
  if (typeof detail === "string") return [{ field: null, message: detail }];
  // FastAPI request validation: [{loc: ["body", "field"], msg: "..."}].
  if (!Array.isArray(detail)) return [];
  return detail.flatMap((item: unknown): FieldMessage[] => {
    if (!isRecord(item) || typeof item["msg"] !== "string") return [];
    const loc = Array.isArray(item["loc"]) ? (item["loc"] as unknown[]) : [];
    const last = loc.at(-1);
    const field = loc.length > 1 && typeof last === "string" ? last : null;
    return [{ field, message: cleanMessage(item["msg"]) }];
  });
}

type SessionExpiredListener = () => void;
const sessionExpiredListeners = new Set<SessionExpiredListener>();

/**
 * Called whenever a request answers `401` because the session is gone (not
 * for a failed sign-in). Returns an unsubscribe function.
 */
export function onSessionExpired(listener: SessionExpiredListener): () => void {
  sessionExpiredListeners.add(listener);
  return () => {
    sessionExpiredListeners.delete(listener);
  };
}

type PasswordChangeRequiredListener = () => void;
const passwordChangeRequiredListeners = new Set<PasswordChangeRequiredListener>();

/**
 * Called whenever a request answers `403` with the code
 * `password_change_required`. Returns an unsubscribe function.
 */
export function onPasswordChangeRequired(listener: PasswordChangeRequiredListener): () => void {
  passwordChangeRequiredListeners.add(listener);
  return () => {
    passwordChangeRequiredListeners.delete(listener);
  };
}

export interface ApiRequestOptions extends RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** Serialized as JSON. */
  body?: unknown;
  /** The cookie string to read the CSRF token from; defaults to `document.cookie`. */
  cookies?: string;
  /** What a `401` means: a failed sign-in, or an ended session (the default). */
  on401?: "invalid_credentials" | "session_expired";
  /** Tell `onSessionExpired` listeners about a `401` (default true). */
  notifySessionExpired?: boolean;
}

/**
 * Calls the API with the session cookie and, for unsafe methods, the CSRF
 * header. Resolves with the parsed JSON body (undefined for `204`); rejects
 * with a {@link RequestError} subclass for everything else.
 */
export async function apiRequest(
  path: string,
  {
    method = "GET",
    body,
    cookies,
    on401 = "session_expired",
    notifySessionExpired = true,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    signal,
    fetchImpl = fetch,
  }: ApiRequestOptions = {},
): Promise<unknown> {
  if (signal?.aborted) throw new RequestAbortedError();

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(method)) {
    const token = readCookie(CSRF_COOKIE, cookies ?? document.cookie);
    if (token !== null) headers[CSRF_HEADER] = token;
  }

  const controller = new AbortController();
  const timeout = { fired: false };
  const timer = setTimeout(() => {
    timeout.fired = true;
    controller.abort();
  }, timeoutMs);
  const forwardAbort = () => {
    controller.abort();
  };
  signal?.addEventListener("abort", forwardAbort, { once: true });

  const failure = (): RequestError => {
    if (timeout.fired) return new RequestTimeoutError();
    if (controller.signal.aborted) return new RequestAbortedError();
    return new NetworkError();
  };

  try {
    let response: Response;
    try {
      response = await fetchImpl(`${API_BASE}${path}`, {
        method,
        headers,
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch {
      throw failure();
    }

    const { status } = response;
    if (status === 204) return undefined;

    let parsed: unknown = undefined;
    const isJson = (response.headers.get("content-type") ?? "").includes("application/json");
    if (isJson) {
      try {
        parsed = await response.json();
      } catch {
        if (timeout.fired || controller.signal.aborted) throw failure();
        throw new UnexpectedResponseError(status);
      }
    }

    if (status >= 200 && status < 300 && isJson) return parsed;
    if (status === 401) {
      if (on401 === "invalid_credentials") throw new InvalidCredentialsError();
      if (notifySessionExpired) for (const listener of sessionExpiredListeners) listener();
      throw new SessionExpiredError();
    }
    if (status === 429)
      throw new RateLimitedError(
        parseRetryAfter(response.headers.get("retry-after")),
        detailText(parsed),
      );
    if (status === 422) {
      const messages = messagesFrom422(parsed);
      if (messages.length > 0) throw new ValidationError(messages);
    }
    if (status === 403 && isRecord(parsed) && parsed["code"] === PASSWORD_CHANGE_REQUIRED_CODE) {
      for (const listener of passwordChangeRequiredListeners) listener();
      throw new PasswordChangeRequiredError();
    }
    // Only these few statuses carry server text, and only for known, fixed
    // messages (last owner, unknown member email).
    if (status === 403) throw new ForbiddenError(detailText(parsed));
    if (status === 404) throw new NotFoundError(detailText(parsed));
    if (status === 409) throw new ConflictError(detailText(parsed));
    if (status === 413) throw new ContentTooLargeError(detailText(parsed));
    throw new UnexpectedResponseError(status);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

function isRole(value: unknown): value is Role {
  return value === "admin" || value === "analyst" || value === "viewer";
}

export function isUser(value: unknown): value is User {
  return (
    isRecord(value) &&
    typeof value["id"] === "string" &&
    typeof value["email"] === "string" &&
    typeof value["display_name"] === "string" &&
    isRole(value["role"]) &&
    typeof value["is_active"] === "boolean" &&
    typeof value["must_change_password"] === "boolean"
  );
}

function expectUser(value: unknown): User {
  if (!isUser(value)) throw new UnexpectedResponseError(200);
  return value;
}

export type AuthRequestOptions = Pick<
  ApiRequestOptions,
  "timeoutMs" | "signal" | "fetchImpl" | "cookies" | "notifySessionExpired"
>;

/** POST /auth/login. Throws {@link InvalidCredentialsError} for a wrong email or password. */
export async function login(
  email: string,
  password: string,
  options: AuthRequestOptions = {},
): Promise<User> {
  const body = await apiRequest("/auth/login", {
    ...options,
    method: "POST",
    body: { email, password },
    on401: "invalid_credentials",
  });
  if (!isRecord(body)) throw new UnexpectedResponseError(200);
  return expectUser(body["user"]);
}

/** POST /auth/logout (needs the CSRF header). */
export async function logout(options: AuthRequestOptions = {}): Promise<void> {
  await apiRequest("/auth/logout", { ...options, method: "POST" });
}

/** GET /auth/me. Throws {@link SessionExpiredError} when nobody is signed in. */
export async function me(options: AuthRequestOptions = {}): Promise<User> {
  return expectUser(await apiRequest("/auth/me", options));
}

/** The server's `400` for a wrong current password, as a field message. */
export const WRONG_CURRENT_PASSWORD = "Current password is incorrect.";

/**
 * POST /auth/password. A wrong current password (`400`) is reported as a
 * {@link ValidationError} on `current_password`.
 */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
  options: AuthRequestOptions = {},
): Promise<void> {
  try {
    await apiRequest("/auth/password", {
      ...options,
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    });
  } catch (error) {
    if (error instanceof UnexpectedResponseError && error.status === 400) {
      throw new ValidationError([{ field: "current_password", message: WRONG_CURRENT_PASSWORD }]);
    }
    throw error;
  }
}
