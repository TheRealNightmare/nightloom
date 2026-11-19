import { useId, useRef, useState, type SubmitEvent } from "react";

import { createUser, type CreatedUser } from "../api/admin";
import { ConflictError, ValidationError, type Role } from "../api/client";
import { ROLE_LABELS } from "../auth/messages";
import { Modal } from "../components/Modal";
import { describeAdminError } from "./errors";

const ROLES: readonly Role[] = ["analyst", "viewer", "admin"];

type Field = "email" | "display_name" | "role";
type FieldErrors = Partial<Record<Field, string>>;

const ROLE_HELP: Record<Role, string> = {
  admin: "Manages users and reads the audit log. Has no access to cases they are not a member of.",
  analyst: "Creates cases and works in the cases they are a member of.",
  viewer: "Reads the cases they are added to; cannot change anything.",
};

export interface CreateUserDialogProps {
  onClose: () => void;
  /** The new user and their one-time temporary password. */
  onCreated: (created: CreatedUser) => void;
}

/** "New user": email, display name and role; the server generates the password. */
export function CreateUserDialog({ onClose, onCreated }: CreateUserDialogProps) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("analyst");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);
  const ids = { email: useId(), name: useId(), role: useId() };

  const onSubmit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const local: FieldErrors = {};
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      local.email = "Enter a valid email address.";
    }
    if (name.trim() === "") local.display_name = "Enter a display name.";
    setErrors(local);
    setFormError(null);
    if (Object.keys(local).length > 0) return;

    setPending(true);
    try {
      onCreated(await createUser({ email: email.trim(), display_name: name.trim(), role }));
    } catch (caught) {
      setPending(false);
      if (caught instanceof ValidationError) {
        const server: FieldErrors = {};
        const general: string[] = [];
        for (const { field, message } of caught.messages) {
          if (field === "email" || field === "display_name" || field === "role") {
            server[field] = message;
          } else {
            general.push(message);
          }
        }
        setErrors(server);
        setFormError(general.length > 0 ? general.join(" ") : null);
      } else if (caught instanceof ConflictError) {
        setErrors({ email: caught.message });
      } else {
        setFormError(describeAdminError(caught));
      }
    }
  };

  const describedBy = (field: Field, id: string, hint = false) =>
    [hint ? `${id}-hint` : null, errors[field] ? `${id}-error` : null]
      .filter((part) => part !== null)
      .join(" ") || undefined;

  return (
    <Modal
      title="New user"
      description="Nightloom generates a temporary password. The new user replaces it when they first sign in."
      onClose={onClose}
      initialFocus={emailRef}
    >
      <form
        noValidate
        aria-busy={pending}
        onSubmit={(event) => {
          void onSubmit(event);
        }}
      >
        <div className="field">
          <label className="field__label" htmlFor={ids.email}>
            Email
          </label>
          <input
            ref={emailRef}
            id={ids.email}
            className="field__input"
            type="email"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={320}
            value={email}
            aria-invalid={errors.email ? true : undefined}
            aria-describedby={describedBy("email", ids.email)}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
          {errors.email && (
            <p id={`${ids.email}-error`} className="field__error">
              {errors.email}
            </p>
          )}
        </div>
        <div className="field">
          <label className="field__label" htmlFor={ids.name}>
            Display name
          </label>
          <input
            id={ids.name}
            className="field__input"
            autoComplete="off"
            maxLength={100}
            value={name}
            aria-invalid={errors.display_name ? true : undefined}
            aria-describedby={describedBy("display_name", ids.name)}
            onChange={(event) => {
              setName(event.target.value);
            }}
          />
          {errors.display_name && (
            <p id={`${ids.name}-error`} className="field__error">
              {errors.display_name}
            </p>
          )}
        </div>
        <div className="field">
          <label className="field__label" htmlFor={ids.role}>
            Role
          </label>
          <select
            id={ids.role}
            className="field__input"
            value={role}
            aria-describedby={describedBy("role", ids.role, true)}
            onChange={(event) => {
              setRole(event.target.value as Role);
            }}
          >
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {ROLE_LABELS[value]}
              </option>
            ))}
          </select>
          <p id={`${ids.role}-hint`} className="field__hint">
            {ROLE_HELP[role]}
          </p>
          {errors.role && (
            <p id={`${ids.role}-error`} className="field__error">
              {errors.role}
            </p>
          )}
        </div>
        <div className="form-error" role="alert">
          {formError}
        </div>
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={pending}>
            {pending ? "Creating…" : "Create user"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
