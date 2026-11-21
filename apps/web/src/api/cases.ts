/**
 * Typed calls for cases, their members and their audit events. Types mirror
 * apps/api/src/nightloom_api/cases/schemas.py, cases/models.py,
 * cases/access.py and audit/listing.py.
 *
 * Errors come from {@link apiRequest}: `404` is {@link NotFoundError} (no
 * such case, or not a member), `403` {@link ForbiddenError}, `409`
 * {@link ConflictError} with the server's message, and `422`
 * {@link ValidationError} with per-field messages.
 */
import {
  apiRequest,
  UnexpectedResponseError,
  type ApiRequestOptions,
  type FieldMessage,
  type Role,
} from "./client";

/** Mirrors `LegalBasis`. */
export const LEGAL_BASES = [
  "owned_assets",
  "authorized_engagement",
  "threat_intelligence",
  "journalism",
  "academic_research",
  "self_assessment",
  "other",
] as const;
export type LegalBasis = (typeof LEGAL_BASES)[number];

/** Mirrors `LEGAL_BASES_NEEDING_NOTE`. */
export const LEGAL_BASES_NEEDING_NOTE: ReadonlySet<LegalBasis> = new Set([
  "authorized_engagement",
  "other",
]);

/** Mirrors `CaseStatus`. */
export const CASE_STATUSES = ["open", "closed", "archived"] as const;
export type CaseStatus = (typeof CASE_STATUSES)[number];

/** Mirrors `CaseRole`. */
export const CASE_ROLES = ["owner", "editor", "viewer"] as const;
export type CaseRole = (typeof CASE_ROLES)[number];

/** Mirrors `Capability` and `CAPABILITIES` in cases/access.py (the API does not send them). */
export type Capability =
  "read" | "edit" | "run_shuttles" | "read_audit" | "manage" | "export_person_data";

export const CAPABILITIES: Record<CaseRole, ReadonlySet<Capability>> = {
  viewer: new Set(["read"]),
  editor: new Set(["read", "edit", "run_shuttles", "read_audit"]),
  owner: new Set(["read", "edit", "run_shuttles", "read_audit", "manage", "export_person_data"]),
};

export function can(role: CaseRole, capability: Capability): boolean {
  return CAPABILITIES[role].has(capability);
}

/** Mirrors `CASE_CREATOR_ROLES`. */
export function canCreateCases(globalRole: Role): boolean {
  return globalRole === "admin" || globalRole === "analyst";
}

/** Length limits from cases/models.py and cases/schemas.py. */
export const LIMITS = {
  titleMin: 3,
  titleMax: 200,
  descriptionMax: 5000,
  purposeMin: 30,
  purposeMax: 2000,
  legalBasisNoteMax: 1000,
  retentionMin: 1,
  retentionMax: 3650,
  personReasonMin: 30,
  changeReasonMin: 10,
  reasonMax: 500,
} as const;

/** Mirrors `CaseOut`. */
export interface Case {
  id: string;
  title: string;
  description: string | null;
  purpose: string;
  legal_basis: LegalBasis;
  legal_basis_note: string | null;
  ethics_ack_version: string;
  ethics_ack_at: string;
  allow_person_entities: boolean;
  /** Whether profile runs may pivot (continue from what they find); owners decide. */
  allow_auto_pivot: boolean;
  status: CaseStatus;
  retention_days: number | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  my_role: CaseRole;
}

/** Mirrors `CasePage`. */
export interface CasePage {
  items: Case[];
  next_cursor: string | null;
}

/** Mirrors `CaseCreate`. */
export interface CaseCreate {
  title: string;
  description?: string | null;
  purpose: string;
  legal_basis: LegalBasis;
  legal_basis_note?: string | null;
  ethics_acknowledged: boolean;
  allow_person_entities?: boolean;
  person_entities_reason?: string | null;
  retention_days?: number | null;
}

/** Mirrors `CaseUpdate`: omitted fields stay unchanged. */
export interface CaseUpdate {
  title?: string;
  description?: string | null;
  purpose?: string;
  legal_basis?: LegalBasis;
  legal_basis_note?: string | null;
  status?: CaseStatus;
  allow_person_entities?: boolean;
  allow_auto_pivot?: boolean;
  retention_days?: number | null;
  change_reason?: string;
}

