/**
 * Typed calls for ownership verification. Types mirror `VerificationOut`,
 * `ProofOut` and `VerificationList` in
 * apps/api/src/nightloom_api/ownership/schemas.py; routes are in
 * apps/api/src/nightloom_api/routers/ownership.py.
 */
import { apiRequest, UnexpectedResponseError, type ApiRequestOptions } from "./client";

type CallOptions = Pick<ApiRequestOptions, "signal" | "timeoutMs" | "fetchImpl">;

export const VERIFICATION_METHODS = ["dns_txt", "http_file"] as const;
export type VerificationMethod = (typeof VERIFICATION_METHODS)[number];

export const VERIFICATION_STATUSES = [
  "pending",
  "verified",
  "failed",
  "revoked",
  "expired",
] as const;
export type VerificationStatus = (typeof VERIFICATION_STATUSES)[number];

/** Where to publish the proof and what it must say. */
export interface Proof {
  record_type: "TXT" | null;
  record_name: string | null;
  url: string | null;
  value: string;
}

/** Mirrors `VerificationOut`. */
export interface Verification {
  id: string;
  domain: string;
  method: VerificationMethod;
  status: VerificationStatus;
  proof: Proof;
  /** A check is queued and has not reported yet. */
  checking: boolean;
  created_at: string;
  checked_at: string | null;
  verified_at: string | null;
  expires_at: string | null;
  last_error: string | null;
  error_message: string | null;
  /** What the domain resolved to at the last successful check (covered too). */
  addresses: string[];
}

/** At most this many per case (the API's limit). */
export const MAX_VERIFICATIONS = 50;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const isString = (value: unknown): value is string => typeof value === "string";
const isNullableString = (value: unknown): value is string | null =>
  value === null || isString(value);

function isProof(value: unknown): value is Proof {
  return (
    isRecord(value) &&
    (value["record_type"] === null || value["record_type"] === "TXT") &&
    isNullableString(value["record_name"]) &&
    isNullableString(value["url"]) &&
    isString(value["value"])
  );
}

export function isVerification(value: unknown): value is Verification {
  return (
    isRecord(value) &&
    isString(value["id"]) &&
    isString(value["domain"]) &&
    (VERIFICATION_METHODS as readonly unknown[]).includes(value["method"]) &&
    (VERIFICATION_STATUSES as readonly unknown[]).includes(value["status"]) &&
    isProof(value["proof"]) &&
    typeof value["checking"] === "boolean" &&
    isString(value["created_at"]) &&
    ["checked_at", "verified_at", "expires_at", "last_error", "error_message"].every((key) =>
      isNullableString(value[key]),
    ) &&
    Array.isArray(value["addresses"]) &&
    value["addresses"].every(isString)
  );
}

function expectVerification(body: unknown): Verification {
  if (!isVerification(body)) throw new UnexpectedResponseError(200);
  return body;
}

const path = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/verifications`;

/** GET /cases/{id}/verifications (members). */
export async function listVerifications(
  caseId: string,
  options: CallOptions = {},
): Promise<Verification[]> {
  const body = await apiRequest(path(caseId), options);
  if (!isRecord(body) || !Array.isArray(body["items"]) || !body["items"].every(isVerification)) {
    throw new UnexpectedResponseError(200);
  }
  return body["items"];
}

/** POST /cases/{id}/verifications (owners, open case). */
export async function addVerification(
  caseId: string,
  body: { domain: string; method: VerificationMethod },
  options: CallOptions = {},
): Promise<Verification> {
  return expectVerification(await apiRequest(path(caseId), { ...options, method: "POST", body }));
}

/** POST /cases/{id}/verifications/{vid}/check (owners, open case): queue a check. */
export async function checkVerification(
  caseId: string,
  id: string,
  options: CallOptions = {},
): Promise<Verification> {
  return expectVerification(
    await apiRequest(`${path(caseId)}/${encodeURIComponent(id)}/check`, {
      ...options,
      method: "POST",
    }),
  );
}

/** DELETE /cases/{id}/verifications/{vid} (owners). */
export async function removeVerification(
  caseId: string,
  id: string,
  options: CallOptions = {},
): Promise<void> {
  await apiRequest(`${path(caseId)}/${encodeURIComponent(id)}`, { ...options, method: "DELETE" });
}
