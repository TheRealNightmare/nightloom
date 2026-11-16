import { useEffect, useRef, useState, type SubmitEvent } from "react";
import { Link, useNavigate } from "react-router";

import { canCreateCases, createCase, groupMessages, LIMITS } from "../api/cases";
import { ValidationError } from "../api/client";
import { useAuth } from "../auth/context";
import { LegalBasisFields, PersonEntitiesSwitch, PurposeField, RetentionField } from "./CaseFields";
import { describeCaseError } from "./errors";
import { Forbidden } from "./Feedback";
import { FormField } from "./FormField";
import { ETHICS_DOC_URL } from "./labels";
import {
  counterText,
  EMPTY_CASE_FORM,
  length,
  toCaseCreate,
  validateCaseForm,
  withoutErrors,
  type CaseFormErrors,
  type CaseFormValues,
} from "./validation";

/** Which errors go away when a field is edited. */
const ERRORS_CLEARED_BY: Record<keyof CaseFormValues, (keyof CaseFormErrors)[]> = {
  title: ["title"],
  description: ["description"],
  purpose: ["purpose"],
  legalBasis: ["legal_basis", "legal_basis_note"],
  legalBasisNote: ["legal_basis_note"],
  allowPersonEntities: ["person_entities_reason"],
  personEntitiesReason: ["person_entities_reason"],
  retentionDays: ["retention_days"],
  ethicsAcknowledged: ["ethics_acknowledged"],
};

export function CaseCreatePage() {
  const { state } = useAuth();
  const navigate = useNavigate();
  const [values, setValues] = useState<CaseFormValues>(EMPTY_CASE_FORM);
  const [errors, setErrors] = useState<CaseFormErrors>({});
  const [pending, setPending] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // After a failed submit, move focus to the first field with an error.
  useEffect(() => {
    if (attempt === 0) return;
    const invalid = formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]');
    (invalid ?? formRef.current?.querySelector<HTMLElement>(".form-error"))?.focus();
  }, [attempt]);

  if (state.status !== "signed_in" || !canCreateCases(state.user.role)) {
    return (
      <main className="page page--narrow">
        <h1 className="page__title">New case</h1>
        <Forbidden />
      </main>
    );
  }

  const set = <K extends keyof CaseFormValues>(key: K, value: CaseFormValues[K]) => {
    setValues((current) => ({ ...current, [key]: value }));
    // Editing a field clears its error (and the form-level one).
    setErrors((current) => withoutErrors(current, [...ERRORS_CLEARED_BY[key], "_form"]));
  };

  const onSubmit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const found = validateCaseForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setAttempt((n) => n + 1);
      return;
    }
    setPending(true);
    try {
      const created = await createCase(toCaseCreate(values));
      void navigate(`/cases/${created.id}`);
    } catch (caught) {
      if (!mounted.current) return;
      setErrors(
        caught instanceof ValidationError
          ? groupMessages(caught.messages)
          : { _form: describeCaseError(caught) },
      );
      setAttempt((n) => n + 1);
      setPending(false);
    }
  };

  return (
    <main className="page page--narrow">
      <p className="breadcrumb">
        <Link to="/cases">Cases</Link>
      </p>
      <h1 className="page__title">New case</h1>
      <p className="page__intro">
        A case is one investigation. Before anything is collected, state why it exists and what
        makes it lawful.
      </p>
      <form
        ref={formRef}
        className="case-form"
        noValidate
        aria-busy={pending}
        onSubmit={(event) => {
          void onSubmit(event);
        }}
      >
        <FormField label="Title" error={errors.title}>
          {(control) => (
            <input
              {...control}
              name="title"
              className="field__input"
              required
              autoComplete="off"
              value={values.title}
              onChange={(event) => {
                set("title", event.target.value);
              }}
            />
          )}
        </FormField>
        <FormField
          label="Description (optional)"
          error={errors.description}
          extra={
            length(values.description) > 0
              ? counterText(length(values.description), LIMITS.descriptionMax)
              : undefined
          }
        >
          {(control) => (
            <textarea
              {...control}
              name="description"
              className="field__input field__textarea"
              rows={2}
              value={values.description}
              onChange={(event) => {
                set("description", event.target.value);
              }}
            />
          )}
        </FormField>
        <PurposeField
          value={values.purpose}
          onChange={(value) => {
            set("purpose", value);
          }}
          error={errors.purpose}
        />
        <LegalBasisFields
          basis={values.legalBasis}
          note={values.legalBasisNote}
          onBasisChange={(value) => {
            set("legalBasis", value);
          }}
          onNoteChange={(value) => {
            set("legalBasisNote", value);
          }}
          basisError={errors.legal_basis}
          noteError={errors.legal_basis_note}
        />
        <PersonEntitiesSwitch
          checked={values.allowPersonEntities}
          onChange={(value) => {
            set("allowPersonEntities", value);
          }}
        />
        {values.allowPersonEntities && (
          <FormField
            label="Why does this case need person entities?"
            extra={counterText(
              length(values.personEntitiesReason),
              LIMITS.reasonMax,
              LIMITS.personReasonMin,
            )}
            error={errors.person_entities_reason}
          >
            {(control) => (
              <textarea
                {...control}
                name="person_entities_reason"
                className="field__input field__textarea"
                rows={3}
                required
                value={values.personEntitiesReason}
                onChange={(event) => {
                  set("personEntitiesReason", event.target.value);
                }}
              />
            )}
          </FormField>
        )}
        <RetentionField
          value={values.retentionDays}
          onChange={(value) => {
            set("retentionDays", value);
          }}
          error={errors.retention_days}
        />
        <div className="field">
          <label className="checkbox">
            <input
              type="checkbox"
              name="ethics_acknowledged"
              required
              checked={values.ethicsAcknowledged}
              aria-invalid={errors.ethics_acknowledged ? true : undefined}
              aria-describedby={errors.ethics_acknowledged ? "ethics-error" : undefined}
              onChange={(event) => {
                set("ethicsAcknowledged", event.target.checked);
              }}
            />
            <span>
              I confirm this investigation follows the Nightloom{" "}
              <a href={ETHICS_DOC_URL} target="_blank" rel="noreferrer">
                ethics policy
              </a>{" "}
              and applicable law
            </span>
          </label>
          {errors.ethics_acknowledged && (
            <p id="ethics-error" className="field__error">
              {errors.ethics_acknowledged}
            </p>
          )}
        </div>
        <div className="form-error" role="alert" tabIndex={-1}>
          {errors._form}
        </div>
        <div className="form-actions">
          <Link to="/cases" className="button">
            Cancel
          </Link>
          <button type="submit" className="button button--primary" disabled={pending}>
            {pending ? "Creating…" : "Create case"}
          </button>
        </div>
      </form>
    </main>
  );
}
