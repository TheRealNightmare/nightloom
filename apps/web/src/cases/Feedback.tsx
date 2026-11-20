import { Link } from "react-router";

import { CASE_NOT_FOUND_MESSAGE } from "./errors";

/** Shown inside a page when the caller's case role does not allow it (403). */
export function Forbidden() {
  return (
    <div className="empty-state" role="alert">
      <h2 className="empty-state__title">You don't have permission</h2>
      <p className="empty-state__text">Your role in this case does not allow this.</p>
    </div>
  );
}

/** Same text whether the case does not exist or the caller is not a member (404). */
export function CaseNotFound() {
  return (
    <main className="page">
      <div className="empty-state">
        <h1 className="empty-state__title">{CASE_NOT_FOUND_MESSAGE.replace(".", "")}</h1>
        <p className="empty-state__text">
          It does not exist, or you are not a member of it. Ask a case owner to add you.
        </p>
        <Link to="/cases" className="button">
          Back to cases
        </Link>
      </div>
    </main>
  );
}

/**
 * A failed load and the next step: the reason, and "Try again" (the page
 * keeps its filters; nothing else is lost).
 */
export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="load-error" role="alert">
      <p className="form-error">{message}</p>
      <button type="button" className="button" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

/**
 * A loading state: what is loading, in words, over a few still skeleton lines
 * (no spinner, no shimmer). The lines are decoration; the words are the status.
 */
export function Loading({ label = "Loading…", lines = 3 }: { label?: string; lines?: number }) {
  return (
    <div className="loading" role="status">
      <p className="loading__label">{label}</p>
      <div className="skeleton" aria-hidden="true">
        {Array.from({ length: lines }, (_, index) => (
          <span key={index} className="skeleton__line" />
        ))}
      </div>
    </div>
  );
}
