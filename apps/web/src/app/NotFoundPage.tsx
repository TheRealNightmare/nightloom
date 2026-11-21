import { Link, useLocation } from "react-router";

import { useDocumentTitle } from "./documentTitle";

export interface NotFoundPageProps {
  /** Inside a case workspace: no page wrapper (the workspace sets the title). */
  embedded?: boolean;
}

/** A thread that runs out without a knot: nothing is tied here. */
function LooseThread() {
  return (
    <svg
      className="not-found__art"
      viewBox="0 0 200 64"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M4 40 C 40 40, 52 16, 88 18 S 132 46, 160 38"
        stroke="var(--brass)"
        strokeWidth="1.5"
      />
      {/* The frayed end. */}
      <path
        d="M160 38 l 12 -6 M160 38 l 13 -1 M160 38 l 11 5"
        stroke="var(--brass)"
        strokeWidth="1"
        strokeLinecap="round"
      />
      <line x1="4" y1="56" x2="196" y2="56" stroke="var(--hair)" strokeWidth="1" />
    </svg>
  );
}

function NotFound() {
  const { pathname } = useLocation();
  return (
    <div className="empty-state not-found">
      <LooseThread />
      <p className="eyebrow">404</p>
      <h1 className="empty-state__title">Page not found</h1>
      <p className="empty-state__text">
        No thread leads to <code className="not-found__path">{pathname}</code>. It may have been
        moved, or the link is mistyped.
      </p>
      <Link to="/cases" className="button button--primary">
        Go to cases
      </Link>
    </div>
  );
}

function NotFoundMain() {
  useDocumentTitle("Page not found");
  return (
    <main className="page">
      <NotFound />
    </main>
  );
}

export function NotFoundPage({ embedded = false }: NotFoundPageProps) {
  return embedded ? <NotFound /> : <NotFoundMain />;
}
