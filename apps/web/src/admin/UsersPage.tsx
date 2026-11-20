import { useCallback, useEffect, useId, useMemo, useState } from "react";

import {
  listUsers,
  resetPassword,
  revokeSessions,
  updateUser,
  type CreatedUser,
} from "../api/admin";
import { RequestAbortedError, type Role, type User } from "../api/client";
import { useAuth } from "../auth/context";
import { ROLE_LABELS } from "../auth/messages";
import { LoadError, Loading } from "../cases/Feedback";
import { RelativeTime } from "../cases/RelativeTime";
import { Modal } from "../components/Modal";
import { CreateUserDialog } from "./CreateUserDialog";
import { describeAdminError } from "./errors";
import { matchesUser, type ActiveFilter } from "./userFilters";
import { TemporaryPasswordPanel } from "./TemporaryPasswordPanel";
import { useDocumentTitle } from "../app/documentTitle";

const ROLES: readonly Role[] = ["admin", "analyst", "viewer"];

/** What is open on top of the table. A temporary password lives only in `password`. */
type Dialog =
  | { kind: "create" }
  | { kind: "deactivate"; user: User }
  | { kind: "revoke"; user: User }
  | { kind: "reset"; user: User }
  | { kind: "password"; title: string; user: User; password: string };

const describe = (user: User) => `${user.display_name} (${user.email})`;

