import {
  ConflictError,
  ForbiddenError,
  NetworkError,
  NotFoundError,
  RequestTimeoutError,
  ValidationError,
} from "../api/client";
import { NETWORK_MESSAGE, TIMEOUT_MESSAGE, UNEXPECTED_MESSAGE } from "../auth/messages";

export const FORBIDDEN_MESSAGE = "You don't have permission to do this.";
export const CASE_NOT_FOUND_MESSAGE = "Case not found.";

/**
 * What to show for a failed case request. Server text is shown only for
 * validation (`422`), conflicts (`409`) and a missing user or member (`404`),
 * which are fixed messages written for users.
 */
export function describeCaseError(error: unknown): string {
  if (error instanceof ValidationError) return error.messages.map((m) => m.message).join(" ");
  if (error instanceof ConflictError) return error.message;
  if (error instanceof ForbiddenError) return FORBIDDEN_MESSAGE;
  if (error instanceof NotFoundError) return error.detail ?? CASE_NOT_FOUND_MESSAGE;
  if (error instanceof NetworkError) return NETWORK_MESSAGE;
  if (error instanceof RequestTimeoutError) return TIMEOUT_MESSAGE;
  return UNEXPECTED_MESSAGE;
}
