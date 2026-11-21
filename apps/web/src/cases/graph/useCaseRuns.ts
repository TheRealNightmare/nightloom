import { useCallback, useEffect, useState } from "react";

import { RequestAbortedError } from "../../api/client";
import { cancelRun, createRun, listRuns, type ScanRun } from "../../api/scans";
import { describeCaseError } from "../errors";
import { JOB_POLL_MS, usePageVisible } from "./useCaseJobs";

export interface CaseRuns {
  /** Newest first. */
  runs: ScanRun[];
  error: string | null;
  /** Starts a profile run; rejects with the API's error so the caller can show it. */
  start: (
    profileId: string,
    entityKeys: string[],
    options?: { fresh?: boolean },
  ) => Promise<ScanRun>;
  /** Cancels a run; rejects with the API's error. */
  cancel: (runId: string) => Promise<ScanRun>;
}

/** Replaces the runs `updates` has newer copies of; adds new ones on top. */
export function mergeRuns(list: readonly ScanRun[], updates: readonly ScanRun[]): ScanRun[] {
  const byId = new Map(updates.map((run) => [run.id, run]));
  const known = new Set(list.map((run) => run.id));
  return [
    ...updates.filter((run) => !known.has(run.id)),
    ...list.map((run) => byId.get(run.id) ?? run),
  ];
}

/**
 * A case's profile runs. While one is running (and the page is visible) the
 * runs are polled every {@link JOB_POLL_MS} and `refreshJobs` is called, so
 * the jobs a run queues as earlier ones finish show up in the Jobs panel.
 */
export function useCaseRuns(caseId: string, refreshJobs: () => Promise<void>): CaseRuns {
  const [runs, setRuns] = useState<ScanRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const visible = usePageVisible();

  useEffect(() => {
    const controller = new AbortController();
    listRuns(caseId, { signal: controller.signal }).then(
      (loaded) => {
        setRuns(loaded);
        setError(null);
      },
      (caught: unknown) => {
        if (!(caught instanceof RequestAbortedError)) setError(describeCaseError(caught));
      },
    );
    return () => {
      controller.abort();
    };
  }, [caseId]);

  const running = runs.some((run) => run.status === "running");

  useEffect(() => {
    if (!visible || !running) return;
    const controller = new AbortController();
    let busy = false;
    const timer = setInterval(() => {
      if (busy) return;
      busy = true;
      void Promise.allSettled([
        listRuns(caseId, { signal: controller.signal }).then((loaded) => {
          if (!controller.signal.aborted) setRuns(loaded);
        }),
        refreshJobs(),
      ]).finally(() => {
        busy = false;
      });
    }, JOB_POLL_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [caseId, running, visible, refreshJobs]);

  const start = useCallback(
    async (
      profileId: string,
      entityKeys: string[],
      { fresh = false }: { fresh?: boolean } = {},
    ) => {
      const run = await createRun(caseId, {
        profile_id: profileId,
        entity_keys: entityKeys,
        ...(fresh ? { fresh: true } : {}),
      });
      setRuns((previous) => mergeRuns(previous, [run]));
      await refreshJobs();
      return run;
    },
    [caseId, refreshJobs],
  );

  const cancel = useCallback(
    async (runId: string) => {
      const run = await cancelRun(caseId, runId);
      setRuns((previous) => mergeRuns(previous, [run]));
      await refreshJobs();
      return run;
    },
    [caseId, refreshJobs],
  );

  return { runs, error, start, cancel };
}
