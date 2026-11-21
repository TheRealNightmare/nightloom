/**
 * Typed calls for admins: user management (`/users`) and the audit log
 * (`/audit`). Types mirror apps/api/src/nightloom_api/auth/schemas.py,
 * routers/users.py, routers/audit.py and audit/listing.py.
 *
 * Responses that carry a temporary password are returned to the caller and
 * never cached or logged here; the caller shows them once and drops them.
 */
import { isAuditEvent, type AuditEvent, type AuditOutcome, type AuditPage } from "./cases";
import {
  apiRequest,
  isUser,
  UnexpectedResponseError,
  type ApiRequestOptions,
  type Role,
  type User,
} from "./client";

type CallOptions = Pick<ApiRequestOptions, "signal" | "timeoutMs" | "fetchImpl" | "cookies">;

/** Every action the audit log knows (mirrors `AuditAction`). */
export const AUDIT_ACTIONS = [
  "auth.login",
  "auth.logout",
  "auth.password_change",
  "auth.rate_limited",
  "user.create",
  "user.update",
  "user.sessions_revoke",
  "user.password_reset",
  "authz.denied",
  "audit.verify",
  "case.create",
  "case.update",
  "case.status_change",
  "case.delete",
  "case.member_add",
  "case.member_update",
  "case.member_remove",
  "case.graph_delete",
  "case.export",
  "entity.create",
  "entity.delete",
  "relationship.create",
  "view.create",
  "view.update",
  "view.delete",
  "job.create",
  "job.cancel",
  "job.finish",
  "result_cache.clear",
] as const;
export type AuditAction = (typeof AUDIT_ACTIONS)[number];

export const AUDIT_OUTCOMES: readonly AuditOutcome[] = ["success", "failure", "denied"];

/** Body of `POST /users` when the server generates the password. */
export interface NewUser {
  email: string;
  display_name: string;
  role: Role;
}

/** `POST /users` answer: the user, and the temporary password to show once. */
export interface CreatedUser {
  user: User;
  temporaryPassword: string | null;
}

/** `POST /users/{id}/password-reset` answer. */
export interface PasswordReset {
  user: User;
  temporaryPassword: string;
  revokedSessions: number;
}

/** Mirrors `UserUpdate`: omitted fields stay unchanged. */
export interface UserChanges {
  display_name?: string;
  role?: Role;
  is_active?: boolean;
}

/** Mirrors `VerifyOut`. */
export interface VerifyResult {
  ok: boolean;
  checked: number;
  first_bad_id: number | null;
  reason: string | null;
}

export interface AuditQuery {
  beforeId?: number | null;
  limit?: number;
  action?: AuditAction | null;
  outcome?: AuditOutcome | null;
  actorUserId?: string | null;
  caseId?: string | null;
  /** Inclusive, ISO 8601 with a time zone. */
  since?: string | null;
  /** Exclusive, ISO 8601 with a time zone. */
  until?: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function expect<T>(value: unknown, check: (value: unknown) => value is T): T {
  if (!check(value)) throw new UnexpectedResponseError(200);
  return value;
}

const isUserList = (value: unknown): value is User[] => Array.isArray(value) && value.every(isUser);

function isAdminAuditPage(value: unknown): value is AuditPage {
  return (
    isRecord(value) &&
    Array.isArray(value["items"]) &&
    value["items"].every(isAuditEvent) &&
    (value["next_before_id"] === null || typeof value["next_before_id"] === "number")
  );
}

function isVerifyResult(value: unknown): value is VerifyResult {
  return (
    isRecord(value) &&
    typeof value["ok"] === "boolean" &&
    typeof value["checked"] === "number" &&
    (value["first_bad_id"] === null || typeof value["first_bad_id"] === "number") &&
    (value["reason"] === null || typeof value["reason"] === "string")
  );
}

/** GET /users: every user, oldest first. */
export async function listUsers(options: CallOptions = {}): Promise<User[]> {
  return expect(await apiRequest("/users", options), isUserList);
}

/**
 * POST /users with a server-generated temporary password; the user must
 * change it at their first sign-in.
 */
export async function createUser(user: NewUser, options: CallOptions = {}): Promise<CreatedUser> {
  const body = await apiRequest("/users", {
    ...options,
    method: "POST",
    body: { ...user, generate_password: true },
  });
  if (!isUser(body)) throw new UnexpectedResponseError(201);
  const { temporary_password: temporary, ...created } = body as User & {
    temporary_password?: unknown;
  };
  if (temporary !== null && typeof temporary !== "string") throw new UnexpectedResponseError(201);
  return { user: created, temporaryPassword: temporary };
}

/** PATCH /users/{id}. `409` (e.g. the last admin) is a {@link ConflictError}. */
export async function updateUser(
  id: string,
  changes: UserChanges,
  options: CallOptions = {},
): Promise<User> {
  return expect(
    await apiRequest(`/users/${encodeURIComponent(id)}`, {
      ...options,
      method: "PATCH",
      body: changes,
    }),
    isUser,
  );
}

/** DELETE /users/{id}/sessions: sign the user out everywhere. */
export async function revokeSessions(id: string, options: CallOptions = {}): Promise<void> {
  await apiRequest(`/users/${encodeURIComponent(id)}/sessions`, { ...options, method: "DELETE" });
}

/**
 * POST /users/{id}/password-reset: a new temporary password, all sessions
 * revoked. Resetting your own password is refused (`400`).
 */
export async function resetPassword(id: string, options: CallOptions = {}): Promise<PasswordReset> {
  const body = await apiRequest(`/users/${encodeURIComponent(id)}/password-reset`, {
    ...options,
    method: "POST",
  });
  if (
    !isRecord(body) ||
    !isUser(body["user"]) ||
    typeof body["temporary_password"] !== "string" ||
    typeof body["revoked_sessions"] !== "number"
  ) {
    throw new UnexpectedResponseError(200);
  }
  return {
    user: body["user"],
    temporaryPassword: body["temporary_password"],
    revokedSessions: body["revoked_sessions"],
  };
}

function auditQuery(params: AuditQuery): string {
  const search = new URLSearchParams();
  const entries: [string, string | number | null | undefined][] = [
    ["before_id", params.beforeId],
    ["limit", params.limit],
    ["action", params.action],
    ["outcome", params.outcome],
    ["actor_user_id", params.actorUserId],
    ["case_id", params.caseId],
    ["since", params.since],
    ["until", params.until],
  ];
  for (const [key, value] of entries) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

/** GET /audit: events newest first, with filters and keyset paging. */
export async function listAudit(
  params: AuditQuery = {},
  options: CallOptions = {},
): Promise<AuditPage> {
  return expect(await apiRequest(`/audit${auditQuery(params)}`, options), isAdminAuditPage);
}

/** POST /audit/verify. Can take a while on a long log, so the timeout is generous. */
export async function verifyAudit(options: CallOptions = {}): Promise<VerifyResult> {
  return expect(
    await apiRequest("/audit/verify", { timeoutMs: 120_000, ...options, method: "POST" }),
    isVerifyResult,
  );
}

export type { AuditEvent };
