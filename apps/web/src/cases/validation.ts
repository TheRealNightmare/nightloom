/**
 * Client-side checks that mirror cases/schemas.py, so most mistakes are
 * caught before the request. The server checks everything again.
 */
import { LEGAL_BASES_NEEDING_NOTE, LIMITS, type CaseCreate, type LegalBasis } from "../api/cases";

/** Length in code points after trimming, as the server (Python `len` on a stripped string) counts it. */
export function length(value: string): number {
  return Array.from(value.trim()).length;
}

export interface CaseFormValues {
  title: string;
  description: string;
  purpose: string;
  legalBasis: LegalBasis | "";
  legalBasisNote: string;
  allowPersonEntities: boolean;
  personEntitiesReason: string;
  retentionDays: string;
  ethicsAcknowledged: boolean;
}

export const EMPTY_CASE_FORM: CaseFormValues = {
  title: "",
  description: "",
  purpose: "",
  legalBasis: "",
  legalBasisNote: "",
  allowPersonEntities: false,
  personEntitiesReason: "",
  retentionDays: "",
  ethicsAcknowledged: false,
};

/** Keys are the API's field names, so server messages land on the same fields. */
export type CaseFormErrors = Partial<
  Record<
    | "title"
    | "description"
    | "purpose"
    | "legal_basis"
    | "legal_basis_note"
    | "person_entities_reason"
    | "retention_days"
    | "ethics_acknowledged"
    | "_form",
    string
  >
>;

export function needsNote(basis: LegalBasis | ""): boolean {
  return basis !== "" && LEGAL_BASES_NEEDING_NOTE.has(basis);
}

export function validatePurpose(purpose: string): string | undefined {
  const n = length(purpose);
  if (n < LIMITS.purposeMin) return `Purpose must be at least ${LIMITS.purposeMin} characters.`;
  if (n > LIMITS.purposeMax) return `Purpose must be at most ${LIMITS.purposeMax} characters.`;
  return undefined;
}

export function validateTitle(title: string): string | undefined {
  const n = length(title);
  if (n < LIMITS.titleMin) return `Title must be at least ${LIMITS.titleMin} characters.`;
  if (n > LIMITS.titleMax) return `Title must be at most ${LIMITS.titleMax} characters.`;
  return undefined;
}

export function validateNote(basis: LegalBasis | "", note: string): string | undefined {
  if (needsNote(basis) && length(note) === 0) {
    return basis === "other"
      ? "Explain the legal basis in the note."
      : "Name the engagement and who authorized it.";
  }
  if (length(note) > LIMITS.legalBasisNoteMax) {
    return `The note must be at most ${LIMITS.legalBasisNoteMax} characters.`;
  }
  return undefined;
}

/** Empty is fine (no retention period); otherwise a whole number of days in range. */
export function validateRetention(days: string): string | undefined {
  const value = days.trim();
  if (value === "") return undefined;
  if (!/^\d+$/.test(value)) return "Enter a whole number of days.";
  const n = Number(value);
  if (n < LIMITS.retentionMin || n > LIMITS.retentionMax) {
    return `Retention must be between ${LIMITS.retentionMin} and ${LIMITS.retentionMax} days.`;
  }
  return undefined;
}

export function validatePersonReason(allow: boolean, reason: string): string | undefined {
  if (!allow) return undefined;
  const n = length(reason);
  if (n < LIMITS.personReasonMin) {
    return `Explain why in at least ${LIMITS.personReasonMin} characters.`;
  }
  if (n > LIMITS.reasonMax) return `The reason must be at most ${LIMITS.reasonMax} characters.`;
  return undefined;
}

export function validateChangeReason(reason: string): string | undefined {
  const n = length(reason);
  if (n < LIMITS.changeReasonMin) {
    return `Give a reason of at least ${LIMITS.changeReasonMin} characters.`;
  }
  if (n > LIMITS.reasonMax) return `The reason must be at most ${LIMITS.reasonMax} characters.`;
  return undefined;
}

export function validateCaseForm(values: CaseFormValues): CaseFormErrors {
  const errors: CaseFormErrors = {};
  const set = (key: keyof CaseFormErrors, message: string | undefined) => {
    if (message !== undefined) errors[key] = message;
  };
  set("title", validateTitle(values.title));
  if (length(values.description) > LIMITS.descriptionMax) {
    errors.description = `Description must be at most ${LIMITS.descriptionMax} characters.`;
  }
  set("purpose", validatePurpose(values.purpose));
  if (values.legalBasis === "") errors.legal_basis = "Choose a legal basis.";
  set("legal_basis_note", validateNote(values.legalBasis, values.legalBasisNote));
  set(
    "person_entities_reason",
    validatePersonReason(values.allowPersonEntities, values.personEntitiesReason),
  );
  set("retention_days", validateRetention(values.retentionDays));
  if (!values.ethicsAcknowledged) {
    errors.ethics_acknowledged = "Confirm that this investigation follows the ethics policy.";
  }
  return errors;
}

/** The request body for a form that passed {@link validateCaseForm}. */
export function toCaseCreate(values: CaseFormValues): CaseCreate {
  if (values.legalBasis === "") throw new Error("legal basis missing");
  const note = values.legalBasisNote.trim();
  const description = values.description.trim();
  const retention = values.retentionDays.trim();
  return {
    title: values.title.trim(),
    description: description === "" ? null : description,
    purpose: values.purpose.trim(),
    legal_basis: values.legalBasis,
    legal_basis_note: note === "" ? null : note,
    ethics_acknowledged: values.ethicsAcknowledged,
    allow_person_entities: values.allowPersonEntities,
    person_entities_reason: values.allowPersonEntities ? values.personEntitiesReason.trim() : null,
    retention_days: retention === "" ? null : Number(retention),
  };
}

/** "123 / 2000 characters (at least 30)". */
export function counterText(count: number, max: number, min?: number): string {
  const base = `${count} / ${max} characters`;
  return min !== undefined && count < min ? `${base} (at least ${min})` : base;
}

/** A copy of `errors` without `keys`. */
export function withoutErrors<T extends Partial<Record<string, string>>>(
  errors: T,
  keys: readonly string[],
): T {
  return Object.fromEntries(Object.entries(errors).filter(([key]) => !keys.includes(key))) as T;
}
