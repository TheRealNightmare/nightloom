import { useCallback, useEffect, useId, useState, type SubmitEvent } from "react";

import type { Case } from "../api/cases";
import { RequestAbortedError } from "../api/client";
import {
  addCtWatch,
  checkCtWatch,
  listCtWatches,
  removeCtWatch,
  type CtWatch,
} from "../api/monitoring";
import { Modal } from "../components/Modal";
import { CT_NOT_IN_GRAPH_HINT, CT_WATCHES_HINT, watchStatus } from "./certificateText";
import { describeCaseError } from "./errors";
import { LoadError } from "./Feedback";
import { RelativeTime } from "./RelativeTime";

/** How often the list is read again while a check is queued or running. */
export const WATCH_POLL_MS = 10_000;

function badge(item: CtWatch): { status: string; label: string } {
  if (item.checking) return { status: "running", label: "Checking" };
  if (item.problem !== null) return { status: "failed", label: "Needs attention" };
  if (item.baseline_at === null) return { status: "queued", label: "Starting" };
  return { status: "succeeded", label: "Watching" };
}

/** Owners: domains whose new certificates the worker looks for every hour. */
export function CertificateWatches({ value }: { value: Case }) {
  const id = useId();
  const open = value.status === "open";
  const [items, setItems] = useState<CtWatch[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [domain, setDomain] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<CtWatch | null>(null);

  const merge = useCallback((next: CtWatch) => {
    setItems((current) => {
      const others = (current ?? []).filter((item) => item.id !== next.id);
      return [...others, next].sort((a, b) => a.domain.localeCompare(b.domain));
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listCtWatches(value.id, { signal: controller.signal }).then(
      (loaded) => {
        setItems(loaded);
        setLoadError(null);
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setLoadError(describeCaseError(caught));
      },
    );
    return () => {
      controller.abort();
    };
  }, [value.id, attempt]);

  // Follow a check under way, or a new watch waiting for its first one.
  const busy = items?.some((item) => item.checking || item.checked_at === null) ?? false;
  useEffect(() => {
    if (!busy) return;
    const timer = setTimeout(() => {
      setAttempt((n) => n + 1);
    }, WATCH_POLL_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [busy, items]);

  const add = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    if (domain.trim() === "") {
      setError("Enter a domain, such as example.org.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      merge(await addCtWatch(value.id, domain.trim()));
      setDomain("");
    } catch (caught) {
      setError(describeCaseError(caught));
    } finally {
      setPending(false);
    }
  };

  const check = async (item: CtWatch) => {
    setError(null);
    try {
      merge(await checkCtWatch(value.id, item.id));
    } catch (caught) {
      setError(describeCaseError(caught));
    }
  };

  const remove = async (item: CtWatch) => {
    setRemoving(null);
    setError(null);
    try {
      await removeCtWatch(value.id, item.id);
      setItems((current) => (current ?? []).filter((other) => other.id !== item.id));
    } catch (caught) {
      setError(describeCaseError(caught));
    }
  };

  return (
    <section aria-labelledby={`${id}-title`} className="section">
      <h2 id={`${id}-title`} className="section__title">
        Certificate monitoring
      </h2>
      <p className="section__hint">{CT_WATCHES_HINT}</p>
      {open && (
        <form
          noValidate
          className="verification__add"
          aria-label="Watch a domain for new certificates"
          onSubmit={(event) => {
            void add(event);
          }}
        >
          <div className="field">
            <label className="field__label" htmlFor={`${id}-domain`}>
              Domain to watch
            </label>
            <input
              id={`${id}-domain`}
              className="field__input"
              value={domain}
              placeholder="example.org"
              autoComplete="off"
              spellCheck={false}
              aria-describedby={`${id}-domain-hint`}
              onChange={(event) => {
                setDomain(event.target.value);
              }}
            />
            <p id={`${id}-domain-hint`} className="field__hint">
              {CT_NOT_IN_GRAPH_HINT}
            </p>
          </div>
          <div className="button-row">
            <button type="submit" className="button" disabled={pending}>
              {pending ? "Adding…" : "Watch domain"}
            </button>
          </div>
        </form>
      )}
      <div className="form-error" role="alert">
        {error}
      </div>
      {items === null && loadError !== null && (
        <LoadError
          message={loadError}
          onRetry={() => {
            setLoadError(null);
            setAttempt((n) => n + 1);
          }}
        />
      )}
      {items !== null && items.length === 0 && (
        <p className="table__muted">No domains are watched in this case.</p>
      )}
      {items !== null && items.length > 0 && (
        <ul className="verification__list" aria-label="Watched domains">
          {items.map((item) => {
            const state = badge(item);
            return (
              <li key={item.id} className="verification" aria-label={item.domain}>
                <div className="verification__head">
                  <strong className="verification__domain">{item.domain}</strong>
                  <span className="job-status" data-status={state.status}>
                    {state.label}
                  </span>
                </div>
                <p className="job__meta">
                  {watchStatus(item)}
                  {item.checked_at !== null && (
                    <>
                      {" "}
                      Last checked <RelativeTime iso={item.checked_at} />.
                    </>
                  )}
                </p>
                <div className="button-row">
                  {open && (
                    <button
                      type="button"
                      className="button button--small"
                      disabled={item.checking}
                      aria-label={`Check ${item.domain} now`}
                      onClick={() => {
                        void check(item);
                      }}
                    >
                      Check now
                    </button>
                  )}
                  <button
                    type="button"
                    className="button button--ghost button--small"
                    aria-label={`Stop watching ${item.domain}`}
                    onClick={() => {
                      setRemoving(item);
                    }}
                  >
                    Stop watching
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {removing !== null && (
        <Modal
          title={`Stop watching ${removing.domain}?`}
          description="No more hourly checks. Its alerts and what it found stay in the case."
          onClose={() => {
            setRemoving(null);
          }}
        >
          <div className="dialog__actions">
            <button
              type="button"
              className="button"
              onClick={() => {
                setRemoving(null);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                void remove(removing);
              }}
            >
              Stop watching
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
