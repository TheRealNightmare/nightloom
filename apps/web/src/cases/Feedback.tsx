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

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="loading" role="status">
      {label}
    </p>
  );
}
