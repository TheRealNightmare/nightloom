import { Link } from "react-router";

export interface NotFoundPageProps {
  /** Inside a case workspace: no page wrapper. */
  embedded?: boolean;
}

export function NotFoundPage({ embedded = false }: NotFoundPageProps) {
  const body = (
    <div className="empty-state">
      <h1 className="empty-state__title">Page not found</h1>
      <p className="empty-state__text">There is nothing at this address.</p>
      <Link to="/cases" className="button">
        Go to cases
      </Link>
    </div>
  );
  return embedded ? body : <main className="page">{body}</main>;
}
