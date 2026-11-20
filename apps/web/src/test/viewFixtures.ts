import type { ApiViewState, SavedView, ViewSummary } from "../api/views";
import { TEST_USER } from "./fakeApi";
import { OTHER_USER } from "./caseFixtures";

/** A version-1 state using every filter. */
export const STATE: ApiViewState = {
  version: 1,
  mode: "2d",
  camera: { x: 1, y: 2, ratio: 0.5, angle: 0.25 },
  filters: {
    hidden_types: ["url"],
    hidden_sources: ["dns"],
    hidden_sensitivities: [],
    seen: { kind: "range", from: "2026-11-01", to: null },
    min_confidence: 0.5,
    min_reliability: "B",
    min_credibility: 3,
    focus: { key: "domain:example.org", hops: 2 },
    hide_isolated: true,
  },
  groups: { expanded: ["group:domain:example.org|url"] },
  positions: { "domain:example.org": [1, 2] },
};

export function makeSummary(overrides: Partial<ViewSummary> = {}): ViewSummary {
  return {
    id: "0d3e9f2a-1b2c-4d5e-8f90-a1b2c3d4e5f6",
    name: "Overview",
    created_by: { id: TEST_USER.id, display_name: TEST_USER.display_name },
    updated_by: { id: TEST_USER.id, display_name: TEST_USER.display_name },
    created_at: "2026-11-20T10:00:00Z",
    updated_at: "2026-11-20T11:00:00Z",
    ...overrides,
  };
}

export function makeView(overrides: Partial<SavedView> = {}): SavedView {
  return { ...makeSummary(), state: STATE, ...overrides };
}

export const OTHER_AUTHOR = { id: OTHER_USER.id, display_name: OTHER_USER.display_name };
