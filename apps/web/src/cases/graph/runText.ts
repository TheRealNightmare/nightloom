import {
  PIVOT_MAX_DEPTH,
  PIVOT_MAX_ENTITIES,
  PIVOT_MAX_JOBS,
  type RunStatus,
  type ScanRun,
} from "../../api/scans";
import type { Job } from "../../api/shuttles";

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  running: "Running",
  finished: "Finished",
  cancelled: "Cancelled",
  stopped: "Stopped",
};

/** "7 of 12 done · 2 running · 3 waiting · 1 failed". */
export function runProgressText(run: ScanRun): string {
  const { succeeded, failed, cancelled, running, queued } = run.jobs;
  const parts = [`${String(succeeded + failed + cancelled)} of ${String(run.total)} done`];
  if (running > 0) parts.push(`${String(running)} running`);
  if (queued > 0) parts.push(`${String(queued)} queued`);
  if (run.waiting > 0) parts.push(`${String(run.waiting)} waiting`);
  if (failed > 0) parts.push(`${String(failed)} failed`);
  if (cancelled > 0) parts.push(`${String(cancelled)} cancelled`);
  return parts.join(" · ");
}

export type Entry =
  { kind: "job"; at: string; job: Job } | { kind: "run"; at: string; run: ScanRun };

/** Single jobs and profile runs, newest first; a run's jobs go inside it. */
export function panelEntries(
  jobs: readonly Job[],
  runs: readonly ScanRun[],
): { entries: Entry[]; byRun: Map<string, Job[]> } {
  const known = new Set(runs.map((run) => run.id));
  const byRun = new Map<string, Job[]>();
  const entries: Entry[] = runs.map((run) => ({ kind: "run", at: run.created_at, run }));
  for (const job of jobs) {
    const runId = job.run_id ?? null;
    if (runId !== null && known.has(runId)) {
      byRun.set(runId, [...(byRun.get(runId) ?? []), job]);
    } else {
      entries.push({ kind: "job", at: job.created_at, job });
    }
  }
  entries.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return { entries, byRun };
}

export const PIVOT_LABEL = "Pivot from here";
export const PIVOT_HINT = `Runs the profile, then runs it again on the domains, addresses and other infrastructure it finds: up to ${String(PIVOT_MAX_DEPTH)} hops, ${String(PIVOT_MAX_JOBS)} jobs and ${String(PIVOT_MAX_ENTITIES)} new entities. Never people or organizations.`;
export const PIVOT_INFRASTRUCTURE_HINT =
  "A pivot starts only from infrastructure, such as domains and IP addresses.";

/** "Pivot · 12 new entities · hop 1 of 2", or null for an ordinary run. */
export function pivotText(run: ScanRun): string | null {
  if (!run.pivot) return null;
  return [
    "Pivot",
    `${String(run.discovered)} new ${run.discovered === 1 ? "entity" : "entities"}`,
    `hop ${String(run.depth)} of ${String(run.max_depth)}`,
  ].join(" · ");
}

/** Why a pivot stopped adding entities, or null. */
export function pivotCappedText(run: ScanRun): string | null {
  if (run.capped === "jobs") {
    return `Reached the limit of ${String(run.max_jobs)} jobs, so it stopped adding entities.`;
  }
  if (run.capped === "entities") {
    return `Reached the limit of ${String(run.max_entities)} new entities, so it stopped adding more.`;
  }
  return null;
}

/** "Queued 5 of 7 jobs; the other 2 start as these finish. 3 skipped." */
export function runStartedText(run: ScanRun): string {
  const jobs = `${String(run.total)} job${run.total === 1 ? "" : "s"}`;
  const queued =
    run.released >= run.total
      ? `Queued ${jobs}.`
      : `Queued ${String(run.released)} of ${jobs}; the other ${String(run.total - run.released)} start as these finish.`;
  return run.skipped > 0 ? `${queued} ${String(run.skipped)} skipped.` : queued;
}
