import { useId, useRef, useState, type SubmitEvent } from "react";
import { useNavigate } from "react-router";

import {
  can,
  deleteCase,
  groupMessages,
  REASON_FIELDS,
  updateCase,
  type Case,
  type CaseStatus,
  type CaseUpdate,
  type LegalBasis,
} from "../api/cases";
import { ValidationError } from "../api/client";
import { clearCaseResultCache } from "../api/resultCache";
import { Modal } from "../components/Modal";
import {
  AutoPivotSwitch,
  LegalBasisFields,
  PersonEntitiesSwitch,
  PurposeField,
  RetentionField,
} from "./CaseFields";
import { useCase } from "./caseContext";
import { VerifiedDomains } from "./VerifiedDomains";
import { ChangeReasonDialog } from "./ChangeReasonDialog";
import { describeCaseError } from "./errors";
import { Forbidden } from "./Feedback";
import { joinWords } from "./labels";
import { validateNote, validatePurpose, validateRetention, withoutErrors } from "./validation";

interface SettingsValues {
  purpose: string;
  legalBasis: LegalBasis | "";
  legalBasisNote: string;
  allowPersonEntities: boolean;
  allowAutoPivot: boolean;
  retentionDays: string;
}

type SettingsErrors = Partial<
  Record<"purpose" | "legal_basis" | "legal_basis_note" | "retention_days" | "_form", string>
>;

const SETTINGS_ERRORS_CLEARED_BY: Record<keyof SettingsValues, (keyof SettingsErrors)[]> = {
  purpose: ["purpose"],
  legalBasis: ["legal_basis", "legal_basis_note"],
  legalBasisNote: ["legal_basis_note"],
  allowPersonEntities: [],
  allowAutoPivot: [],
  retentionDays: ["retention_days"],
};

const FIELD_NAMES: Record<string, string> = {
  purpose: "purpose",
  legal_basis: "legal basis",
  legal_basis_note: "legal basis note",
  allow_person_entities: "person entities setting",
};

function valuesOf(value: Case): SettingsValues {
  return {
    purpose: value.purpose,
    legalBasis: value.legal_basis,
    legalBasisNote: value.legal_basis_note ?? "",
    allowPersonEntities: value.allow_person_entities,
    allowAutoPivot: value.allow_auto_pivot,
    retentionDays: value.retention_days === null ? "" : String(value.retention_days),
  };
}

/** Only the fields whose value differs from the case. */
function settingsChanges(original: Case, values: SettingsValues): CaseUpdate {
  const changes: CaseUpdate = {};
  const purpose = values.purpose.trim();
  if (purpose !== original.purpose) changes.purpose = purpose;
  if (values.legalBasis !== "" && values.legalBasis !== original.legal_basis) {
    changes.legal_basis = values.legalBasis;
  }
  const note = values.legalBasisNote.trim() === "" ? null : values.legalBasisNote.trim();
  if (note !== original.legal_basis_note) changes.legal_basis_note = note;
  if (values.allowPersonEntities !== original.allow_person_entities) {
    changes.allow_person_entities = values.allowPersonEntities;
  }
  if (values.allowAutoPivot !== original.allow_auto_pivot) {
    changes.allow_auto_pivot = values.allowAutoPivot;
  }
  const retention = values.retentionDays.trim() === "" ? null : Number(values.retentionDays);
  if (retention !== original.retention_days) changes.retention_days = retention;
  return changes;
}

/** Owner-only: purpose, legal basis, person entities, auto-pivot, retention, status, deletion. */
export function SettingsTab() {
  const { case: current, setCase } = useCase();
  if (!can(current.my_role, "manage")) return <Forbidden />;
  return (
    <div className="settings">
      <SettingsForm key={current.updated_at} value={current} onSaved={setCase} />
      <StatusActions value={current} onChanged={setCase} />
      <VerifiedDomains value={current} />
      <CachedResults value={current} />
      <DangerZone value={current} />
    </div>
  );
}

