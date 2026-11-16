import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, fetchReadiness, type Readiness, type RequestOptions } from "./client";

export const READINESS_POLL_MS = 15_000;

export type ReadinessView = Readiness | { kind: "checking" };

export interface UseReadinessOptions {
  intervalMs?: number;
  /** Injected in tests. */
  fetcher?: (options: RequestOptions) => Promise<Readiness>;
}

/**
 * Polls `/api/v1/ready`: once on mount, then every `intervalMs` after the
 * previous answer (requests never overlap). `recheck()` polls immediately.
 */
export function useReadiness({
  intervalMs = READINESS_POLL_MS,
  fetcher = fetchReadiness,
}: UseReadinessOptions = {}): { readiness: ReadinessView; recheck: () => void } {
  const [readiness, setReadiness] = useState<ReadinessView>({ kind: "checking" });
  const pollRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let disposed = false;

    const poll = () => {
      clearTimeout(timer);
      controller?.abort();
      const current = new AbortController();
      controller = current;
      fetcher({ signal: current.signal }).then(
        (result) => {
          if (disposed || current.signal.aborted) return;
          setReadiness(result);
          timer = setTimeout(poll, intervalMs);
        },
        (error: unknown) => {
          // Aborted by a newer poll or by unmounting: nothing to report.
          if (disposed || (error instanceof ApiError && error.kind === "aborted")) return;
          setReadiness({ kind: "offline", reason: "Status check failed" });
          timer = setTimeout(poll, intervalMs);
        },
      );
    };

    pollRef.current = poll;
    poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller?.abort();
      pollRef.current = null;
    };
  }, [fetcher, intervalMs]);

  const recheck = useCallback(() => {
    pollRef.current?.();
  }, []);

  return { readiness, recheck };
}
