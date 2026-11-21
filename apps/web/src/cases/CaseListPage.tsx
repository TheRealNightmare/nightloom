import { useEffect, useId, useState } from "react";
import { Link, useLocation } from "react-router";

import { CASE_STATUSES, canCreateCases, listCases, type Case, type CaseStatus } from "../api/cases";
import { RequestAbortedError } from "../api/client";
import { useAuth } from "../auth/context";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { CaseRoleBadge, StatusBadge } from "./Badges";
import { noticeFrom } from "./caseContext";
import { describeCaseError } from "./errors";
import { LoadError, Loading } from "./Feedback";
import { LEGAL_BASIS_LABELS, STATUS_LABELS } from "./labels";
import { RelativeTime } from "./RelativeTime";
import { useDocumentTitle } from "../app/documentTitle";

export const CASE_PAGE_SIZE = 25;
export const CASES_SUBTITLE = "Every investigation starts with a stated purpose and a legal basis.";

/** The status filter's choices: all, then each status. */
const STATUS_FILTERS: readonly { value: CaseStatus | ""; label: string }[] = [
  { value: "", label: "All" },
  ...CASE_STATUSES.map((value) => ({ value, label: STATUS_LABELS[value] })),
];
export const SEARCH_DEBOUNCE_MS = 300;

interface Listing {
  items: Case[];
  nextCursor: string | null;
}

export function CaseListPage() {
  useDocumentTitle("Cases");
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
  // Bumped by "Try again" after a failed first load.
  const [attempt, setAttempt] = useState(0);
  const searchId = useId();
  const statusName = useId();

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
  }, [q, status, attempt]);

  const retry = () => {
    setError(null);
    setAttempt((n) => n + 1);
  };

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
        <div>
          <h1 className="page__title page__title--hero">Cases</h1>
          <p className="page__subtitle">{CASES_SUBTITLE}</p>
        </div>
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
        <fieldset className="segmented-field">
          <legend className="field__label">Status</legend>
          <div className="segmented">
            {STATUS_FILTERS.map(({ value, label }) => (
              <label key={value} className="segmented__option">
                <input
                  type="radio"
                  name={statusName}
                  className="segmented__input"
                  value={value}
                  checked={status === value}
                  onChange={() => {
                    setStatus(value);
                  }}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
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
                <div className="case-row__chips">
                  <StatusBadge status={item.status} />
                  <span className="chip case-row__basis">
                    <span className="visually-hidden">Legal basis: </span>
                    {LEGAL_BASIS_LABELS[item.legal_basis]}
                  </span>
                  <CaseRoleBadge role={item.my_role} />
                </div>
              </div>
              <span className="case-row__time">
                Updated <RelativeTime iso={item.updated_at} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {errorMessage !== null &&
        (listing === null ? (
          <LoadError message={errorMessage} onRetry={retry} />
        ) : (
          // "Load more" failed: its button below is the next step.
          <p className="form-error" role="alert">
            {errorMessage}
          </p>
        ))}
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
