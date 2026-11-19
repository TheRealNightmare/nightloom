import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";

import { focusableIn, trapTab } from "./focus";

export interface ModalProps {
  title: string;
  /** Optional text under the title. */
  description?: ReactNode;
  onClose: () => void;
  /** Focused when the dialog opens (default: the first focusable element). */
  initialFocus?: RefObject<HTMLElement | null>;
  /** Wider layout for forms with several fields. */
  wide?: boolean;
  /** A panel along the right edge, for reading details next to a list. */
  drawer?: boolean;
  children: ReactNode;
}

/**
 * A modal dialog: focus moves into it, Tab stays inside, Escape closes it,
 * and focus returns to where it was when it closes.
 */
export function Modal({
  title,
  description,
  onClose,
  initialFocus,
  wide,
  drawer,
  children,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const target = initialFocus?.current ?? focusableIn(dialogRef.current ?? document.body)[0];
    target?.focus();
    return () => {
      if (previous?.isConnected) previous.focus();
    };
    // Only on open and close.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    <div className={drawer ? "dialog-backdrop dialog-backdrop--drawer" : "dialog-backdrop"}>
      <div
        ref={dialogRef}
        className={drawer ? "dialog dialog--drawer" : wide ? "dialog dialog--wide" : "dialog"}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description === undefined ? undefined : descriptionId}
      >
        <h2 id={titleId} className="dialog__title">
          {title}
        </h2>
        {description !== undefined && (
          <p id={descriptionId} className="dialog__text">
            {description}
          </p>
        )}
        {children}
      </div>
    </div>
  );
}
