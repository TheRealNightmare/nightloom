import { useId, useState, type Ref } from "react";

export interface PasswordFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  name: string;
  error?: string | null;
  hint?: string;
  ref?: Ref<HTMLInputElement>;
}

/** A labelled password input with a show/hide toggle and an optional error. */
export function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  name,
  error = null,
  hint,
  ref,
}: PasswordFieldProps) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  const describedBy = [hint === undefined ? null : `${id}-hint`, error ? `${id}-error` : null]
    .filter((part) => part !== null)
    .join(" ");

  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      <div className="field__control">
        <input
          ref={ref}
          id={id}
          name={name}
          className="field__input field__input--password"
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          spellCheck={false}
          autoCapitalize="none"
          value={value}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          onChange={(event) => {
            onChange(event.target.value);
          }}
        />
        <button
          type="button"
          className="field__toggle"
          aria-pressed={visible}
          aria-label={`Show ${label.toLowerCase()}`}
          onClick={() => {
            setVisible((v) => !v);
          }}
        >
          {visible ? "Hide" : "Show"}
        </button>
      </div>
      {hint !== undefined && (
        <p id={`${id}-hint`} className="field__hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="field__error">
          {error}
        </p>
      )}
    </div>
  );
}