/** /admin/users: every account, with the admin's actions on each. */
export function UsersPage() {
  useDocumentTitle("Users", "Admin");
  const { state } = useAuth();
  const myId = state.status === "signed_in" ? state.user.id : null;
  const [users, setUsers] = useState<User[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState<Role | "">("");
  const [active, setActive] = useState<ActiveFilter>("");
  const ids = { search: useId(), role: useId(), active: useId() };
  // Bumped by "Try again" after a failed first load.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    listUsers({ signal: controller.signal }).then(setUsers, (caught: unknown) => {
      if (!(caught instanceof RequestAbortedError)) setLoadError(describeAdminError(caught));
    });
    return () => {
      controller.abort();
    };
  }, [attempt]);

  const shown = useMemo(
    () => (users ?? []).filter((user) => matchesUser(user, search, role, active)),
    [users, search, role, active],
  );

  const replace = (updated: User) => {
    setUsers((current) => (current ?? []).map((u) => (u.id === updated.id ? updated : u)));
  };

  /** Runs one row action; its error appears above the table. */
  const run = async (user: User, action: () => Promise<string | null>) => {
    setBusy(user.id);
    setRowError(null);
    setNotice(null);
    try {
      setNotice(await action());
    } catch (caught) {
      setRowError(`${user.display_name}: ${describeAdminError(caught)}`);
    } finally {
      setBusy(null);
    }
  };

  const changeRole = (user: User, next: Role) =>
    run(user, async () => {
      replace(await updateUser(user.id, { role: next }));
      return `${user.display_name} is now ${ROLE_LABELS[next].toLowerCase()}.`;
    });

  const setActiveFlag = (user: User, value: boolean) =>
    run(user, async () => {
      replace(await updateUser(user.id, { is_active: value }));
      return value
        ? `${user.display_name} can sign in again.`
        : `${user.display_name} is deactivated and signed out everywhere.`;
    });

  const revoke = (user: User) =>
    run(user, async () => {
      await revokeSessions(user.id);
      return `${user.display_name} is signed out everywhere.`;
    });

  const reset = (user: User) =>
    run(user, async () => {
      const result = await resetPassword(user.id);
      replace(result.user);
      setDialog({
        kind: "password",
        title: "Password reset",
        user: result.user,
        password: result.temporaryPassword,
      });
      return `${user.display_name}'s password was reset and their sessions ended.`;
    });

  const onCreated = useCallback((created: CreatedUser) => {
    setUsers((current) => [...(current ?? []), created.user]);
    setNotice(`${created.user.display_name} was created.`);
    setRowError(null);
    setDialog(
      created.temporaryPassword === null
        ? null
        : {
            kind: "password",
            title: "User created",
            user: created.user,
            password: created.temporaryPassword,
          },
    );
  }, []);

  const closeDialog = useCallback(() => {
    // Also drops a temporary password from memory.
    setDialog(null);
  }, []);

  const confirm = (
    title: string,
    text: string,
    label: string,
    user: User,
    action: (user: User) => Promise<void>,
  ) => (
    <Modal title={title} description={text} onClose={closeDialog}>
      <div className="dialog__actions">
        <button type="button" className="button" onClick={closeDialog}>
          Cancel
        </button>
        <button
          type="button"
          className="button button--danger"
          onClick={() => {
            setDialog(null);
            void action(user);
          }}
        >
          {label}
        </button>
      </div>
    </Modal>
  );

  const filtered = search.trim() !== "" || role !== "" || active !== "";

  return (
    <main className="page">
      <div className="page__header">
        <h1 className="page__title">Users</h1>
        <button
          type="button"
          className="button button--primary"
          onClick={() => {
            setDialog({ kind: "create" });
          }}
        >
          New user
        </button>
      </div>
      {notice !== null && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <div className="filters" role="search">
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.search}>
            Search name or email
          </label>
          <input
            id={ids.search}
            type="search"
            className="field__input"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
            }}
          />
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.role}>
            Role
          </label>
          <select
            id={ids.role}
            className="field__input"
            value={role}
            onChange={(event) => {
              setRole(event.target.value as Role | "");
            }}
          >
            <option value="">All</option>
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {ROLE_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.active}>
            Status
          </label>
          <select
            id={ids.active}
            className="field__input"
            value={active}
            onChange={(event) => {
              setActive(event.target.value as ActiveFilter);
            }}
          >
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="inactive">Deactivated</option>
          </select>
        </div>
      </div>

      {users === null && loadError !== null ? (
        <LoadError
          message={loadError}
          onRetry={() => {
            setLoadError(null);
            setAttempt((n) => n + 1);
          }}
        />
      ) : (
        <div className="form-error" role="alert">
          {rowError ?? loadError}
        </div>
      )}
      {users === null && loadError === null && <Loading label="Loading users…" />}
      {users !== null && shown.length === 0 && (
        <div className="empty-state">
          <h2 className="empty-state__title">{filtered ? "No matching users" : "No users"}</h2>
          {filtered && <p className="empty-state__text">Try another search or filter.</p>}
        </div>
      )}
      {shown.length > 0 && (
        <div className="table-scroll">
          <table className="table" aria-label="Users">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Last sign-in</th>
                <th scope="col">Created</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((user) => {
                const self = user.id === myId;
                const disabled = busy !== null;
                return (
                  <tr key={user.id} data-active={user.is_active}>
                    <td>
                      {user.display_name}
                      {self && <span className="table__you"> (you)</span>}
                    </td>
                    <td className="table__email">{user.email}</td>
                    <td>
                      <select
                        className="field__input field__input--compact"
                        aria-label={`Role of ${user.display_name}`}
                        value={user.role}
                        disabled={disabled}
                        onChange={(event) => {
                          void changeRole(user, event.target.value as Role);
                        }}
                      >
                        {ROLES.map((value) => (
                          <option key={value} value={value}>
                            {ROLE_LABELS[value]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <span className="user-status" data-active={user.is_active}>
                        {user.is_active ? "Active" : "Deactivated"}
                      </span>
                      {user.must_change_password && (
                        <span className="table__muted" title="Must choose a new password">
                          {" "}
                          · temporary password
                        </span>
                      )}
                    </td>
                    <td>
                      {user.last_login_at === null ? (
                        <span className="table__muted">Never</span>
                      ) : (
                        <RelativeTime iso={user.last_login_at} />
                      )}
                    </td>
                    <td>
                      <RelativeTime iso={user.created_at} />
                    </td>
                    <td className="table__actions">
                      <span className="row-actions">
                        {!self &&
                          (user.is_active ? (
                            <button
                              type="button"
                              className="button button--small"
                              disabled={disabled}
                              aria-label={`Deactivate ${user.display_name}`}
                              onClick={() => {
                                setDialog({ kind: "deactivate", user });
                              }}
                            >
                              Deactivate
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="button button--small"
                              disabled={disabled}
                              aria-label={`Activate ${user.display_name}`}
                              onClick={() => {
                                void setActiveFlag(user, true);
                              }}
                            >
                              Activate
                            </button>
                          ))}
                        <button
                          type="button"
                          className="button button--small"
                          disabled={disabled}
                          aria-label={`Revoke sessions of ${user.display_name}`}
                          onClick={() => {
                            setDialog({ kind: "revoke", user });
                          }}
                        >
                          Revoke sessions
                        </button>
                        {!self && (
                          <button
                            type="button"
                            className="button button--small"
                            disabled={disabled}
                            aria-label={`Reset password of ${user.display_name}`}
                            onClick={() => {
                              setDialog({ kind: "reset", user });
                            }}
                          >
                            Reset password
                          </button>
                        )}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {dialog?.kind === "create" && (
        <CreateUserDialog onClose={closeDialog} onCreated={onCreated} />
      )}
      {dialog?.kind === "deactivate" &&
        confirm(
          `Deactivate ${dialog.user.display_name}?`,
          "They are signed out everywhere and cannot sign in until an admin activates them again. Their case memberships stay.",
          "Deactivate",
          dialog.user,
          (user) => setActiveFlag(user, false),
        )}
      {dialog?.kind === "revoke" &&
        confirm(
          `Sign ${dialog.user.display_name} out everywhere?`,
          "Every session of this user ends now. They can sign in again with their password.",
          "Revoke sessions",
          dialog.user,
          revoke,
        )}
      {dialog?.kind === "reset" &&
        confirm(
          `Reset the password of ${dialog.user.display_name}?`,
          "Their current password stops working, every session ends, and Nightloom shows you a temporary password once. They must replace it when they sign in.",
          "Reset password",
          dialog.user,
          reset,
        )}
      {dialog?.kind === "password" && (
        <TemporaryPasswordPanel
          title={dialog.title}
          forWhom={describe(dialog.user)}
          password={dialog.password}
          onClose={closeDialog}
        />
      )}
    </main>
  );
}
