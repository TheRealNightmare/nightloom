import { describe, expect, it } from "vitest";

import { sectionTitle } from "./sectionTitle";

describe("sectionTitle", () => {
  it("names the case section in the path", () => {
    expect(sectionTitle("/cases/c1/graph", "c1")).toBe("Graph");
    expect(sectionTitle("/cases/c1/members/", "c1")).toBe("Members");
    expect(sectionTitle("/cases/c1/activity", "c1")).toBe("Activity");
    expect(sectionTitle("/cases/c1/settings", "c1")).toBe("Settings");
  });

  it("is null on the case itself and 'Page not found' elsewhere", () => {
    expect(sectionTitle("/cases/c1", "c1")).toBeNull();
    expect(sectionTitle("/cases/c1/nope/x", "c1")).toBe("Page not found");
    expect(sectionTitle("/elsewhere", "c1")).toBeNull();
  });
});
