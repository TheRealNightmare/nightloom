import { useEffect, useRef, useState, type ReactNode, type SubmitEvent } from "react";

import { ValidationError } from "../api/client";
import { useAuth } from "../auth/context";
import { describeError } from "../auth/messages";
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  validatePasswordChange,
  type PasswordChangeErrors as FieldErrors,
} from "../auth/passwordPolicy";
import { PasswordField } from "./PasswordField";

export interface PasswordChangeFormProps {
  /** Called after the server accepted the new password. */
  onSuccess: () => void;
  /** Buttons shown before the submit button (Cancel, Sign out). */
  secondary: ReactNode;
  currentLabel?: string;
  submitLabel?: string;
  /** Class of the row that holds the buttons. */
  actionsClassName?: string;
}

/**
 * The password change form (POST /auth/password): checks the policy locally,
 * then shows the server's validation messages next to the fields. Used by the
 * "Change password" dialog and the forced-change screen. The first field is
 * focused when it appears.
 */
export function PasswordChangeForm({
  onSuccess,
  secondary,
  currentLabel = "Current password",
  submitLabel = "Change password",
  actionsClassName = "dialog__actions",
}: PasswordChangeFormProps) {
  const { changePassword } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    firstFieldRef.current?.focus();
    return () => {
      mounted.current = false;
    };
  }, []);

  const onSubmit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const errors = validatePasswordChange(current, next, confirm);
    setFieldErrors(errors);
    setFormError(null);
    if (Object.keys(errors).length > 0) return;

    setPending(true);
    try {
      await changePassword(current, next);
      if (mounted.current) onSuccess();
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ValidationError) {
        const server: FieldErrors = {};
        const general: string[] = [];
        for (const { field, message } of caught.messages) {
          if (field === "current_password") server.current = message;
          else if (field === "new_password" || field === null) server.next = message;
          else general.push(message);
        }
        setFieldErrors(server);
        setFormError(general.length > 0 ? general.join(" ") : null);
      } else {
        setFormError(describeError(caught));
      }
    } finally {
      if (mounted.current) setPending(false);
    }
  };

  return (
    <form
      noValidate
      aria-busy={pending}
      onSubmit={(event) => {
        void onSubmit(event);
      }}
    >
      <PasswordField
        ref={firstFieldRef}
        label={currentLabel}
        name="current_password"
        autoComplete="current-password"
        value={current}
        onChange={setCurrent}
        error={fieldErrors.current ?? null}
      />
      <PasswordField
        label="New password"
        name="new_password"
        autoComplete="new-password"
        value={next}
        onChange={setNext}
        hint={`${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} characters.`}
        error={fieldErrors.next ?? null}
      />
      <PasswordField
        label="Confirm new password"
        name="confirm_password"
        autoComplete="new-password"
        value={confirm}
        onChange={setConfirm}
        error={fieldErrors.confirm ?? null}
      />
      <div className="form-error" aria-live="polite">
        {formError}
      </div>
      <div className={actionsClassName}>
        {secondary}
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? "Saving…" : submitLabel}
        </button>
      </div>
    </form>
  );
}
