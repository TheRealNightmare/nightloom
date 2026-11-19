import {
  ConflictError,
  ForbiddenError,
  NetworkError,
  NotFoundError,
  RateLimitedError,
  RequestTimeoutError,
  UnexpectedResponseError,
  ValidationError,
} from "../api/client";
import { NETWORK_MESSAGE, TIMEOUT_MESSAGE, UNEXPECTED_MESSAGE } from "../auth/messages";
import { FORBIDDEN_MESSAGE } from "../cases/errors";

export const USER_NOT_FOUND_MESSAGE = "User not found.";
export const SELF_RESET_MESSAGE =
  "You cannot reset your own password. Use Change password in your account menu.";
export const VERIFY_BUSY_MESSAGE = "A verification is already running. Try again shortly.";

/**
 * What to show for a failed admin request. Server text is shown only for
 * validation (`422`) and conflicts (`409`: the last admin, your own role, an
 * email in use), which are fixed messages written for users.
 */
export function describeAdminError(error: unknown): string {
  if (error instanceof ValidationError) return error.messages.map((m) => m.message).join(" ");
  if (error instanceof ConflictError) return error.message;
  if (error instanceof ForbiddenError) return FORBIDDEN_MESSAGE;
  if (error instanceof NotFoundError) return USER_NOT_FOUND_MESSAGE;
  if (error instanceof RateLimitedError) return VERIFY_BUSY_MESSAGE;
  if (error instanceof UnexpectedResponseError && error.status === 400) return SELF_RESET_MESSAGE;
  if (error instanceof NetworkError) return NETWORK_MESSAGE;
  if (error instanceof RequestTimeoutError) return TIMEOUT_MESSAGE;
  return UNEXPECTED_MESSAGE;
}
