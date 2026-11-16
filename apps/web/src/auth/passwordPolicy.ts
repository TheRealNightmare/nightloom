/** Same policy as apps/api/src/nightloom_api/auth/passwords.py. */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;

export type PasswordChangeField = "current" | "next" | "confirm";
export type PasswordChangeErrors = Partial<Record<PasswordChangeField, string>>;

/** Length in code points, as the server (Python `len`) counts it. */
function length(value: string): number {
  return Array.from(value).length;
}

/** Client-side checks before POST /auth/password; the server checks again. */
export function validatePasswordChange(
  current: string,
  next: string,
  confirm: string,
): PasswordChangeErrors {
  const errors: PasswordChangeErrors = {};
  if (current === "") errors.current = "Enter your current password.";
  if (length(next) < PASSWORD_MIN_LENGTH) {
    errors.next = `New password must be at least ${PASSWORD_MIN_LENGTH} characters long.`;
  } else if (length(next) > PASSWORD_MAX_LENGTH) {
    errors.next = `New password must be at most ${PASSWORD_MAX_LENGTH} characters long.`;
  } else if (current !== "" && next === current) {
    errors.next = "New password must differ from the current one.";
  }
  if (confirm !== next) errors.confirm = "Passwords do not match.";
  return errors;
}
