import { useEffect, useId, useRef, useState, type SubmitEvent } from "react";

import { ValidationError } from "../api/client";
import { useAuth } from "../auth/context";
import { describeError } from "../auth/messages";
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  validatePasswordChange,
  type PasswordChangeErrors as FieldErrors,
} from "../auth/passwordPolicy";
import { trapTab } from "./focus";
import { PasswordField } from "./PasswordField";

export const PASSWORD_CHANGED_MESSAGE = "Password changed. Other sessions were signed out.";

export interface ChangePasswordDialogProps {
  onClose: () => void;
}

/** Modal form for POST /auth/password. Render it only while open. */
export function ChangePasswordDialog({ onClose }: ChangePasswordDialogProps) {
  const { changePassword } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const doneRef = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true);
  const titleId = useId();

  useEffect(() => {
    mounted.current = true;
    firstFieldRef.current?.focus();
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (done) doneRef.current?.focus();
  }, [done]);

  // Escape closes; Tab stays inside the dialog.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      } else if (dialogRef.current) {
        trapTab(event, dialogRef.current);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

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
      if (mounted.current) setDone(true);
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
    <div className="dialog-backdrop">
      <div
        ref={dialogRef}
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <h2 id={titleId} className="dialog__title">
          Change password
        </h2>
        {done ? (
          <>
            <p className="dialog__success" role="status">
              {PASSWORD_CHANGED_MESSAGE}
            </p>
            <div className="dialog__actions">
              <button
                ref={doneRef}
                type="button"
                className="button button--primary"
                onClick={onClose}
              >
                Done
              </button>
            </div>
          </>
        ) : (
          <form
            noValidate
            aria-busy={pending}
            onSubmit={(event) => {
              void onSubmit(event);
            }}
          >
            <PasswordField
              ref={firstFieldRef}
              label="Current password"
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
            <div className="dialog__actions">
              <button type="button" className="button" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="button button--primary" disabled={pending}>
                {pending ? "Saving…" : "Change password"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
