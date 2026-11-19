import { useEffect, useId, useRef, useState } from "react";

import { trapTab } from "./focus";
import { PasswordChangeForm } from "./PasswordChangeForm";

export const PASSWORD_CHANGED_MESSAGE = "Password changed. Other sessions were signed out.";

export interface ChangePasswordDialogProps {
  onClose: () => void;
}

/** Modal form for POST /auth/password. Render it only while open. */
export function ChangePasswordDialog({ onClose }: ChangePasswordDialogProps) {
  const [done, setDone] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const doneRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

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
          <PasswordChangeForm
            onSuccess={() => {
              setDone(true);
            }}
            secondary={
              <button type="button" className="button" onClick={onClose}>
                Cancel
              </button>
            }
          />
        )}
      </div>
    </div>
  );
}
