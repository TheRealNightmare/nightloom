import { useCallback, useEffect, useId, useState, type SubmitEvent } from "react";
import { useNavigate } from "react-router";

import {
  CASE_ROLES,
  can,
  listMembers,
  putMember,
  removeMember,
  type CaseRole,
  type Member,
} from "../api/cases";
import { RequestAbortedError } from "../api/client";
import { useAuth } from "../auth/context";
import { ROLE_LABELS } from "../auth/messages";
import { useCase } from "./caseContext";
import { describeCaseError } from "./errors";
import { Loading } from "./Feedback";
import { CASE_ROLE_LABELS } from "./labels";

/** Global viewers can only be case viewers (mirrors `allowed_case_roles`). */
function allowedRoles(member: Pick<Member, "global_role">): readonly CaseRole[] {
  return member.global_role === "viewer" ? ["viewer"] : CASE_ROLES;
}

export function MembersTab() {
  const { case: current, reload } = useCase();
  const { state } = useAuth();
  const navigate = useNavigate();
  const myId = state.status === "signed_in" ? state.user.id : null;
  const manage = can(current.my_role, "manage");

  const [members, setMembers] = useState<Member[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmLeave, setConfirmLeave] = useState(false);

  const refresh = useCallback(
    (signal?: AbortSignal) =>
      listMembers(current.id, signal ? { signal } : {}).then(
        (found) => {
          setMembers(found);
          setLoadError(null);
        },
        (caught: unknown) => {
          if (!(caught instanceof RequestAbortedError)) setLoadError(describeCaseError(caught));
        },
      ),
    [current.id],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => {
      controller.abort();
    };
  }, [refresh]);

  const changeRole = async (member: Member, role: CaseRole) => {
    setBusy(member.user_id);
    setRowError(null);
    try {
      await putMember(current.id, member.email, role);
      await refresh();
      if (member.user_id === myId) reload();
    } catch (caught) {
      setRowError(describeCaseError(caught));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (member: Member) => {
    setBusy(member.user_id);
    setRowError(null);
    try {
      await removeMember(current.id, member.user_id);
      if (member.user_id === myId) {
        void navigate("/cases", {
          replace: true,
          state: { notice: `You left “${current.title}”.` },
        });
        return;
      }
      await refresh();
    } catch (caught) {
      setRowError(describeCaseError(caught));
      setConfirmLeave(false);
    } finally {
      setBusy(null);
    }
  };

  if (loadError !== null && members === null) {
    return (
      <p className="form-error" role="alert">
        {loadError}
      </p>
    );
  }
  if (members === null) return <Loading label="Loading members…" />;

  return (
    <section aria-labelledby="members-title" className="section">
      <h2 id="members-title" className="section__title">
        Members
      </h2>
      {!manage && (
        <p className="section__hint">Only case owners can add members or change roles.</p>
      )}
      <div className="form-error" role="alert">
        {rowError}
      </div>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Email</th>
            <th scope="col">Role</th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {members.map((member) => {
            const self = member.user_id === myId;
            return (
              <tr key={member.user_id}>
                <td>
                  {member.display_name}
                  {self && <span className="table__you"> (you)</span>}
                  {!member.is_active && <span className="table__muted"> · deactivated</span>}
                </td>
                <td className="table__email">{member.email}</td>
                <td>
                  {manage ? (
                    <select
                      className="field__input field__input--compact"
                      aria-label={`Role of ${member.display_name}`}
                      value={member.role}
                      disabled={busy !== null}
                      onChange={(event) => {
                        void changeRole(member, event.target.value as CaseRole);
                      }}
                    >
                      {allowedRoles(member).map((role) => (
                        <option key={role} value={role}>
                          {CASE_ROLE_LABELS[role]}
                        </option>
                      ))}
                    </select>
                  ) : (
                    CASE_ROLE_LABELS[member.role]
                  )}
                  {member.global_role === "viewer" && (
                    <span className="table__muted" title="Global viewers can only be case viewers">
                      {" "}
                      · {ROLE_LABELS.viewer} account
                    </span>
                  )}
                </td>
                <td className="table__actions">
                  {self ? (
                    confirmLeave ? (
                      <span className="inline-confirm">
                        <span>Leave this case?</span>
                        <button
                          type="button"
                          className="button button--danger"
                          disabled={busy !== null}
                          onClick={() => {
                            void remove(member);
                          }}
                        >
                          Leave
                        </button>
                        <button
                          type="button"
                          className="button"
                          onClick={() => {
                            setConfirmLeave(false);
                          }}
                        >
                          Stay
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="button"
                        disabled={busy !== null}
                        onClick={() => {
                          setConfirmLeave(true);
                        }}
                      >
                        Leave case
                      </button>
                    )
                  ) : (
                    manage && (
                      <button
                        type="button"
                        className="button"
                        aria-label={`Remove ${member.display_name}`}
                        disabled={busy !== null}
                        onClick={() => {
                          void remove(member);
                        }}
                      >
                        Remove
                      </button>
                    )
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {manage && <AddMemberForm caseId={current.id} onAdded={() => void refresh()} />}
    </section>
  );
}

function AddMemberForm({ caseId, onAdded }: { caseId: string; onAdded: () => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<CaseRole>("viewer");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const emailId = useId();
  const roleId = useId();

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const address = email.trim();
    if (address === "" || !address.includes("@")) {
      setError("Enter the email address of an existing Nightloom user.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await putMember(caseId, address, role);
      setEmail("");
      onAdded();
    } catch (caught) {
      setError(describeCaseError(caught));
    } finally {
      setPending(false);
    }
  };

  return (
    <form
      className="add-member"
      aria-labelledby="add-member-title"
      noValidate
      onSubmit={(event) => {
        void submit(event);
      }}
    >
      <h3 id="add-member-title" className="section__subtitle">
        Add a member
      </h3>
      <div className="add-member__row">
        <div className="field field--inline add-member__email">
          <label className="field__label" htmlFor={emailId}>
            Email
          </label>
          <input
            id={emailId}
            type="email"
            className="field__input"
            autoComplete="off"
            value={email}
            aria-invalid={error === null ? undefined : true}
            aria-describedby={error === null ? undefined : `${emailId}-error`}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={roleId}>
            Role
          </label>
          <select
            id={roleId}
            className="field__input"
            value={role}
            onChange={(event) => {
              setRole(event.target.value as CaseRole);
            }}
          >
            {CASE_ROLES.map((value) => (
              <option key={value} value={value}>
                {CASE_ROLE_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? "Adding…" : "Add member"}
        </button>
      </div>
      {error !== null && (
        <p id={`${emailId}-error`} className="field__error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
