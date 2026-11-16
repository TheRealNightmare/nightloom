import { useEffect, useId, useRef, useState, type SubmitEvent } from "react";

import { LIMITS } from "../api/cases";
import { trapTab } from "../components/focus";
import { FormField } from "./FormField";
import { counterText, length, validateChangeReason } from "./validation";

export interface ChangeReasonDialogProps {
  /** What is changing, e.g. "purpose and legal basis". */
  fields: string;
  /**
   * Saves with the reason. Resolve with an error message for the reason
   * field to keep the dialog open, or null when done.
   */
  onSubmit: (reason: string) => Promise<string | null>;
  onCancel: () => void;
}

/** Modal asking why a purpose, legal basis or person-entities change is made (10–500 characters). */
export function ChangeReasonDialog({ fields, onSubmit, onCancel }: ChangeReasonDialogProps) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);
  const [pending, setPending] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const mounted = useRef(true);
  const titleId = useId();

  useEffect(() => {
    mounted.current = true;
    fieldRef.current?.focus();
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      } else if (dialogRef.current) {
        trapTab(event, dialogRef.current);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onCancel]);

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const problem = validateChangeReason(reason);
    setError(problem);
    if (problem !== undefined) {
      fieldRef.current?.focus();
      return;
    }
    setPending(true);
    const result = await onSubmit(reason.trim());
    if (!mounted.current) return;
    setPending(false);
    if (result !== null) {
      setError(result);
      fieldRef.current?.focus();
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
          Why are you making this change?
        </h2>
        <p className="dialog__text">
          Changing the {fields} is recorded in the case's activity together with your reason.
        </p>
        <form
          noValidate
          aria-busy={pending}
          onSubmit={(event) => {
            void submit(event);
          }}
        >
          <FormField
            label="Reason for the change"
            error={error}
            extra={counterText(length(reason), LIMITS.reasonMax, LIMITS.changeReasonMin)}
          >
            {(control) => (
              <textarea
                {...control}
                ref={fieldRef}
                name="change_reason"
                className="field__input field__textarea"
                rows={3}
                value={reason}
                onChange={(event) => {
                  setReason(event.target.value);
                }}
              />
            )}
          </FormField>
          <div className="dialog__actions">
            <button type="button" className="button" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="button button--primary" disabled={pending}>
              {pending ? "Saving…" : "Save change"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