/** Mirrors `REASON_FIELDS`: changing one of these needs `change_reason`. */
export const REASON_FIELDS: ReadonlySet<keyof CaseUpdate> = new Set([
  "purpose",
  "legal_basis",
  "legal_basis_note",
  "allow_person_entities",
]);

/** Mirrors `MemberOut`. */
export interface Member {
  user_id: string;
  email: string;
  display_name: string;
  global_role: Role;
  is_active: boolean;
  role: CaseRole;
  added_by: string | null;
  added_at: string;
}

/** Mirrors `AuditOutcome`. */
export type AuditOutcome = "success" | "failure" | "denied";

/** Mirrors `AuditEventOut` (without hashes, which the case listing never sends). */
export interface AuditEvent {
  id: number;
  occurred_at: string;
  actor_user_id: string | null;
  actor_label: string | null;
  actor_ip: string | null;
  request_id: string;
  action: string;
  outcome: AuditOutcome;
  target_type: string | null;
  target_id: string | null;
  case_id: string | null;
  details: Record<string, unknown>;
}

/** Mirrors `AuditPage`. */
export interface AuditPage {
  items: AuditEvent[];
  next_before_id: number | null;
}

// --- shape checks -------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const isOneOf =
  <T extends string>(values: readonly T[]) =>
  (value: unknown): value is T =>
    typeof value === "string" && (values as readonly string[]).includes(value);

const isLegalBasis = isOneOf(LEGAL_BASES);
const isStatus = isOneOf(CASE_STATUSES);
const isCaseRole = isOneOf(CASE_ROLES);
const isGlobalRole = isOneOf(["admin", "analyst", "viewer"] as const);
const isOutcome = isOneOf(["success", "failure", "denied"] as const);

const isString = (value: unknown): value is string => typeof value === "string";
const isNullableString = (value: unknown) => value === null || isString(value);

export function isCase(value: unknown): value is Case {
  return (
    isRecord(value) &&
    isString(value["id"]) &&
    isString(value["title"]) &&
    isNullableString(value["description"]) &&
    isString(value["purpose"]) &&
    isLegalBasis(value["legal_basis"]) &&
    isNullableString(value["legal_basis_note"]) &&
    typeof value["allow_person_entities"] === "boolean" &&
    typeof value["allow_auto_pivot"] === "boolean" &&
    isStatus(value["status"]) &&
    (value["retention_days"] === null || typeof value["retention_days"] === "number") &&
    isString(value["updated_at"]) &&
    isCaseRole(value["my_role"])
  );
}

function isMember(value: unknown): value is Member {
  return (
    isRecord(value) &&
    isString(value["user_id"]) &&
    isString(value["email"]) &&
    isString(value["display_name"]) &&
    isGlobalRole(value["global_role"]) &&
    isCaseRole(value["role"])
  );
}

export function isAuditEvent(value: unknown): value is AuditEvent {
  return (
    isRecord(value) &&
    typeof value["id"] === "number" &&
    isString(value["occurred_at"]) &&
    isNullableString(value["actor_user_id"]) &&
    isNullableString(value["actor_label"]) &&
    isString(value["action"]) &&
    isOutcome(value["outcome"]) &&
    isNullableString(value["target_id"]) &&
    isRecord(value["details"])
  );
}

function expect<T>(value: unknown, check: (value: unknown) => value is T): T {
  if (!check(value)) throw new UnexpectedResponseError(200);
  return value;
}

function isCasePage(value: unknown): value is CasePage {
  return (
    isRecord(value) &&
    Array.isArray(value["items"]) &&
    value["items"].every(isCase) &&
    isNullableString(value["next_cursor"])
  );
}

function isAuditPage(value: unknown): value is AuditPage {
  return (
    isRecord(value) &&
    Array.isArray(value["items"]) &&
    value["items"].every(isAuditEvent) &&
    (value["next_before_id"] === null || typeof value["next_before_id"] === "number")
  );
}

const isMemberList = (value: unknown): value is Member[] =>
  Array.isArray(value) && value.every(isMember);

// --- calls --------------------------------------------------------------------------

