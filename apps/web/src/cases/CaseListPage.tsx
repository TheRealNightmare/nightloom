import { useEffect, useId, useState } from "react";
import { Link, useLocation } from "react-router";

import { CASE_STATUSES, canCreateCases, listCases, type Case, type CaseStatus } from "../api/cases";
import { RequestAbortedError } from "../api/client";
import { useAuth } from "../auth/context";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { CaseRoleBadge, StatusBadge } from "./Badges";
import { noticeFrom } from "./caseContext";
import { describeCaseError } from "./errors";
import { Loading } from "./Feedback";
import { LEGAL_BASIS_LABELS, STATUS_LABELS } from "./labels";
import { RelativeTime } from "./RelativeTime";

export const CASE_PAGE_SIZE = 25;
export const SEARCH_DEBOUNCE_MS = 300;

interface Listing {
  items: Case[];
  nextCursor: string | null;
}

export function CaseListPage() {
  const { state } = useAuth();
  const location = useLocation();
  const notice = noticeFrom(location.state);
  const mayCreate = state.status === "signed_in" && canCreateCases(state.user.role);

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<CaseStatus | "">("");
  const q = useDebouncedValue(search.trim(), SEARCH_DEBOUNCE_MS);
  // Results are tagged with the filters they answer, so stale ones are never shown.
  const filterKey = `${status}\n${q}`;
  const [result, setResult] = useState<{ key: string; listing: Listing | null }>({
    key: "",
    listing: null,
  });
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const listing = result.key === filterKey ? result.listing : null;
  const errorMessage = error?.key === filterKey ? error.message : null;
  const setListing = (update: (current: Listing | null) => Listing) => {
    setResult((current) => ({ key: filterKey, listing: update(current.listing) }));
  };
  const [loadingMore, setLoadingMore] = useState(false);
  const searchId = useId();
  const statusId = useId();

  useEffect(() => {
    const controller = new AbortController();
    const key = `${status}\n${q}`;
    listCases(
      { q, status: status === "" ? null : status, limit: CASE_PAGE_SIZE },
      { signal: controller.signal },
    ).then(
      (page) => {
        setResult({ key, listing: { items: page.items, nextCursor: page.next_cursor } });
      },
      (caught: unknown) => {
        if (!(caught instanceof RequestAbortedError)) {
          setError({ key, message: describeCaseError(caught) });
        }
      },
    );
    return () => {
      controller.abort();
    };
  }, [q, status]);

  const loadMore = async () => {
    if (listing?.nextCursor == null || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await listCases({
        q,
        status: status === "" ? null : status,
        limit: CASE_PAGE_SIZE,
        cursor: listing.nextCursor,
      });
      setListing((current) => ({
        items: [...(current?.items ?? []), ...page.items],
        nextCursor: page.next_cursor,
      }));
    } catch (caught) {
      setError({ key: filterKey, message: describeCaseError(caught) });
    } finally {
      setLoadingMore(false);
    }
  };

  const filtered = q !== "" || status !== "";

  return (
    <main className="page">
      <div className="page__header">
        <h1 className="page__title">Cases</h1>
        {mayCreate && (
          <Link to="/cases/new" className="button button--primary">
            New case
          </Link>
        )}
      </div>
      {notice !== null && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <div className="filters" role="search">
        <div className="field field--inline">
          <label className="field__label" htmlFor={searchId}>
            Search titles
          </label>
          <input
            id={searchId}
            type="search"
            className="field__input"
            value={search}
            maxLength={200}
            onChange={(event) => {
              setSearch(event.target.value);
            }}
          />
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={statusId}>
            Status
          </label>
          <select
            id={statusId}
            className="field__input"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as CaseStatus | "");
            }}
          >
            <option value="">All</option>
            {CASE_STATUSES.map((value) => (
              <option key={value} value={value}>
                {STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
      </div>

      {listing === null && errorMessage === null && <Loading label="Loading cases…" />}
      {listing !== null && listing.items.length === 0 && (
        <div className="empty-state">
          <h2 className="empty-state__title">{filtered ? "No matching cases" : "No cases yet"}</h2>
          <p className="empty-state__text">
            {filtered
              ? "Try another search or status."
              : mayCreate
                ? "Create a case to start an investigation. Every case states its purpose and legal basis first."
                : "You are not a member of any case yet. Ask a case owner to add you."}
          </p>
        </div>
      )}
      {listing !== null && listing.items.length > 0 && (
        <ul className="case-list" aria-label="Cases">
          {listing.items.map((item) => (
            <li key={item.id} className="case-row">
              <div className="case-row__main">
                <Link to={`/cases/${item.id}`} className="case-row__title">
                  {item.title}
                </Link>
                <span className="case-row__basis">{LEGAL_BASIS_LABELS[item.legal_basis]}</span>
              </div>
              <div className="case-row__meta">
                <StatusBadge status={item.status} />
                <CaseRoleBadge role={item.my_role} />
                <span className="case-row__time">
                  Updated <RelativeTime iso={item.updated_at} />
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
      {errorMessage !== null && (
        <p className="form-error" role="alert">
          {errorMessage}
        </p>
      )}
      {listing?.nextCursor != null && (
        <div className="load-more">
          <button
            type="button"
            className="button"
            disabled={loadingMore}
            onClick={() => {
              void loadMore();
            }}
          >
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </main>
  );
}
