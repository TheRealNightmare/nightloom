import { useOutletContext } from "react-router";

import type { Case } from "../api/cases";

/** What the case workspace hands to its tabs. */
export interface CaseContextValue {
  case: Case;
  /** Replace the case after a successful change. */
  setCase: (next: Case) => void;
  /** Fetch the case again (e.g. after the caller's own role changed). */
  reload: () => void;
}

export function useCase(): CaseContextValue {
  return useOutletContext<CaseContextValue>();
}

/** A notice handed over by navigation, e.g. after deleting or leaving a case. */
export function noticeFrom(state: unknown): string | null {
  if (typeof state !== "object" || state === null || !("notice" in state)) return null;
  return typeof state.notice === "string" ? state.notice : null;
}
