import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { resetShuttleCache } from "../api/shuttles";

afterEach(() => {
  cleanup();
  // Each test serves its own Shuttles.
  resetShuttleCache();
});
