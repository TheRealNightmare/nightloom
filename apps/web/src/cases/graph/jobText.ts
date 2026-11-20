import { ConflictError, ForbiddenError, RateLimitedError } from "../../api/client";
import type { Job, JobStatus, ShuttleInfo } from "../../api/shuttles";
import { describeCaseError, FORBIDDEN_MESSAGE } from "../errors";

export const TOO_MANY_JOBS_MESSAGE =
  "You already have the maximum number of queued or running jobs. Wait for one to finish.";
export const ACTIVE_SHUTTLE_REASON =
  "Active Shuttles touch the target itself and cannot run until ownership verification is available.";
export const PERSON_SHUTTLE_REASON =
  "This case does not allow person-related entities, so person-related Shuttles cannot run.";

/**
 * What to show when starting or cancelling a job fails. The API's `403`,
 * `409` and `429` answers for jobs are fixed, user-safe messages.
 */
export function describeJobError(error: unknown): string {
  if (error instanceof ForbiddenError) return error.detail ?? FORBIDDEN_MESSAGE;
  if (error instanceof RateLimitedError) return error.detail ?? TOO_MANY_JOBS_MESSAGE;
  if (error instanceof ConflictError) return error.message;
  return describeCaseError(error);
}

/** Why a Shuttle cannot run in this case, or null if it can (the API checks again). */
export function shuttleBlockedReason(
  shuttle: ShuttleInfo,
  allowPersonEntities: boolean,
): string | null {
  if (shuttle.mode !== "passive") return ACTIVE_SHUTTLE_REASON;
  if (shuttle.sensitivity === "person" && !allowPersonEntities) return PERSON_SHUTTLE_REASON;
  return null;
}

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** "45 s", "3 min 05 s", "1 h 02 min". */
export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${String(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${String(minutes)} min ${String(seconds % 60).padStart(2, "0")} s`;
  return `${String(Math.floor(minutes / 60))} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/**
 * Time the job has been running (or waiting, while queued), or how long it
 * took. A running job's time stops at its deadline: the run cannot go on
 * past it, only the recording of its outcome can.
 */
export function elapsedMs(job: Job, now: number): number {
  const start = Date.parse(job.started_at ?? job.created_at);
  let end = job.finished_at ? Date.parse(job.finished_at) : now;
  if (!job.finished_at && job.status === "running" && job.deadline) {
    end = Math.min(end, Date.parse(job.deadline));
  }
  return Math.max(0, end - start);
}

export const OVERDUE_MESSAGE = "overdue — the worker may have stopped";

/** A job still running after the worker's hard time limit: its worker most likely died. */
export function isOverdue(job: Job, now: number): boolean {
  return job.status === "running" && job.overdue_at !== null && now > Date.parse(job.overdue_at);
}

/** "Created 4 · Updated 1 · Rejected 0" (entities and relationships together). */
export function statsText(job: Job): string {
  const { stats } = job;
  const created = stats.entities_created + stats.relationships_created;
  const updated = stats.entities_updated + stats.relationships_updated;
  return `Created ${String(created)} · Updated ${String(updated)} · Rejected ${String(stats.findings_rejected)}`;
}
