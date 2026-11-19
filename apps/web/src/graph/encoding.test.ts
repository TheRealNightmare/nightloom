import { describe, expect, it } from "vitest";

import type { EntityNode, RelationEdge } from "../domain/entities";
import { GRAPH_PALETTES } from "../theme/graphPalette";
import { THEMES, token } from "../theme/tokens";
import {
  attachmentName,
  baseKnotSize,
  compactCount,
  CURVATURE,
  edgeSizeFor,
  FACE_FRACTION,
  FOCUS_THREAD_WIDTH,
  FOCUS_UNCERTAIN_OPACITY,
  focusThreadStyle,
  GROUP_MAX_SIZE,
  GROUP_MIN_SIZE,
  groupKnotSize,
  HALO_WIDTH,
  isUncertain,
  KNOT_MAX_SIZE,
  KNOT_MIN_SIZE,
  knotDisplaySize,
  knotSize,
  knotVariables,
  LABEL_FONT,
  LABEL_FONT_STRONG,
  LOW_CONFIDENCE_OPACITY,
  LOW_CONFIDENCE_WIDTH_FACTOR,
  nodeLabelStyle,
  OUTER_GAP,
  PIN_WIDTH,
  RING_WIDTH,
  SELECTED_SCALE,
  threadStyle,
  threadWidth,
  threadWidthStep,
} from "./encoding";

const prov = {
  source: "test",
  first_seen: "2026-01-01T00:00:00Z",
  last_seen: "2026-01-02T00:00:00Z",
  confidence: 1,
};
const entity: EntityNode = { id: "d", label: "example.org", type: "domain", ...prov };
const group: EntityNode = {
  id: "group:d|url",
  label: "312 URLs",
  type: "url",
  ...prov,
  group: { parent: "d", members: Array.from({ length: 312 }, (_, i) => `u${String(i)}`) },
};
const edge = (overrides: Partial<RelationEdge> = {}): RelationEdge => ({
  id: "e",
  from: "a",
  to: "b",
  relation: "RESOLVES_TO",
  ...prov,
  ...overrides,
});
const plain = { isGroup: false, selected: false, pinned: false };

describe("knot sizes", () => {
  it("grow with the degree within the min/max range", () => {
    expect(knotSize(0)).toBe(KNOT_MIN_SIZE);
    expect(knotSize(4)).toBeGreaterThan(knotSize(1));
    expect(knotSize(10_000)).toBe(KNOT_MAX_SIZE);
    expect(knotSize(-3)).toBe(KNOT_MIN_SIZE);
  });

  it("make groups larger, growing slowly with their size", () => {
    expect(groupKnotSize(1)).toBe(GROUP_MIN_SIZE);
    expect(groupKnotSize(26)).toBeLessThan(groupKnotSize(1000));
    expect(groupKnotSize(10 ** 9)).toBe(GROUP_MAX_SIZE);
    expect(groupKnotSize(26)).toBeGreaterThan(knotSize(3));
    expect(baseKnotSize(group, 1)).toBe(groupKnotSize(312));
    expect(baseKnotSize(entity, 9)).toBe(knotSize(9));
  });

  it("add the halo and the pinned ring outside the knot", () => {
    expect(knotDisplaySize(10, plain)).toBe(10);
    expect(knotDisplaySize(10, { ...plain, selected: true })).toBeCloseTo(
      10 * SELECTED_SCALE + HALO_WIDTH + OUTER_GAP,
    );
    expect(knotDisplaySize(10, { ...plain, pinned: true })).toBe(10 + PIN_WIDTH + OUTER_GAP);
  });
});

