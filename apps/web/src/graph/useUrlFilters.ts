import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { filtersFromParams, filtersToParams, type GraphFilters } from "./filters";

export type FiltersUpdate = (change: (current: GraphFilters) => GraphFilters) => void;

/**
 * Graph filters kept in the page's query string, so a filtered view can be
 * shared as a link and survives a reload. Updates replace the history entry
 * (moving a slider must not fill the back button). Other query parameters
 * are left as they are.
 */
export function useUrlFilters(): [GraphFilters, FiltersUpdate] {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(params), [params]);
  const update = useCallback<FiltersUpdate>(
    (change) => {
      setParams((current) => filtersToParams(change(filtersFromParams(current)), current), {
        replace: true,
      });
    },
    [setParams],
  );
  return [filters, update];
}

/** The current time in epoch milliseconds, refreshed every `intervalMs` while `live`. */
export function useNow(live: boolean, intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const update = () => {
      setNow(Date.now());
    };
    // Right away (the value may be old when the filter is switched on), then regularly.
    const first = setTimeout(update, 0);
    const timer = setInterval(update, intervalMs);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [live, intervalMs]);
  return now;
}
