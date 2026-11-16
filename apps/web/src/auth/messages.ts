import {
  InvalidCredentialsError,
  type Role,
  NetworkError,
  RateLimitedError,
  RequestTimeoutError,
  UnexpectedResponseError,
  ValidationError,
} from "../api/client";

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Admin",
  analyst: "Analyst",
  viewer: "Viewer",
};

export const INVALID_CREDENTIALS_MESSAGE = "Invalid email or password.";
export const NETWORK_MESSAGE = "Cannot reach Nightloom. Check your connection and try again.";
export const TIMEOUT_MESSAGE = "Nightloom did not answer in time. Try again.";
export const UNAVAILABLE_MESSAGE = "Sign-in is temporarily unavailable. Try again shortly.";
export const UNEXPECTED_MESSAGE = "Something went wrong. Try again in a moment.";

/** "Too many attempts. Try again in N minutes." (N rounded up, at least 1). */
export function rateLimitMessage(retryAfterSeconds: number | null): string {
  if (retryAfterSeconds === null) return "Too many attempts. Try again later.";
  const minutes = Math.max(1, Math.ceil(retryAfterSeconds / 60));
  return `Too many attempts. Try again in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.`;
}

/**
 * What to tell the user about a failed request. Only validation messages come
 * from the server; everything else is fixed text.
 */
export function describeError(error: unknown): string {
  if (error instanceof InvalidCredentialsError) return INVALID_CREDENTIALS_MESSAGE;
  if (error instanceof RateLimitedError) return rateLimitMessage(error.retryAfterSeconds);
  if (error instanceof ValidationError) return error.messages.map((m) => m.message).join(" ");
  if (error instanceof NetworkError) return NETWORK_MESSAGE;
  if (error instanceof RequestTimeoutError) return TIMEOUT_MESSAGE;
  if (error instanceof UnexpectedResponseError && error.status === 503) return UNAVAILABLE_MESSAGE;
  return UNEXPECTED_MESSAGE;
}
