import { useCallback, useEffect, useRef, useState, type SubmitEvent } from "react";
import { NavLink, Outlet, useParams } from "react-router";

import { can, getCase, updateCase, type Case } from "../api/cases";
import { NotFoundError, RequestAbortedError, ValidationError } from "../api/client";
import { CaseRoleBadge, StatusBadge } from "./Badges";
import type { CaseContextValue } from "./caseContext";
import { describeCaseError } from "./errors";
import { CaseNotFound, Loading } from "./Feedback";
import { LEGAL_BASIS_LABELS } from "./labels";
import { validateTitle } from "./validation";

type Load =
  | { kind: "loading" }
  | { kind: "not_found" }
  | { kind: "error"; message: string }
  | { kind: "ready"; case: Case };

/** `/cases/:caseId`: the case header and its tabs. */
export function CaseWorkspace() {
  const { caseId = "" } = useParams();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getCase(caseId, { signal: controller.signal }).then(
      (found) => {
        setLoad({ kind: "ready", case: found });
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setLoad(
          caught instanceof NotFoundError
            ? { kind: "not_found" }
            : { kind: "error", message: describeCaseError(caught) },
        );
      },
    );
    return () => {
      controller.abort();
    };
  }, [caseId, generation]);

  const setCase = useCallback((next: Case) => {
    setLoad({ kind: "ready", case: next });
  }, []);
  const reload = useCallback(() => {
    setGeneration((n) => n + 1);
  }, []);

  if (load.kind === "loading") {
    return (
      <main className="page">
        <Loading label="Loading case…" />
      </main>
    );
  }
  if (load.kind === "not_found") return <CaseNotFound />;
  if (load.kind === "error") {
    return (
      <main className="page">
        <p className="form-error" role="alert">
          {load.message}
        </p>
        <button type="button" className="button" onClick={reload}>
          Try again
        </button>
      </main>
    );
  }

  const current = load.case;
  const context: CaseContextValue = { case: current, setCase, reload };
  return (
    <main className="page page--case">
      <CaseHeader key={current.id} value={current} onChange={setCase} />
      <nav className="tabs" aria-label="Case sections">
        <NavLink to="graph" className="tab">
          Graph
        </NavLink>
        <NavLink to="members" className="tab">
          Members
        </NavLink>
        {can(current.my_role, "read_audit") && (
          <NavLink to="activity" className="tab">
            Activity
          </NavLink>
        )}
        {can(current.my_role, "manage") && (
          <NavLink to="settings" className="tab">
            Settings
          </NavLink>
        )}
      </nav>
      <div className="tab-panel">
        <Outlet context={context} />
      </div>
    </main>
  );
}

function CaseHeader({ value, onChange }: { value: Case; onChange: (next: Case) => void }) {
  const editable = can(value.my_role, "edit");
  return (
    <header className="case-header">
      <p className="breadcrumb">
        <NavLink to="/cases" end>
          Cases
        </NavLink>
      </p>
      {editable ? (
        <EditableTitle value={value} onChange={onChange} />
      ) : (
        <h1 className="page__title">{value.title}</h1>
      )}
      <div className="case-header__meta">
        <StatusBadge status={value.status} />
        <span className="case-header__basis">
          <span className="visually-hidden">Legal basis: </span>
          {LEGAL_BASIS_LABELS[value.legal_basis]}
        </span>
        <span className="case-header__role">
          <span className="case-header__role-label">Your role</span>
          <CaseRoleBadge role={value.my_role} />
        </span>
      </div>
      {value.description !== null && (
        <p className="case-header__description">{value.description}</p>
      )}
      <details className="disclosure">
        <summary>Purpose</summary>
        <p className="disclosure__body">{value.purpose}</p>
        {value.legal_basis_note !== null && (
          <p className="disclosure__body">
            <strong>Legal basis note:</strong> {value.legal_basis_note}
          </p>
        )}
      </details>
    </header>
  );
}

function EditableTitle({ value, onChange }: { value: Case; onChange: (next: Case) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value.title);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const renameRef = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);

  useEffect(() => {
    if (editing) inputRef.current?.select();
    else if (wasEditing.current) renameRef.current?.focus();
    wasEditing.current = editing;
  }, [editing]);

  const cancel = () => {
    setDraft(value.title);
    setError(null);
    setEditing(false);
  };

  const save = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const title = draft.trim();
    if (title === value.title) {
      cancel();
      return;
    }
    const problem = validateTitle(title);
    if (problem !== undefined) {
      setError(problem);
      return;
    }
    setPending(true);
    try {
      onChange(await updateCase(value.id, { title }));
      setError(null);
      setEditing(false);
    } catch (caught) {
      setError(
        caught instanceof ValidationError
          ? caught.messages.map((m) => m.message).join(" ")
          : describeCaseError(caught),
      );
    } finally {
      setPending(false);
    }
  };

  if (!editing) {
    return (
      <div className="case-title">
        <h1 className="page__title">{value.title}</h1>
        <button
          ref={renameRef}
          type="button"
          className="link-button"
          onClick={() => {
            setDraft(value.title);
            setEditing(true);
          }}
        >
          Rename
        </button>
      </div>
    );
  }
  return (
    <form
      className="case-title case-title--editing"
      noValidate
      onSubmit={(event) => {
        void save(event);
      }}
    >
      <label className="visually-hidden" htmlFor="case-title-input">
        Case title
      </label>
      <input
        ref={inputRef}
        id="case-title-input"
        className="field__input case-title__input"
        value={draft}
        aria-invalid={error === null ? undefined : true}
        aria-describedby={error === null ? undefined : "case-title-error"}
        onChange={(event) => {
          setDraft(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            cancel();
          }
        }}
      />
      <button type="submit" className="button button--primary" disabled={pending}>
        {pending ? "Saving…" : "Save"}
      </button>
      <button type="button" className="button" onClick={cancel}>
        Cancel
      </button>
      {error !== null && (
        <p id="case-title-error" className="field__error case-title__error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