function SettingsForm({ value, onSaved }: { value: Case; onSaved: (next: Case) => void }) {
  const [values, setValues] = useState<SettingsValues>(() => valuesOf(value));
  const [errors, setErrors] = useState<SettingsErrors>({});
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [reasonFor, setReasonFor] = useState<{ changes: CaseUpdate; fields: string } | null>(null);
  const saveRef = useRef<HTMLButtonElement>(null);

  const set = <K extends keyof SettingsValues>(key: K, next: SettingsValues[K]) => {
    setValues((currentValues) => ({ ...currentValues, [key]: next }));
    setNotice(null);
    setErrors((current) => withoutErrors(current, [...SETTINGS_ERRORS_CLEARED_BY[key], "_form"]));
  };

  const send = async (changes: CaseUpdate): Promise<string | null> => {
    try {
      const saved = await updateCase(value.id, changes);
      setReasonFor(null);
      onSaved(saved);
      return null;
    } catch (caught) {
      if (caught instanceof ValidationError) {
        const grouped = groupMessages(caught.messages);
        if (grouped["change_reason"] !== undefined) return grouped["change_reason"];
        setErrors(grouped);
      } else {
        setErrors({ _form: describeCaseError(caught) });
      }
      setReasonFor(null);
      return null;
    }
  };

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const found: SettingsErrors = {};
    const purpose = validatePurpose(values.purpose);
    if (purpose !== undefined) found.purpose = purpose;
    const note = validateNote(values.legalBasis, values.legalBasisNote);
    if (note !== undefined) found.legal_basis_note = note;
    const retention = validateRetention(values.retentionDays);
    if (retention !== undefined) found.retention_days = retention;
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    const changes = settingsChanges(value, values);
    if (Object.keys(changes).length === 0) {
      setNotice("Nothing to save.");
      return;
    }
    const needReason = Object.keys(changes).filter((key) =>
      REASON_FIELDS.has(key as keyof CaseUpdate),
    );
    if (needReason.length > 0) {
      setReasonFor({ changes, fields: joinWords(needReason.map((k) => FIELD_NAMES[k] ?? k)) });
      return;
    }
    setPending(true);
    await send(changes);
    setPending(false);
  };

  return (
    <section aria-labelledby="settings-title" className="section">
      <h2 id="settings-title" className="section__title">
        Purpose and legal basis
      </h2>
      <p className="section__hint">
        Changing the purpose, legal basis, its note or the person entities setting asks for a
        reason, which is recorded in the activity.
      </p>
      <form
        noValidate
        className="case-form"
        aria-busy={pending}
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <PurposeField
          value={values.purpose}
          onChange={(next) => {
            set("purpose", next);
          }}
          error={errors.purpose}
        />
        <LegalBasisFields
          basis={values.legalBasis}
          note={values.legalBasisNote}
          onBasisChange={(next) => {
            set("legalBasis", next);
          }}
          onNoteChange={(next) => {
            set("legalBasisNote", next);
          }}
          basisError={errors.legal_basis}
          noteError={errors.legal_basis_note}
        />
        <PersonEntitiesSwitch
          checked={values.allowPersonEntities}
          onChange={(next) => {
            set("allowPersonEntities", next);
          }}
        />
        <AutoPivotSwitch
          checked={values.allowAutoPivot}
          onChange={(next) => {
            set("allowAutoPivot", next);
          }}
        />
        <RetentionField
          value={values.retentionDays}
          onChange={(next) => {
            set("retentionDays", next);
          }}
          error={errors.retention_days}
        />
        <div className="form-error" role="alert">
          {errors._form}
        </div>
        {notice !== null && (
          <p className="section__hint" role="status">
            {notice}
          </p>
        )}
        <div className="form-actions">
          <button ref={saveRef} type="submit" className="button button--primary" disabled={pending}>
            Save changes
          </button>
        </div>
      </form>
      {reasonFor !== null && (
        <ChangeReasonDialog
          fields={reasonFor.fields}
          onSubmit={(reason) => send({ ...reasonFor.changes, change_reason: reason })}
          onCancel={() => {
            setReasonFor(null);
            saveRef.current?.focus();
          }}
        />
      )}
    </section>
  );
}