export type CallOptions = Pick<ApiRequestOptions, "signal" | "fetchImpl" | "timeoutMs">;

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

const casePath = (id: string) => `/cases/${encodeURIComponent(id)}`;

export interface ListCasesParams {
  cursor?: string | null;
  limit?: number;
  status?: CaseStatus | null;
  /** Title search; blank is ignored (the API needs at least one character). */
  q?: string | null;
}

/** GET /cases: the caller's cases, most recently updated first. */
export async function listCases(
  { cursor, limit, status, q }: ListCasesParams = {},
  options: CallOptions = {},
): Promise<CasePage> {
  const path = `/cases${query({ cursor, limit, status, q: q?.trim() })}`;
  return expect(await apiRequest(path, options), isCasePage);
}

/** GET /cases/{id}. */
export async function getCase(id: string, options: CallOptions = {}): Promise<Case> {
  return expect(await apiRequest(casePath(id), options), isCase);
}

/** POST /cases (global admins and analysts). */
export async function createCase(body: CaseCreate, options: CallOptions = {}): Promise<Case> {
  return expect(await apiRequest("/cases", { ...options, method: "POST", body }), isCase);
}

/** PATCH /cases/{id}. */
export async function updateCase(
  id: string,
  body: CaseUpdate,
  options: CallOptions = {},
): Promise<Case> {
  return expect(await apiRequest(casePath(id), { ...options, method: "PATCH", body }), isCase);
}

/** DELETE /cases/{id} with the exact title as confirmation. */
export async function deleteCase(
  id: string,
  confirmTitle: string,
  options: CallOptions = {},
): Promise<void> {
  await apiRequest(casePath(id), {
    ...options,
    method: "DELETE",
    body: { confirm_title: confirmTitle },
  });
}

/** GET /cases/{id}/members (owners first). */
export async function listMembers(id: string, options: CallOptions = {}): Promise<Member[]> {
  return expect(await apiRequest(`${casePath(id)}/members`, options), isMemberList);
}

/** PUT /cases/{id}/members: add a user by email, or change their role. */
export async function putMember(
  id: string,
  email: string,
  role: CaseRole,
  options: CallOptions = {},
): Promise<Member> {
  return expect(
    await apiRequest(`${casePath(id)}/members`, {
      ...options,
      method: "PUT",
      body: { email, role },
    }),
    isMember,
  );
}

/** DELETE /cases/{id}/members/{userId} (owners, or the member themselves). */
export async function removeMember(
  id: string,
  userId: string,
  options: CallOptions = {},
): Promise<void> {
  await apiRequest(`${casePath(id)}/members/${encodeURIComponent(userId)}`, {
    ...options,
    method: "DELETE",
  });
}

export interface CaseAuditParams {
  beforeId?: number | null;
  limit?: number;
}

/** GET /cases/{id}/audit (owners and editors), newest first. */
export async function listCaseAudit(
  id: string,
  { beforeId, limit }: CaseAuditParams = {},
  options: CallOptions = {},
): Promise<AuditPage> {
  const path = `${casePath(id)}/audit${query({ before_id: beforeId, limit })}`;
  return expect(await apiRequest(path, options), isAuditPage);
}

// --- validation messages ------------------------------------------------------------

/**
 * Which form field a `422` message belongs to. Field-level errors carry the
 * field in `loc`; the cross-field checks (`model_validator`) and the
 * service's own checks do not, so those are matched by the field they name.
 */
export function fieldOf(message: FieldMessage): string | null {
  if (message.field !== null) return message.field;
  const text = message.message;
  if (text.includes("ethics_acknowledged")) return "ethics_acknowledged";
  if (text.includes("legal_basis_note")) return "legal_basis_note";
  if (text.includes("person_entities_reason")) return "person_entities_reason";
  if (text.startsWith("change_reason")) return "change_reason";
  return null;
}

/** Groups `422` messages by form field; unmatched ones go under `_form`. */
export function groupMessages(messages: FieldMessage[]): Record<string, string> {
  const grouped: Record<string, string> = {};
  for (const message of messages) {
    const key = fieldOf(message) ?? "_form";
    grouped[key] =
      grouped[key] === undefined ? message.message : `${grouped[key]} ${message.message}`;
  }
  return grouped;
}
