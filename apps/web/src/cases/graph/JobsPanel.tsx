import { useEffect, useState } from "react";

import { isActive, type Job, type JobStatus } from "../../api/shuttles";
import { RelativeTime } from "../RelativeTime";
import {
  describeJobError,
  elapsedMs,
  formatDuration,
  JOB_STATUS_LABELS,
  statsText,
} from "./jobText";

export const NO_JOBS_MESSAGE =
  "No Shuttles have run in this case yet. Select an entity and choose a Shuttle in the side panel.";

export function JobStatusChip({ status }: { status: JobStatus }) {
  return (
    <span className="job-status" data-status={status}>
      {JOB_STATUS_LABELS[status]}
    </span>
  );
}

function useNow(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [ticking]);
  return now;
}

function statsTitle(job: Job): string {
  const { stats } = job;
  return (
    `Entities: ${String(stats.entities_created)} created, ${String(stats.entities_updated)} updated. ` +
    `Relationships: ${String(stats.relationships_created)} created, ` +
    `${String(stats.relationships_updated)} updated. ` +
    `Findings rejected: ${String(stats.findings_rejected)}.`
  );
}

/** `domain:example.org` → `example.org`. */
const valueOf = (key: string) => key.slice(key.indexOf(":") + 1);

function JobRow({
  job,
  canCancel,
  onCancel,
}: {
  job: Job;
  canCancel: boolean;
  onCancel: (jobId: string) => Promise<unknown>;
}) {
  const active = isActive(job);
  const now = useNow(active);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = `${job.shuttle_name} on ${valueOf(job.input_key)}`;

  const cancel = () => {
    setCancelling(true);
    setError(null);
    onCancel(job.id).then(
      () => {
        setCancelling(false);
      },
      (caught: unknown) => {
        setCancelling(false);
        setError(describeJobError(caught));
      },
    );
  };

  return (
    <li className="job" data-status={job.status} aria-label={label}>
      <div className="job__head">
        <JobStatusChip status={job.status} />
        <span className="job__title">
          <strong>{job.shuttle_name}</strong> <span className="table__muted">on</span>{" "}
          <code className="job__input">{valueOf(job.input_key)}</code>
        </span>
        {canCancel && active && (
          <button
            type="button"
            className="link-button job__cancel"
            disabled={cancelling || job.cancel_requested}
            onClick={cancel}
          >
            {job.cancel_requested ? "Cancelling…" : "Cancel"}
          </button>
        )}
      </div>
      <p className="job__meta">
        <span data-testid="job-elapsed">
          {job.status === "queued" ? "Waiting " : active ? "Running for " : "Took "}
          {formatDuration(elapsedMs(job, now))}
        </span>{" "}
        · started <RelativeTime iso={job.created_at} /> · v{job.shuttle_version}
      </p>
      {(job.status !== "queued" || job.stats.findings_rejected > 0) && (
        <p className="job__stats" title={statsTitle(job)}>
          {statsText(job)}
        </p>
      )}
      {job.error && <p className="job__error">{job.error}</p>}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}

export interface JobsPanelProps {
  jobs: Job[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  canCancel: boolean;
  onCancel: (jobId: string) => Promise<unknown>;
  onLoadMore: () => void;
  onClose: () => void;
}

/** The case's Shuttle jobs, newest first: live status, elapsed time, stats, errors. */
export function JobsPanel({
  jobs,
  loading,
  error,
  hasMore,
  loadingMore,
  canCancel,
  onCancel,
  onLoadMore,
  onClose,
}: JobsPanelProps) {
  return (
    <section className="jobs-panel" aria-labelledby="jobs-panel-title">
      <div className="jobs-panel__head">
        <h2 id="jobs-panel-title" className="jobs-panel__title">
          Jobs
        </h2>
        <button type="button" className="link-button" onClick={onClose}>
          Hide
        </button>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p className="loading" role="status">
          Loading jobs…
        </p>
      ) : jobs.length === 0 ? (
        <p className="jobs-panel__empty">{NO_JOBS_MESSAGE}</p>
      ) : (
        <ol className="jobs-list" aria-label="Job history">
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} canCancel={canCancel} onCancel={onCancel} />
          ))}
        </ol>
      )}
      {hasMore && (
        <button type="button" className="button" disabled={loadingMore} onClick={onLoadMore}>
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </section>
  );
}
