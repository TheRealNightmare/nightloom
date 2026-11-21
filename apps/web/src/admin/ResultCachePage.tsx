import { useEffect, useState } from "react";

import { RequestAbortedError } from "../api/client";
import {
  clearResultCache,
  formatBytes,
  getResultCache,
  type ResultCacheInfo,
} from "../api/resultCache";
import { useDocumentTitle } from "../app/documentTitle";
import { LoadError, Loading } from "../cases/Feedback";
import { formatLifetime } from "../cases/graph/jobText";
import { Modal } from "../components/Modal";
import { describeAdminError } from "./errors";
import { SCOPE_TEXT } from "./resultCacheText";

/** "1 entry", "3 entries", "2 jobs". */
const plural = (count: number, word: string) =>
  `${count.toLocaleString("en")} ${
    count === 1 ? word : word.endsWith("y") ? `${word.slice(0, -1)}ies` : `${word}s`
  }`;

/** What "Clear" will delete: everything, or one Shuttle's entries. */
interface Clearing {
  shuttle: string | null;
}

/** /admin/result-cache: what Shuttle results are cached, and clearing them. */
export function ResultCachePage() {
  useDocumentTitle("Result cache", "Admin");
  const [info, setInfo] = useState<ResultCacheInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [clearing, setClearing] = useState<Clearing | null>(null);
  // Bumped to load again (after "Try again", and after clearing).
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getResultCache({ signal: controller.signal }).then(
      (loaded) => {
        setInfo(loaded);
        setLoadError(null);
      },
      (caught: unknown) => {
        if (!(caught instanceof RequestAbortedError)) setLoadError(describeAdminError(caught));
      },
    );
    return () => {
      controller.abort();
    };
  }, [attempt]);

  const clear = async ({ shuttle }: Clearing) => {
    setClearing(null);
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const deleted = await clearResultCache(shuttle);
      setNotice(
        `Cleared ${plural(deleted, "cached result")}${shuttle === null ? "" : ` of ${shuttle}`}.`,
      );
      setAttempt((n) => n + 1);
    } catch (caught) {
      setError(describeAdminError(caught));
    } finally {
      setBusy(false);
    }
  };

  const rows = info?.shuttles ?? [];

  return (
    <main className="page">
      <div className="page__header">
        <h1 className="page__title">Result cache</h1>
        {info !== null && info.entries > 0 && (
          <button
            type="button"
            className="button button--danger"
            disabled={busy}
            onClick={() => {
              setClearing({ shuttle: null });
            }}
          >
            Clear all
          </button>
        )}
      </div>
      {info !== null && (
        <>
          <p className="section__hint">{SCOPE_TEXT[info.scope]}</p>
          <p className="table__muted" data-testid="result-cache-summary">
            {plural(info.entries, "entry")} · {formatBytes(info.size_bytes)} of{" "}
            {formatBytes(info.max_bytes)} · entries over {formatBytes(info.max_entry_bytes)} are not
            kept
          </p>
        </>
      )}
      {notice !== null && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {info === null && loadError !== null ? (
        <LoadError
          message={loadError}
          onRetry={() => {
            setLoadError(null);
            setAttempt((n) => n + 1);
          }}
        />
      ) : (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {info === null && loadError === null && <Loading label="Loading the result cache…" />}
      {info !== null && rows.length === 0 && (
        <div className="empty-state">
          <h2 className="empty-state__title">Nothing is cached yet</h2>
          <p className="empty-state__text">
            Results of Shuttles with a cache lifetime are kept here after a successful run.
          </p>
        </div>
      )}
      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="table" aria-label="Cached results by Shuttle">
            <thead>
              <tr>
                <th scope="col">Shuttle</th>
                <th scope="col">Kept for</th>
                <th scope="col">Entries</th>
                <th scope="col">Size</th>
                <th scope="col">Reused</th>
                <th scope="col" title="Jobs that succeeded in the last 30 days">
                  From cache (30 days)
                </th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.shuttle}>
                  <td>
                    <code>{row.shuttle}</code>
                  </td>
                  <td>
                    {row.cache_ttl_seconds === null
                      ? "Not installed"
                      : row.cache_ttl_seconds === 0
                        ? "Never cached"
                        : formatLifetime(row.cache_ttl_seconds)}
                  </td>
                  <td>{row.entries.toLocaleString("en")}</td>
                  <td>{formatBytes(row.size_bytes)}</td>
                  <td>{plural(row.hits, "time")}</td>
                  <td>
                    {row.jobs === 0
                      ? "No jobs"
                      : `${row.jobs_from_cache.toLocaleString("en")} of ${plural(row.jobs, "job")}`}
                  </td>
                  <td className="table__actions">
                    {row.entries > 0 && (
                      <button
                        type="button"
                        className="button button--ghost button--small"
                        aria-label={`Clear cached results of ${row.shuttle}`}
                        disabled={busy}
                        onClick={() => {
                          setClearing({ shuttle: row.shuttle });
                        }}
                      >
                        Clear
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {clearing !== null && (
        <Modal
          title={
            clearing.shuttle === null
              ? "Clear the whole result cache?"
              : `Clear the cached results of ${clearing.shuttle}?`
          }
          description="The next job for each entity asks its source again. Nothing in any case's graph changes."
          onClose={() => {
            setClearing(null);
          }}
        >
          <div className="dialog__actions">
            <button
              type="button"
              className="button"
              onClick={() => {
                setClearing(null);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button--danger"
              onClick={() => {
                void clear(clearing);
              }}
            >
              Clear
            </button>
          </div>
        </Modal>
      )}
    </main>
  );
}
