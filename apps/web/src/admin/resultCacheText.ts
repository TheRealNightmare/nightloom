import type { ResultCacheScope } from "../api/resultCache";

/** What the configured scope means, at the top of the admin's result cache page. */
export const SCOPE_TEXT: Record<ResultCacheScope, string> = {
  case: "Each case keeps its own cached results, so one case never learns that another looked the same target up.",
  instance:
    "Cached results are shared by every case on this instance: a run in one case answers the same lookup in another.",
  off: "The result cache is off: every job asks its source. Set NIGHTLOOM_RESULT_CACHE_SCOPE to turn it on.",
};
