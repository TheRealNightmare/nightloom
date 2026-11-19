import { useEffect, useRef, useState } from "react";

import { Modal } from "../components/Modal";

export const ONE_TIME_WARNING = "It will not be shown again. Share it through a secure channel.";
export const COPIED_MESSAGE = "Copied to the clipboard.";
export const COPY_FAILED_MESSAGE = "Could not copy. Select the password and copy it by hand.";

export interface TemporaryPasswordPanelProps {
  title: string;
  /** Who the password is for, e.g. "Bob Builder (bob@example.org)". */
  forWhom: string;
  password: string;
  /** The caller forgets the password when this is called. */
  onClose: () => void;
}

/**
 * Shows a temporary password once, with a copy button. The password lives
 * only in the caller's state while this is open; it is never logged.
 */
export function TemporaryPasswordPanel({
  title,
  forWhom,
  password,
  onClose,
}: TemporaryPasswordPanelProps) {
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const copyRef = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(password);
      if (mounted.current) setCopy("copied");
    } catch {
      if (mounted.current) setCopy("failed");
    }
  };

  return (
    <Modal title={title} onClose={onClose} initialFocus={copyRef}>
      <p className="dialog__text">
        Temporary password for <strong>{forWhom}</strong>. They must choose a new password when they
        sign in.
      </p>
      <div className="temp-password">
        <code className="temp-password__value" aria-label="Temporary password">
          {password}
        </code>
        <button
          ref={copyRef}
          type="button"
          className="button"
          onClick={() => {
            void onCopy();
          }}
        >
          Copy
        </button>
      </div>
      <p className="temp-password__warning" role="note">
        {ONE_TIME_WARNING}
      </p>
      <p className="temp-password__status" aria-live="polite">
        {copy === "copied" ? COPIED_MESSAGE : copy === "failed" ? COPY_FAILED_MESSAGE : null}
      </p>
      <div className="dialog__actions">
        <button type="button" className="button button--primary" onClick={onClose}>
          Done
        </button>
      </div>
    </Modal>
  );
}
