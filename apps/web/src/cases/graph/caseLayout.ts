import {
  ForbiddenError,
  NotFoundError,
  RateLimitedError,
  SessionExpiredError,
  ValidationError,
} from "../../api/client";
import { getLayout, saveLayout } from "../../api/layout";
import type { LayoutPersistence } from "../../graph/usePins";

/** The signed-in user's pins in a case, saved with the API (personal: viewers have their own too). */
export function caseLayout(caseId: string): LayoutPersistence {
  return {
    load: async (signal) => (await getLayout(caseId, { signal })).positions,
    save: (positions, { keepalive }) => saveLayout(caseId, positions, { keepalive }),
    // Retrying cannot fix these: signed out, no longer a member, a rejected layout.
    isPermanent: (error) =>
      error instanceof SessionExpiredError ||
      error instanceof ForbiddenError ||
      error instanceof NotFoundError ||
      error instanceof ValidationError,
    retryAfterSeconds: (error) =>
      error instanceof RateLimitedError ? error.retryAfterSeconds : null,
  };
}
