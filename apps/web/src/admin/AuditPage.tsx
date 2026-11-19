import { useEffect, useId, useState } from "react";

import {
  AUDIT_ACTIONS,
  AUDIT_OUTCOMES,
  listAudit,
  listUsers,
  verifyAudit,
  type AuditAction,
  type AuditQuery,
  type VerifyResult,
} from "../api/admin";
import type { AuditEvent, AuditOutcome } from "../api/cases";
import { RequestAbortedError, type User } from "../api/client";
import { OutcomeBadge } from "../cases/Badges";
import { Loading } from "../cases/Feedback";
import { RelativeTime } from "../cases/RelativeTime";
import { Modal } from "../components/Modal";
import { formatTimestamp } from "../domain/format";
import {
  NO_FILTERS,
  OUTCOME_LABELS,
  toAuditQuery,
  verifyMessage,
  type AuditFilterValues,
} from "./auditFilters";
import { ACTION_LABELS, auditActor, describeAuditEvent } from "./auditLabels";
import { describeAdminError } from "./errors";

export const AUDIT_PAGE_SIZE = 50;

interface Listing {
  key: string;
  items: AuditEvent[];
  nextBefore: number | null;
}

/** /admin/audit: the whole audit log, newest first, with filters and verification. */
export function AuditPage() {
  const [values, setValues] = useState<AuditFilterValues>(NO_FILTERS);
  const [users, setUsers] = useState<User[]>([]);
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const [verify, setVerify] = useState<
    | { state: "idle" }
    | { state: "running" }
    | { state: "done"; result: VerifyResult }
    | {
        state: "error";
        message: string;
      }
  >({ state: "idle" });
  const ids = {
    action: useId(),
    outcome: useId(),
    actor: useId(),
    caseId: useId(),
    from: useId(),
    to: useId(),
  };

  const converted = toAuditQuery(values);
  const key = JSON.stringify(converted.query);
  const current = listing?.key === key ? listing : null;
  const errorMessage = converted.error ?? (error?.key === key ? error.message : null);

  useEffect(() => {
    // Only for the actor filter and names; the log works without them.
    const controller = new AbortController();
    listUsers({ signal: controller.signal }).then(setUsers, () => undefined);
    return () => {
      controller.abort();
    };
  }, []);

  useEffect(() => {
    const parsed: unknown = JSON.parse(key);
    if (parsed === null) return;
    const query = parsed as AuditQuery;
    const controller = new AbortController();
    listAudit({ ...query, limit: AUDIT_PAGE_SIZE }, { signal: controller.signal }).then(
      (page) => {
        setListing({ key, items: page.items, nextBefore: page.next_before_id });
      },
      (caught: unknown) => {
        if (!(caught instanceof RequestAbortedError)) {
          setError({ key, message: describeAdminError(caught) });
        }
      },
    );
    return () => {
      controller.abort();
    };
  }, [key]);

  const loadMore = async () => {
    if (current?.nextBefore == null || converted.query === null || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await listAudit({
        ...converted.query,
        limit: AUDIT_PAGE_SIZE,
        beforeId: current.nextBefore,
      });
      setListing({
        key,
        items: [...current.items, ...page.items],
        nextBefore: page.next_before_id,
      });
    } catch (caught) {
      setError({ key, message: describeAdminError(caught) });
    } finally {
      setLoadingMore(false);
    }
  };

  const runVerify = async () => {
    setVerify({ state: "running" });
    try {
      setVerify({ state: "done", result: await verifyAudit() });
    } catch (caught) {
      setVerify({ state: "error", message: describeAdminError(caught) });
    }
  };

  const set = <K extends keyof AuditFilterValues>(name: K, value: AuditFilterValues[K]) => {
    setValues((previous) => ({ ...previous, [name]: value }));
  };
  const filtered = JSON.stringify(values) !== JSON.stringify(NO_FILTERS);

  return (
    <main className="page">
      <div className="page__header">
        <h1 className="page__title">Audit log</h1>
        <button
          type="button"
          className="button"
          disabled={verify.state === "running"}
          onClick={() => {
            void runVerify();
          }}
        >
          {verify.state === "running" ? "Verifying…" : "Verify chain"}
        </button>
      </div>
      <div aria-live="polite">
        {verify.state === "done" && (
          <p
            className={verify.result.ok ? "notice" : "verify-result verify-result--bad"}
            role="status"
          >
            {verifyMessage(verify.result)}
          </p>
        )}
        {verify.state === "error" && (
          <p className="form-error" role="alert">
            {verify.message}
          </p>
        )}
      </div>

      <div className="filters filters--audit" role="search">
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.action}>
            Action
          </label>
          <select
            id={ids.action}
            className="field__input"
            value={values.action}
            onChange={(event) => {
              set("action", event.target.value as AuditAction | "");
            }}
          >
            <option value="">All</option>
            {AUDIT_ACTIONS.map((action) => (
              <option key={action} value={action}>
                {ACTION_LABELS[action]}
              </option>
            ))}
          </select>
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.outcome}>
            Outcome
          </label>
          <select
            id={ids.outcome}
            className="field__input"
            value={values.outcome}
            onChange={(event) => {
              set("outcome", event.target.value as AuditOutcome | "");
            }}
          >
            <option value="">All</option>
            {AUDIT_OUTCOMES.map((outcome) => (
              <option key={outcome} value={outcome}>
                {OUTCOME_LABELS[outcome]}
              </option>
            ))}
          </select>
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.actor}>
            Actor
          </label>
          <select
            id={ids.actor}
            className="field__input"
            value={values.actor}
            onChange={(event) => {
              set("actor", event.target.value);
            }}
          >
            <option value="">Anyone</option>
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name} ({user.email})
              </option>
            ))}
          </select>
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.caseId}>
            Case ID
          </label>
          <input
            id={ids.caseId}
            className="field__input"
            spellCheck={false}
            autoComplete="off"
            maxLength={36}
            placeholder="00000000-0000-…"
            value={values.caseId}
            onChange={(event) => {
              set("caseId", event.target.value);
            }}
          />
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.from}>
            From (UTC)
          </label>
          <input
            id={ids.from}
            type="date"
            className="field__input"
            value={values.from}
            onChange={(event) => {
              set("from", event.target.value);
            }}
          />
        </div>
        <div className="field field--inline">
          <label className="field__label" htmlFor={ids.to}>
            To (UTC)
          </label>
          <input
            id={ids.to}
            type="date"
            className="field__input"
            value={values.to}
            onChange={(event) => {
              set("to", event.target.value);
            }}
          />
        </div>
        {filtered && (
          <button
            type="button"
            className="button filters__clear"
            onClick={() => {
              setValues(NO_FILTERS);
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      {current === null && errorMessage === null && <Loading label="Loading audit log…" />}
      {current?.items.length === 0 && (
        <div className="empty-state">
          <h2 className="empty-state__title">{filtered ? "No matching events" : "No events"}</h2>
        </div>
      )}
      {current !== null && current.items.length > 0 && (
        <div className="table-scroll">
          <table className="table audit-table" aria-label="Audit events, newest first">
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Actor</th>
                <th scope="col">Event</th>
                <th scope="col">Outcome</th>
                <th scope="col">
                  <span className="visually-hidden">Details</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {current.items.map((event) => (
                <tr key={event.id} data-outcome={event.outcome}>
                  <td className="audit-table__time">
                    <RelativeTime iso={event.occurred_at} />
                  </td>
                  <td className="table__email">{auditActor(event)}</td>
                  <td>
                    {describeAuditEvent(event, users)}
                    <span className="audit-table__action"> {event.action}</span>
                  </td>
                  <td>
                    <OutcomeBadge outcome={event.outcome} />
                  </td>
                  <td className="table__actions">
                    <button
                      type="button"
                      className="button button--small"
                      aria-label={`Details of event ${event.id}`}
                      onClick={() => {
                        setSelected(event);
                      }}
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {errorMessage !== null && (
        <p className="form-error" role="alert">
          {errorMessage}
        </p>
      )}
      {current?.nextBefore != null && (
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
      {selected !== null && (
        <EventDetails
          event={selected}
          users={users}
          onClose={() => {
            setSelected(null);
          }}
        />
      )}
    </main>
  );
}

function EventDetails({
  event,
  users,
  onClose,
}: {
  event: AuditEvent;
  users: readonly User[];
  onClose: () => void;
}) {
  const rows: [string, string][] = [
    [
      "Action",
      `${(ACTION_LABELS as Record<string, string | undefined>)[event.action] ?? event.action} (${event.action})`,
    ],
    ["Outcome", OUTCOME_LABELS[event.outcome]],
    ["When", formatTimestamp(event.occurred_at)],
    ["Actor", event.actor_label ?? "—"],
    ["Actor ID", event.actor_user_id ?? "—"],
    ["IP address", event.actor_ip ?? "—"],
    ["Request ID", event.request_id],
    [
      "Target",
      event.target_type === null ? "—" : `${event.target_type} ${event.target_id ?? ""}`.trim(),
    ],
    ["Case ID", event.case_id ?? "—"],
  ];
  return (
    <Modal
      title={`Event #${event.id}`}
      description={`${auditActor(event)} ${describeAuditEvent(event, users)}`}
      onClose={onClose}
      drawer
    >
      <dl className="event-details">
        {rows.map(([term, value]) => (
          <div key={term} className="event-details__row">
            <dt>{term}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <h3 className="section__subtitle">Details</h3>
      <pre className="event-details__json" aria-label="Event details as JSON">
        {JSON.stringify(event.details, null, 2)}
      </pre>
      <div className="dialog__actions">
        <button type="button" className="button button--primary" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