describe.each(THEMES)("knotVariables (%s theme)", (theme) => {
  const palette = GRAPH_PALETTES[theme];
  const typeColor = token(theme, "--type-domain");

  it("draws an entity as a surface disc with a type ring and center", () => {
    expect(knotVariables(typeColor, plain, palette)).toEqual({
      haloSize: 0,
      haloColor: token(theme, "--brass"),
      haloGap: 0,
      pinSize: 0,
      pinColor: token(theme, "--brass"),
      pinGap: 0,
      ringSize: RING_WIDTH,
      ringColor: typeColor,
      innerGap: 0,
      innerRingSize: 0,
      faceSize: FACE_FRACTION,
      faceColor: token(theme, "--surface"),
      centerColor: typeColor,
    });
  });

  it("gives groups a double ring and an empty face", () => {
    const knot = knotVariables(typeColor, { ...plain, isGroup: true }, palette);
    expect(knot.innerGap).toBeGreaterThan(0);
    expect(knot.innerRingSize).toBeGreaterThan(0);
    expect(knot.centerColor).toBe(token(theme, "--surface"));
  });

  it("adds a brass halo when selected and a brass ring when pinned", () => {
    const knot = knotVariables(typeColor, { ...plain, selected: true, pinned: true }, palette);
    expect(knot).toMatchObject({
      haloSize: HALO_WIDTH,
      haloGap: OUTER_GAP,
      pinSize: PIN_WIDTH,
      pinGap: OUTER_GAP,
    });
  });

  it("labels entities in --muted, the selection in --brass-text, groups by count", () => {
    expect(nodeLabelStyle(entity, false, palette)).toEqual({
      label: "example.org",
      labelColor: token(theme, "--muted"),
      labelFont: LABEL_FONT,
      labelSize: 12,
      labelPosition: "right",
    });
    expect(nodeLabelStyle(entity, true, palette)).toMatchObject({
      labelColor: token(theme, "--brass-text"),
      labelFont: LABEL_FONT_STRONG,
    });
    expect(nodeLabelStyle(group, false, palette)).toMatchObject({
      label: "312",
      labelColor: token(theme, "--text"),
      labelPosition: "over",
    });
  });

  it("draws threads in --hair and focused ones in --brass", () => {
    expect(threadStyle(false, 1, palette)).toEqual({
      color: token(theme, "--hair"),
      width: 1,
      opacity: 1,
      curvature: CURVATURE,
    });
    expect(focusThreadStyle(false, palette)).toEqual({
      color: token(theme, "--brass"),
      width: FOCUS_THREAD_WIDTH,
      opacity: 1,
      curvature: CURVATURE,
    });
  });
});

describe("threads", () => {
  it("are uncertain below 0.6 confidence or when not forward-confirmed", () => {
    expect(isUncertain(edge())).toBe(false);
    expect(isUncertain(edge({ confidence: 0.6 }))).toBe(false);
    expect(isUncertain(edge({ confidence: 0.59 }))).toBe(true);
    const note = (text: string) =>
      edge({
        relation: "HAS_PTR",
        details: {
          sources: [],
          observation_count: 1,
          admiralty: null,
          note: text,
          evidence_refs: [],
        },
      });
    expect(isUncertain(note("forward_confirmed: false. PTR not confirmed"))).toBe(true);
    expect(isUncertain(note("forward_confirmed: true (the name resolves back)"))).toBe(false);
  });

  it("are thinner and faint when uncertain, also when focused", () => {
    const palette = GRAPH_PALETTES.dark;
    expect(threadStyle(true, 1, palette)).toMatchObject({
      width: LOW_CONFIDENCE_WIDTH_FACTOR,
      opacity: LOW_CONFIDENCE_OPACITY,
    });
    expect(focusThreadStyle(true, palette).opacity).toBe(FOCUS_UNCERTAIN_OPACITY);
  });

  it("widen from 1 to 1.5 px as the camera zooms in", () => {
    expect(threadWidth(4)).toBe(1);
    expect(threadWidth(1)).toBe(1);
    expect(threadWidth(0.625)).toBeCloseTo(1.25);
    expect(threadWidth(0.25)).toBe(1.5);
    expect(threadWidth(0.08)).toBe(1.5);
  });

  it("repaint only when the rounded width changes", () => {
    expect(threadWidthStep(1)).toBe(threadWidthStep(0.98));
    expect(threadWidthStep(0.25)).toBe(1.5);
  });

  it("convert pixels to sigma's zoom-scaled edge size", () => {
    expect(edgeSizeFor(2, 1)).toBe(2);
    expect(edgeSizeFor(2, 0.25)).toBe(1);
    expect(edgeSizeFor(1, 0)).toBeGreaterThan(0);
  });
});

describe("labels", () => {
  it("shorten large group counts", () => {
    expect(compactCount(312)).toBe("312");
    expect(compactCount(999)).toBe("999");
    expect(compactCount(1000)).toBe("1k");
    expect(compactCount(1234)).toBe("1.2k");
    expect(compactCount(35_000)).toBe("35k");
  });

  it("name an attachment per theme", () => {
    expect(attachmentName("pin", "light")).toBe("pin:light");
  });
});
