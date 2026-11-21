import { useEffect, useState, type ReactNode } from "react";

import { runLabel, type ScanRun } from "../../api/scans";
import { isActive, type Job, type JobStatus } from "../../api/shuttles";
import { RelativeTime } from "../RelativeTime";
import {
  describeJobError,
  elapsedMs,
  formatDuration,
  isOverdue,
  JOB_STATUS_LABELS,
  OVERDUE_MESSAGE,
  statsText,
} from "./jobText";
import { panelEntries, RUN_STATUS_LABELS, runProgressText } from "./runText";

export const FROM_CACHE_LABEL = "From cache";
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

/** Requests a job's cancellation; `error` is null or what went wrong. */
function useCancel(job: Job, onCancel: (jobId: string) => Promise<unknown>) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
  return { cancel, cancelling, error };
}

export interface RunningJobChipProps {
  job: Job;
  canCancel: boolean;
  onCancel: (jobId: string) => Promise<unknown>;
  /** Opens the Jobs panel. */
  onOpen: () => void;
}

/** The latest job still under way, over the graph: what runs, for how long, and Cancel. */
export function RunningJobChip({ job, canCancel, onCancel, onOpen }: RunningJobChipProps) {
  const now = useNow(true);
  const { cancel, cancelling, error } = useCancel(job, onCancel);
  const label = `${job.shuttle_name} on ${valueOf(job.input_key)}`;
  return (
    <div className="job-chip" data-status={job.status}>
      <span className="job-chip__dot" aria-hidden="true" />
      <button
        type="button"
        className="job-chip__label"
        aria-label={`${JOB_STATUS_LABELS[job.status]}: ${label}`}
        title="Show jobs"
        onClick={onOpen}
      >
        {job.shuttle_name} <span className="job-chip__input">{valueOf(job.input_key)}</span>
      </button>
      <span className="job-chip__timer" aria-hidden="true">
        {formatDuration(elapsedMs(job, now))}
      </span>
      {canCancel && (
        <button
          type="button"
          className="button button--ghost button--small"
          aria-label={`Cancel ${label}`}
          disabled={cancelling || job.cancel_requested}
          onClick={cancel}
        >
          {job.cancel_requested ? "Cancelling…" : "Cancel"}
        </button>
      )}
      {error && (
        <span className="job-chip__error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

/** Starts a fresh run of a cached job's Shuttle; `error` is null or what went wrong. */
function useRunFresh(job: Job, onRunFresh: ((job: Job) => Promise<unknown>) | undefined) {
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runFresh = () => {
    if (!onRunFresh) return;
    setRunning(true);
    setStarted(false);
    setError(null);
    onRunFresh(job).then(
      () => {
        setRunning(false);
        setStarted(true);
      },
      (caught: unknown) => {
        setRunning(false);
        setError(describeJobError(caught));
      },
    );
  };
  return { runFresh, running, started, error };
}

function JobRow({
  job,
  canCancel,
  onCancel,
  onRunFresh,
}: {
  job: Job;
  canCancel: boolean;
  onCancel: (jobId: string) => Promise<unknown>;
  onRunFresh: ((job: Job) => Promise<unknown>) | undefined;
}) {
  const active = isActive(job);
  const now = useNow(active);
  const { cancel, cancelling, error } = useCancel(job, onCancel);
  const fresh = useRunFresh(job, onRunFresh);
  const fetchedAt = job.from_cache === true ? (job.cached_fetched_at ?? null) : null;
  const label = `${job.shuttle_name} on ${valueOf(job.input_key)}`;
  const notes = job.stats.notes ?? [];

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
            className="button button--ghost button--small job__cancel"
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
      {fetchedAt !== null && (
        <p className="job__cached">
          <span>
            {FROM_CACHE_LABEL} · fetched <RelativeTime iso={fetchedAt} />
          </span>
          {onRunFresh && !active && (
            <button
              type="button"
              className="button button--ghost button--small"
              aria-label={`Run ${job.shuttle_name} fresh on ${valueOf(job.input_key)}`}
              disabled={fresh.running}
              onClick={fresh.runFresh}
            >
              {fresh.running ? "Starting…" : "Run fresh"}
            </button>
          )}
          {fresh.started && (
            <span className="shuttle-card__started" role="status">
              Queued.
            </span>
          )}
        </p>
      )}
      {fresh.error && (
        <p className="form-error" role="alert">
          {fresh.error}
        </p>
      )}
      {isOverdue(job, now) && (
        <p className="job__overdue" title="Past the worker's time limit; it will be failed soon.">
          {OVERDUE_MESSAGE}
        </p>
      )}
      {(job.status !== "queued" || job.stats.findings_rejected > 0) && (
        <p className="job__stats" title={statsTitle(job)}>
          {statsText(job)}
        </p>
      )}
      {notes.length > 0 && (
        <ul className="job__notes" aria-label="Notes">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
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

const STOP_REASONS: Record<string, string> = {
  case_not_open: "Stopped because the case is no longer open.",
  requester_not_allowed: "Stopped because its requester may no longer run Shuttles here.",
};

/** A profile run, with its progress and (on demand) its jobs. */
function RunRow({
  run,
  jobs,
  canCancel,
  onCancelRun,
  renderJob,
}: {
  run: ScanRun;
  jobs: readonly Job[];
  canCancel: boolean;
  onCancelRun: ((runId: string) => Promise<unknown>) | undefined;
  renderJob: (job: Job) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = runLabel(run);
  const done = run.jobs.succeeded + run.jobs.failed + run.jobs.cancelled;
  const cancel = () => {
    if (!onCancelRun) return;
    setCancelling(true);
    setError(null);
    onCancelRun(run.id).then(
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
    <li className="job job--run" data-status={run.status} aria-label={label}>
      <div className="job__head">
        <span
          className="job-status"
          data-status={run.status === "finished" ? "succeeded" : run.status}
        >
          {RUN_STATUS_LABELS[run.status]}
        </span>
        <span className="job__title">
          <strong>{run.profile_name}</strong> <span className="table__muted">on</span>{" "}
          {run.entities === 1 ? "1 entity" : `${String(run.entities)} entities`}
        </span>
        {canCancel && onCancelRun && run.status === "running" && (
          <button
            type="button"
            className="button button--ghost button--small job__cancel"
            aria-label={`Cancel ${label}`}
            disabled={cancelling}
            onClick={cancel}
          >
            {cancelling ? "Cancelling…" : "Cancel run"}
          </button>
        )}
      </div>
      <progress
        className="job__progress"
        value={done}
        max={Math.max(1, run.total)}
        aria-label={`${label}: ${String(done)} of ${String(run.total)} jobs done`}
      />
      <p className="job__stats" data-testid="run-progress">
        {runProgressText(run)}
      </p>
      <p className="job__meta">
        started <RelativeTime iso={run.created_at} />
        {run.skipped > 0 && ` · ${String(run.skipped)} skipped`}
        {!run.use_cache && " · fresh"}
      </p>
      {run.stop_reason !== null && (
        <p className="job__error">{STOP_REASONS[run.stop_reason] ?? "Stopped."}</p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {jobs.length > 0 && (
        <>
          <button
            type="button"
            className="link-button job__toggle"
            aria-expanded={open}
            onClick={() => {
              setOpen((value) => !value);
            }}
          >
            {open ? "Hide its jobs" : `Show its jobs (${String(jobs.length)})`}
          </button>
          {open && (
            <ol className="jobs-list jobs-list--nested" aria-label={`Jobs of ${label}`}>
              {jobs.map(renderJob)}
            </ol>
          )}
        </>
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
  /** Runs a cached job's Shuttle again, asking the source (omitted: no "Run fresh"). */
  onRunFresh?: (job: Job) => Promise<unknown>;
  /** The case's profile runs, newest first; their jobs are shown inside them. */
  runs?: readonly ScanRun[];
  /** Cancels a profile run (shown with `canCancel`). */
  onCancelRun?: (runId: string) => Promise<unknown>;
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
  onRunFresh,
  runs = [],
  onCancelRun,
  onLoadMore,
  onClose,
}: JobsPanelProps) {
  const { entries, byRun } = panelEntries(jobs, runs);
  const renderJob = (job: Job) => (
    <JobRow
      key={job.id}
      job={job}
      canCancel={canCancel}
      onCancel={onCancel}
      onRunFresh={onRunFresh}
    />
  );
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
      ) : entries.length === 0 ? (
        <p className="jobs-panel__empty">{NO_JOBS_MESSAGE}</p>
      ) : (
        <ol className="jobs-list" aria-label="Job history">
          {entries.map((entry) =>
            entry.kind === "job" ? (
              renderJob(entry.job)
            ) : (
              <RunRow
                key={entry.run.id}
                run={entry.run}
                jobs={byRun.get(entry.run.id) ?? []}
                canCancel={canCancel}
                onCancelRun={onCancelRun}
                renderJob={renderJob}
              />
            ),
          )}
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
