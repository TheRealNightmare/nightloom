import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { RequestAbortedError } from "../../api/client";
import {
  cancelJob,
  createJob,
  getJob,
  isActive,
  JOB_PAGE_SIZE,
  listJobs,
  type Job,
} from "../../api/shuttles";
import { describeCaseError } from "../errors";

/** How often queued and running jobs are polled while the page is visible. */
export const JOB_POLL_MS = 2000;

export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => {
      setVisible(document.visibilityState !== "hidden");
    };
    document.addEventListener("visibilitychange", update);
    return () => {
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  return visible;
}

/** Replaces the jobs in `list` that `updates` has newer copies of; adds new ones on top. */
export function mergeJobs(list: readonly Job[], updates: readonly Job[]): Job[] {
  const byId = new Map(updates.map((job) => [job.id, job]));
  const merged = list.map((job) => byId.get(job.id) ?? job);
  const known = new Set(list.map((job) => job.id));
  const added = updates.filter((job) => !known.has(job.id));
  return [...added, ...merged];
}

export interface CaseJobs {
  /** Newest first. */
  jobs: Job[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  activeCount: number;
  loadMore: () => void;
  /**
   * Queues a job (`fresh`: even if recent results are cached); rejects with
   * the API's error so the caller can show it.
   */
  start: (shuttle: string, entityKey: string, options?: { fresh?: boolean }) => Promise<Job>;
  /** Cancels a job; rejects with the API's error. */
  cancel: (jobId: string) => Promise<Job>;
  /**
   * Loads the newest page again and merges it in: picks up jobs queued
   * elsewhere (a profile run's next jobs). Errors are ignored.
   */
  refresh: () => Promise<void>;
}

/**
 * A case's jobs: the newest page, *Load more*, and live updates of queued and
 * running jobs (polled every {@link JOB_POLL_MS} while the page is visible;
 * nothing is polled once every job has finished or the tab is hidden).
 * `onFinished` is called once for each job seen going from active to finished.
 */
export function useCaseJobs(caseId: string, onFinished: (job: Job) => void): CaseJobs {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const visible = usePageVisible();
  const finishedRef = useRef(onFinished);
  const reported = useRef(new Set<string>());

  useEffect(() => {
    finishedRef.current = onFinished;
  }, [onFinished]);

  const report = useCallback((updates: readonly Job[]) => {
    for (const job of updates) {
      if (isActive(job) || reported.current.has(job.id)) continue;
      reported.current.add(job.id);
      finishedRef.current(job);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    reported.current = new Set();
    listJobs(caseId, {}, { signal: controller.signal }).then(
      (page) => {
        setJobs(page.items);
        setCursor(page.next_cursor);
        // Jobs that had already finished are history, not news.
        for (const job of page.items) if (!isActive(job)) reported.current.add(job.id);
        setError(null);
        setLoading(false);
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setError(describeCaseError(caught));
        setLoading(false);
      },
    );
    return () => {
      controller.abort();
    };
  }, [caseId]);

  const activeIds = useMemo(() => jobs.filter(isActive).map((job) => job.id), [jobs]);
  const activeKey = activeIds.join(",");

  useEffect(() => {
    if (!visible || activeKey === "") return;
    const ids = activeKey.split(",");
    const controller = new AbortController();
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      const results = await Promise.allSettled(
        ids.map((id) => getJob(caseId, id, { signal: controller.signal })),
      );
      busy = false;
      if (controller.signal.aborted) return;
      const updates = results.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : [],
      );
      if (updates.length === 0) return;
      setJobs((previous) => mergeJobs(previous, updates));
      report(updates);
    };
    const timer = setInterval(() => {
      void poll();
    }, JOB_POLL_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [caseId, activeKey, visible, report]);

  const loadMore = useCallback(() => {
    if (cursor === null || loadingMore) return;
    setLoadingMore(true);
    listJobs(caseId, { cursor, limit: JOB_PAGE_SIZE }).then(
      (page) => {
        setJobs((previous) => {
          const known = new Set(previous.map((job) => job.id));
          return [...previous, ...page.items.filter((job) => !known.has(job.id))];
        });
        for (const job of page.items) if (!isActive(job)) reported.current.add(job.id);
        setCursor(page.next_cursor);
        setLoadingMore(false);
      },
      (caught: unknown) => {
        setError(describeCaseError(caught));
        setLoadingMore(false);
      },
    );
  }, [caseId, cursor, loadingMore]);

  const start = useCallback(
    async (shuttle: string, entityKey: string, { fresh = false }: { fresh?: boolean } = {}) => {
      const job = await createJob(caseId, {
        shuttle,
        entity_key: entityKey,
        ...(fresh ? { fresh: true } : {}),
      });
      setJobs((previous) => mergeJobs(previous, [job]));
      report([job]);
      return job;
    },
    [caseId, report],
  );

  const cancel = useCallback(
    async (jobId: string) => {
      const job = await cancelJob(caseId, jobId);
      setJobs((previous) => mergeJobs(previous, [job]));
      report([job]);
      return job;
    },
    [caseId, report],
  );

  const refresh = useCallback(async () => {
    try {
      const page = await listJobs(caseId, {});
      setJobs((previous) => mergeJobs(previous, page.items));
      report(page.items);
    } catch {
      // The next poll tries again.
    }
  }, [caseId, report]);

  return {
    jobs,
    loading,
    error,
    hasMore: cursor !== null,
    loadingMore,
    activeCount: activeIds.length,
    loadMore,
    start,
    cancel,
    refresh,
  };
}
