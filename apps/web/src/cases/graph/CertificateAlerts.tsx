import { useEffect, useState } from "react";

import { RequestAbortedError } from "../../api/client";
import { acknowledgeCtAlerts, listCtAlerts, type CtAlert } from "../../api/monitoring";
import { newThings } from "../certificateText";
import { describeCaseError } from "../errors";
import { RelativeTime } from "../RelativeTime";

/** How often unseen alerts are read again while the graph is open. */
export const ALERTS_POLL_MS = 60_000;
/** Names listed per alert before "and N more". */
const NAMES_SHOWN = 5;

function namesText(alert: CtAlert): string {
  const shown = alert.names.slice(0, NAMES_SHOWN);
  const more = alert.names_total - shown.length;
  return more > 0 ? `${shown.join(", ")} and ${String(more)} more` : shown.join(", ");
}

/**
 * The case's unseen Certificate Transparency alerts, over the graph. Editors
 * and owners mark them seen for everyone; "Show in graph" highlights what is new.
 */
export function CertificateAlerts({
  caseId,
  canAcknowledge,
  onShow,
}: {
  caseId: string;
  canAcknowledge: boolean;
  onShow: (keys: readonly string[]) => void;
}) {
  const [alerts, setAlerts] = useState<CtAlert[]>([]);
  const [unseen, setUnseen] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    listCtAlerts(caseId, { unseen: true, signal: controller.signal }).then(
      (list) => {
        setAlerts(list.items);
        setUnseen(list.unseen);
      },
      (caught: unknown) => {
        // Alerts are an extra: a failed read leaves the graph alone.
        if (caught instanceof RequestAbortedError) return;
      },
    );
    const timer = setTimeout(() => {
      setAttempt((n) => n + 1);
    }, ALERTS_POLL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [caseId, attempt]);

  if (alerts.length === 0) return null;

  const acknowledge = async (ids?: string[]) => {
    setError(null);
    try {
      await acknowledgeCtAlerts(caseId, ids);
      setAlerts((current) =>
        ids === undefined ? [] : current.filter((alert) => !ids.includes(alert.id)),
      );
      setUnseen((count) => (ids === undefined ? 0 : Math.max(0, count - ids.length)));
    } catch (caught) {
      setError(describeCaseError(caught));
    }
  };

  return (
    <section className="ct-alerts" aria-labelledby="ct-alerts-title">
      <div className="ct-alerts__head">
        <h2 id="ct-alerts-title" className="ct-alerts__title">
          New certificates
        </h2>
        {canAcknowledge && unseen > 1 && (
          <button
            type="button"
            className="button button--ghost button--small"
            onClick={() => {
              void acknowledge();
            }}
          >
            Mark all {String(unseen)} seen
          </button>
        )}
      </div>
      <ul className="ct-alerts__list">
        {alerts.map((alert) => (
          <li key={alert.id} className="ct-alert" aria-label={`Alert for ${alert.domain}`}>
            <p className="ct-alert__text">
              <strong>{alert.domain}</strong>: {newThings(alert.certificates, alert.names_total)}{" "}
              <span className="job__meta">
                (<RelativeTime iso={alert.created_at} />)
              </span>
              {alert.names.length > 0 && (
                <span className="ct-alert__names">{namesText(alert)}</span>
              )}
            </p>
            <div className="button-row">
              {alert.keys.length > 0 && (
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => {
                    onShow(alert.keys);
                  }}
                >
                  Show in graph
                </button>
              )}
              {canAcknowledge && (
                <button
                  type="button"
                  className="button button--ghost button--small"
                  aria-label={`Mark the ${alert.domain} alert seen`}
                  onClick={() => {
                    void acknowledge([alert.id]);
                  }}
                >
                  Mark seen
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {unseen > alerts.length && (
        <p className="job__meta">
          Showing the newest {String(alerts.length)} of {String(unseen)} unseen alerts.
        </p>
      )}
      <div className="form-error" role="alert">
        {error}
      </div>
    </section>
  );
}
