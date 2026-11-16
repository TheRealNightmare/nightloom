import { useId, type ReactNode } from "react";

export interface ControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
}

export interface FormFieldProps {
  label: ReactNode;
  /** Helper text under the control. */
  hint?: ReactNode;
  error?: string | undefined;
  /** E.g. a live character counter, shown next to the hint. */
  extra?: ReactNode;
  children: (control: ControlProps) => ReactNode;
}

/** A labelled control with optional hint, extra text and error, wired up with ARIA. */
export function FormField({ label, hint, error, extra, children }: FormFieldProps) {
  const id = useId();
  const described = [
    hint === undefined ? null : `${id}-hint`,
    extra === undefined ? null : `${id}-extra`,
    error ? `${id}-error` : null,
  ].filter((part) => part !== null);
  const control: ControlProps = { id };
  if (described.length > 0) control["aria-describedby"] = described.join(" ");
  if (error) control["aria-invalid"] = true;

  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children(control)}
      {(hint !== undefined || extra !== undefined) && (
        <div className="field__meta">
          {hint !== undefined && (
            <p id={`${id}-hint`} className="field__hint">
              {hint}
            </p>
          )}
          {extra !== undefined && (
            <p id={`${id}-extra`} className="field__hint field__counter">
              {extra}
            </p>
          )}
        </div>
      )}
      {error && (
        <p id={`${id}-error`} className="field__error">
          {error}
        </p>
      )}
    </div>
  );
}
