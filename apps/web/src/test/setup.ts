import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

import { resetShuttleCache } from "../api/shuttles";

// findBy*/waitFor resolve as soon as the element appears; the default 1 s
// limit only matters on a busy machine (parallel suites, a shared CI runner),
// where a render can take longer and the test would fail for no real reason.
configure({ asyncUtilTimeout: 4_000 });

afterEach(() => {
  cleanup();
  // Each test serves its own Shuttles.
  resetShuttleCache();
});
