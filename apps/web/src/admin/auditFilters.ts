/** Pure helpers for the audit log page: filters to API query, verification text. */
import type { AuditAction, AuditQuery, VerifyResult } from "../api/admin";
import type { AuditOutcome } from "../api/cases";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const CASE_ID_INVALID = "Enter a full case ID (a UUID).";
export const DATE_RANGE_INVALID = "The end date is before the start date.";

export const OUTCOME_LABELS: Record<AuditOutcome, string> = {
  success: "Success",
  failure: "Failed",
  denied: "Denied",
};

/** The API's `reason` texts (audit/verify.py), in plainer words. */
const VERIFY_REASONS: Record<string, string> = {
  "prev_hash does not match the previous event's hash": "an event is missing or out of order",
  "hash does not match the event's contents": "an event was changed",
};

export interface AuditFilterValues {
  action: AuditAction | "";
  outcome: AuditOutcome | "";
  actor: string;
  caseId: string;
  /** Whole UTC days, `YYYY-MM-DD`, both inclusive. */
  from: string;
  to: string;
}

export const NO_FILTERS: AuditFilterValues = {
  action: "",
  outcome: "",
  actor: "",
  caseId: "",
  from: "",
  to: "",
};

/** The day after `YYYY-MM-DD`, at midnight UTC: the exclusive end of a range. */
function nextDayStart(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString();
}

/**
 * The API query for the filters, or an error message when they cannot be
 * sent (a partial case ID, an end before the start).
 */
export function toAuditQuery(
  values: AuditFilterValues,
): { query: AuditQuery; error: null } | { query: null; error: string } {
  const caseId = values.caseId.trim();
  if (caseId !== "" && !UUID.test(caseId)) return { query: null, error: CASE_ID_INVALID };
  if (values.from !== "" && values.to !== "" && values.to < values.from) {
    return { query: null, error: DATE_RANGE_INVALID };
  }
  return {
    error: null,
    query: {
      action: values.action === "" ? null : values.action,
      outcome: values.outcome === "" ? null : values.outcome,
      actorUserId: values.actor === "" ? null : values.actor,
      caseId: caseId === "" ? null : caseId.toLowerCase(),
      since: values.from === "" ? null : new Date(`${values.from}T00:00:00Z`).toISOString(),
      until: values.to === "" ? null : nextDayStart(values.to),
    },
  };
}

/** "Chain intact: 1,204 events checked." and its failure counterpart. */
export function verifyMessage(result: VerifyResult): string {
  const checked = `${result.checked.toLocaleString("en-US")} ${result.checked === 1 ? "event" : "events"} checked`;
  if (result.ok) return `Chain intact: ${checked}.`;
  const reason =
    result.reason === null ? "" : ` (${VERIFY_REASONS[result.reason] ?? result.reason})`;
  return `Chain broken at event #${result.first_bad_id ?? "?"}${reason}: ${checked}.`;
}
