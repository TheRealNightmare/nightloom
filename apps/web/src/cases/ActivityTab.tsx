import { useEffect, useState } from "react";

import { can, listCaseAudit, listMembers, type AuditEvent, type Member } from "../api/cases";
import { RequestAbortedError } from "../api/client";
import { OutcomeBadge } from "./Badges";
import { useCase } from "./caseContext";
import { describeCaseError } from "./errors";
import { Forbidden, Loading } from "./Feedback";
import { actorName, describeEvent } from "./labels";
import { RelativeTime } from "./RelativeTime";

export const ACTIVITY_PAGE_SIZE = 50;

/** The case's audit events, newest first (owners and editors). */
export function ActivityTab() {
  const { case: current } = useCase();
  const allowed = can(current.my_role, "read_audit");
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    if (!allowed) return;
    const controller = new AbortController();
    const options = { signal: controller.signal };
    listCaseAudit(current.id, { limit: ACTIVITY_PAGE_SIZE }, options).then(
      (page) => {
        setEvents(page.items);
        setNextBefore(page.next_before_id);
      },
      (caught: unknown) => {
        if (!(caught instanceof RequestAbortedError)) setError(describeCaseError(caught));
      },
    );
    // Only for showing names instead of IDs; the list works without them.
    listMembers(current.id, options).then(setMembers, () => undefined);
    return () => {
      controller.abort();
    };
    // Reload when the case changes (a status change adds events).
  }, [allowed, current.id, current.updated_at]);

  if (!allowed) return <Forbidden />;

  const loadMore = async () => {
    if (nextBefore === null || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await listCaseAudit(current.id, {
        beforeId: nextBefore,
        limit: ACTIVITY_PAGE_SIZE,
      });
      setEvents((items) => [...(items ?? []), ...page.items]);
      setNextBefore(page.next_before_id);
    } catch (caught) {
      setError(describeCaseError(caught));
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <section aria-labelledby="activity-title" className="section">
      <h2 id="activity-title" className="section__title">
        Activity
      </h2>
      {events === null && error === null && <Loading label="Loading activity…" />}
      {events?.length === 0 && <p className="section__hint">No activity recorded yet.</p>}
      {events !== null && events.length > 0 && (
        <ol className="activity" aria-label="Case activity, newest first">
          {events.map((event) => (
            <li key={event.id} className="activity__item" data-outcome={event.outcome}>
              <p className="activity__text">
                <span className="activity__actor">{actorName(event)}</span>{" "}
                {describeEvent(event, members)}
              </p>
              <p className="activity__meta">
                <RelativeTime iso={event.occurred_at} />
                {event.outcome !== "success" && <OutcomeBadge outcome={event.outcome} />}
              </p>
            </li>
          ))}
        </ol>
      )}
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {nextBefore !== null && (
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
    </section>
  );
}