const STATUS_ACTIONS: Record<CaseStatus, { to: CaseStatus; label: string }[]> = {
  open: [
    { to: "closed", label: "Close case" },
    { to: "archived", label: "Archive case" },
  ],
  closed: [
    { to: "open", label: "Reopen case" },
    { to: "archived", label: "Archive case" },
  ],
  archived: [{ to: "open", label: "Reopen case" }],
};

function StatusActions({ value, onChanged }: { value: Case; onChanged: (next: Case) => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const change = async (to: CaseStatus) => {
    setPending(true);
    setError(null);
    try {
      onChanged(await updateCase(value.id, { status: to }));
    } catch (caught) {
      setError(describeCaseError(caught));
    } finally {
      setPending(false);
    }
  };

  return (
    <section aria-labelledby="status-title" className="section">
      <h2 id="status-title" className="section__title">
        Status
      </h2>
      <p className="section__hint">
        Closed cases can be reopened. Archived cases are kept for reference.
      </p>
      <div className="button-row">
        {STATUS_ACTIONS[value.status].map(({ to, label }) => (
          <button
            key={to}
            type="button"
            className="button"
            disabled={pending}
            onClick={() => {
              void change(to);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="form-error" role="alert">
        {error}
      </div>
    </section>
  );
}

export const CACHED_RESULTS_HINT =
  "Some Shuttles reuse their recent results for the same entity instead of asking their source again. Clear this case's cached results to make the next jobs ask again; the graph does not change.";

function CachedResults({ value }: { value: Case }) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const clear = async () => {
    setConfirming(false);
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const deleted = await clearCaseResultCache(value.id);
      setNotice(
        deleted === 0
          ? "There were no cached results to clear."
          : `Cleared ${String(deleted)} cached result${deleted === 1 ? "" : "s"}.`,
      );
    } catch (caught) {
      setError(describeCaseError(caught));
    } finally {
      setPending(false);
    }
  };

  return (
    <section aria-labelledby="cached-results-title" className="section">
      <h2 id="cached-results-title" className="section__title">
        Cached Shuttle results
      </h2>
      <p className="section__hint">{CACHED_RESULTS_HINT}</p>
      <div className="button-row">
        <button
          type="button"
          className="button"
          disabled={pending}
          onClick={() => {
            setConfirming(true);
          }}
        >
          {pending ? "Clearing…" : "Clear cached results"}
        </button>
      </div>
      {notice !== null && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <div className="form-error" role="alert">
        {error}
      </div>
      {confirming && (
        <Modal
          title="Clear this case's cached results?"
          description="The next job on each entity asks its source again."
          onClose={() => {
            setConfirming(false);
          }}
        >
          <div className="dialog__actions">
            <button
              type="button"
              className="button"
              onClick={() => {
                setConfirming(false);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                void clear();
              }}
            >
              Clear
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}

function DangerZone({ value }: { value: Case }) {
  const navigate = useNavigate();
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();
  const matches = typed === value.title;

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!matches || pending) return;
    setPending(true);
    setError(null);
    try {
      await deleteCase(value.id, typed);
      void navigate("/cases", { replace: true, state: { notice: `Deleted “${value.title}”.` } });
    } catch (caught) {
      setError(describeCaseError(caught));
      setPending(false);
    }
  };

  return (
    <section aria-labelledby="danger-title" className="section danger-zone">
      <h2 id="danger-title" className="section__title">
        Danger zone
      </h2>
      <p className="section__hint">
        Deleting a case removes it and its memberships for everyone. Its activity record is kept.
        This cannot be undone.
      </p>
      <form
        noValidate
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <div className="field">
          <label className="field__label" htmlFor={inputId}>
            Type the case title <strong className="danger-zone__title">{value.title}</strong> to
            confirm
          </label>
          <input
            id={inputId}
            className="field__input"
            autoComplete="off"
            spellCheck={false}
            value={typed}
            onChange={(event) => {
              setTyped(event.target.value);
            }}
          />
        </div>
        <div className="form-error" role="alert">
          {error}
        </div>
        <button type="submit" className="button button--danger" disabled={!matches || pending}>
          {pending ? "Deleting…" : "Delete case"}
        </button>
      </form>
    </section>
  );
}
