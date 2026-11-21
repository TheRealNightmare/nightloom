import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

import { resetProfileCache } from "../api/scans";
import { resetShuttleCache } from "../api/shuttles";

// jsdom has no WebGL. sigma's rendering helpers read WebGL2's type constants
// when they are imported (sigma itself is mocked where a test draws a graph),
// so give them the constants, with the values every browser uses.
if (!("WebGL2RenderingContext" in globalThis)) {
  Object.defineProperty(globalThis, "WebGL2RenderingContext", {
    configurable: true,
    value: {
      BYTE: 0x1400,
      UNSIGNED_BYTE: 0x1401,
      SHORT: 0x1402,
      UNSIGNED_SHORT: 0x1403,
      INT: 0x1404,
      UNSIGNED_INT: 0x1405,
      FLOAT: 0x1406,
      BOOL: 0x8b56,
      TRIANGLE_STRIP: 0x0005,
    },
  });
}

// findBy*/waitFor resolve as soon as the element appears; the default 1 s
// limit only matters on a busy machine (parallel suites, a shared CI runner),
// where a render can take longer and the test would fail for no real reason.
configure({ asyncUtilTimeout: 4_000 });

afterEach(() => {
  cleanup();
  // Each test serves its own Shuttles.
  resetShuttleCache();
  resetProfileCache();
});
