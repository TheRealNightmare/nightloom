import { describe, expect, it } from "vitest";

import { ENTITY_TYPES, ENTITY_TYPE_INFO, isEntityType } from "./entities";
import { formatConfidence, formatTimestamp } from "./format";

describe("formatTimestamp", () => {
  it("formats ISO timestamps in UTC", () => {
    expect(formatTimestamp("2026-11-02T09:30:00Z")).toBe("Nov 2, 2026, 9:30 AM UTC");
  });

  it("returns unparseable input unchanged", () => {
    expect(formatTimestamp("sometime")).toBe("sometime");
  });
});

describe("formatConfidence", () => {
  it.each([
    [0, "0%"],
    [0.856, "86%"],
    [1, "100%"],
    [1.5, "100%"],
    [-0.2, "0%"],
  ])("%d → %s", (value, expected) => {
    expect(formatConfidence(value)).toBe(expected);
  });
});

describe("entity types", () => {
  it("describes every type with a distinct color", () => {
    const colors = ENTITY_TYPES.map((type) => ENTITY_TYPE_INFO[type].color);
    expect(new Set(colors).size).toBe(ENTITY_TYPES.length);
    for (const color of colors) expect(color).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("recognizes valid type names", () => {
    expect(isEntityType("domain")).toBe(true);
    expect(isEntityType("Domain")).toBe(false);
  });
});
