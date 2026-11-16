import type { Ref } from "react";

import { LEGAL_BASES, LIMITS, type LegalBasis } from "../api/cases";
import { FormField } from "./FormField";
import { ETHICS_DOC_URL, LEGAL_BASIS_HELP, LEGAL_BASIS_LABELS } from "./labels";
import { counterText, length, needsNote } from "./validation";

export const PURPOSE_HELP =
  "Why does this investigation exist, and what question should it answer? Nightloom requires a stated purpose before any data is collected, so everyone on the case — and anyone reviewing it later — can see why it was run.";

export function PurposeField({
  value,
  onChange,
  error,
  ref,
}: {
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
  ref?: Ref<HTMLTextAreaElement>;
}) {
  return (
    <FormField
      label="Purpose"
      hint={PURPOSE_HELP}
      extra={counterText(length(value), LIMITS.purposeMax, LIMITS.purposeMin)}
      error={error}
    >
      {(control) => (
        <textarea
          {...control}
          ref={ref}
          name="purpose"
          className="field__input field__textarea"
          rows={4}
          required
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
        />
      )}
    </FormField>
  );
}

export function LegalBasisFields({
  basis,
  note,
  onBasisChange,
  onNoteChange,
  basisError,
  noteError,
}: {
  basis: LegalBasis | "";
  note: string;
  onBasisChange: (value: LegalBasis | "") => void;
  onNoteChange: (value: string) => void;
  basisError: string | undefined;
  noteError: string | undefined;
}) {
  const noteRequired = needsNote(basis);
  return (
    <>
      <FormField
        label="Legal basis"
        hint={
          basis === ""
            ? "What makes this investigation lawful. See ETHICS.md."
            : LEGAL_BASIS_HELP[basis]
        }
        error={basisError}
      >
        {(control) => (
          <select
            {...control}
            name="legal_basis"
            className="field__input"
            required
            value={basis}
            onChange={(event) => {
              onBasisChange(event.target.value as LegalBasis | "");
            }}
          >
            <option value="">Choose a legal basis…</option>
            {LEGAL_BASES.map((value) => (
              <option key={value} value={value}>
                {LEGAL_BASIS_LABELS[value]}
              </option>
            ))}
          </select>
        )}
      </FormField>
      {(noteRequired || note !== "" || noteError !== undefined) && (
        <FormField
          label={noteRequired ? "Legal basis note" : "Legal basis note (optional)"}
          hint={
            basis === "authorized_engagement"
              ? "Name the engagement: who authorized it, and its scope or reference."
              : basis === "other"
                ? "Explain the basis that makes this investigation lawful."
                : undefined
          }
          extra={counterText(length(note), LIMITS.legalBasisNoteMax)}
          error={noteError}
        >
          {(control) => (
            <textarea
              {...control}
              name="legal_basis_note"
              className="field__input field__textarea"
              rows={2}
              required={noteRequired}
              value={note}
              onChange={(event) => {
                onNoteChange(event.target.value);
              }}
            />
          )}
        </FormField>
      )}
    </>
  );
}

export function PersonEntitiesSwitch({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="field">
      <label className="switch">
        <input
          type="checkbox"
          role="switch"
          name="allow_person_entities"
          className="switch__input"
          checked={checked}
          onChange={(event) => {
            onChange(event.target.checked);
          }}
        />
        <span className="switch__track" aria-hidden="true" />
        <span className="switch__label">Allow person entities</span>
      </label>
      <p className="field__hint">
        Off by default. Only turn it on when the case genuinely needs data about people.
      </p>
      {checked && (
        <p className="warning" role="note">
          Collecting data about people carries real risk of harm. Person-related Shuttles never run
          during auto-pivot, and your reason is recorded in the case activity. Read the{" "}
          <a href={ETHICS_DOC_URL} target="_blank" rel="noreferrer">
            ethics policy
          </a>{" "}
          before continuing.
        </p>
      )}
    </div>
  );
}

export function RetentionField({
  value,
  onChange,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
}) {
  return (
    <FormField
      label="Retention (days, optional)"
      hint={`How long to keep this case's data, ${LIMITS.retentionMin}–${LIMITS.retentionMax} days. Not enforced automatically yet.`}
      error={error}
    >
      {(control) => (
        <input
          {...control}
          name="retention_days"
          className="field__input field__input--short"
          inputMode="numeric"
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
        />
      )}
    </FormField>
  );
}
