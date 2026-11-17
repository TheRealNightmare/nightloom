import { describe, expect, it } from "vitest";

import { makeCase } from "../../test/caseFixtures";
import { graphSummaryText, readOnlyReason } from "./text";

describe("graph texts", () => {
  it("counts entities and relationships", () => {
    expect(graphSummaryText(0, 0)).toBe("0 entities, 0 relationships");
    expect(graphSummaryText(1, 1)).toBe("1 entity, 1 relationship");
    expect(graphSummaryText(2000, 4512)).toBe("2,000 entities, 4,512 relationships");
  });

  it("explains why a graph is read-only", () => {
    expect(readOnlyReason(makeCase({ my_role: "editor" }))).toBeNull();
    expect(readOnlyReason(makeCase({ my_role: "viewer" }))).toMatch(/view access/);
    expect(readOnlyReason(makeCase({ status: "closed" }))).toMatch(/closed/);
    expect(readOnlyReason(makeCase({ my_role: "viewer", status: "archived" }))).toMatch(/view/);
  });
});
