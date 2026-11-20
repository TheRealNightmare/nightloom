import { describe, expect, it } from "vitest";

import {
  boxesOverlap,
  LABEL_GAP,
  labelBox,
  labelWidth,
  MAX_LABELS,
  selectLabels,
  type LabelCandidate,
} from "./labels3d";

const VIEWPORT = { width: 800, height: 600 };

function candidate(index: number, overrides: Partial<LabelCandidate> = {}): LabelCandidate {
  return {
    index,
    x: 100,
    y: 100,
    depth: 100,
    radius: 8,
    chars: 10,
    degree: 1,
    mandatory: false,
    centered: false,
    ...overrides,
  };
}

describe("label boxes", () => {
  it("sit to the right of the knot, or centered on a group", () => {
    const box = labelBox(candidate(0));
    expect(box.x).toBe(100 + 8 + LABEL_GAP);
    expect(box.width).toBe(labelWidth(10));
    const centered = labelBox(candidate(0, { centered: true }));
    expect(centered.x + centered.width / 2).toBeCloseTo(100, 6);
  });

  it("overlap when they touch within the gap", () => {
    const a = { x: 0, y: 0, width: 10, height: 10 };
    expect(boxesOverlap(a, { x: 12, y: 0, width: 10, height: 10 })).toBe(true);
    expect(boxesOverlap(a, { x: 20, y: 0, width: 10, height: 10 })).toBe(false);
    expect(boxesOverlap(a, { x: 0, y: 30, width: 10, height: 10 })).toBe(false);
  });
});

describe("label selection", () => {
  it("always labels hovered, selected and focused knots, even overlapping", () => {
    const placed = selectLabels(
      [candidate(0, { mandatory: true }), candidate(1, { mandatory: true, y: 102 })],
      VIEWPORT,
    );
    expect(placed.map((label) => label.index)).toEqual([0, 1]);
  });

  it("prefers well-connected knots and skips labels that would overlap", () => {
    const placed = selectLabels(
      [
        candidate(0, { degree: 1 }),
        candidate(1, { degree: 9, y: 104 }),
        candidate(2, { degree: 3, y: 300 }),
      ],
      VIEWPORT,
    );
    expect(placed.map((label) => label.index)).toEqual([1, 2]);
  });

  it("culls labels off screen and behind the camera", () => {
    const placed = selectLabels(
      [
        candidate(0, { x: -20 }),
        candidate(1, { y: 700 }),
        candidate(2, { depth: -3 }),
        candidate(3, { mandatory: true, x: 900 }),
        candidate(4),
      ],
      VIEWPORT,
    );
    expect(placed.map((label) => label.index)).toEqual([4]);
  });

  it("places at most MAX_LABELS optional labels", () => {
    const many = Array.from({ length: 400 }, (_, i) =>
      candidate(i, { x: (i % 20) * 40, y: Math.floor(i / 20) * 30, chars: 2 }),
    );
    const placed = selectLabels(many, VIEWPORT);
    expect(placed).toHaveLength(MAX_LABELS);
    for (let i = 0; i < placed.length; i += 1) {
      for (let j = i + 1; j < placed.length; j += 1) {
        const a = placed[i]?.box;
        const b = placed[j]?.box;
        if (a && b) expect(boxesOverlap(a, b)).toBe(false);
      }
    }
  });

  it("breaks degree ties by depth (nearer first)", () => {
    const placed = selectLabels(
      [candidate(0, { depth: 300 }), candidate(1, { depth: 50, y: 103 })],
      VIEWPORT,
    );
    expect(placed.map((label) => label.index)).toEqual([1]);
  });
});
