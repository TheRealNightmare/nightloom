import { describe, expect, it } from "vitest";

import {
  classifyWheel,
  GESTURE_GAP_MS,
  isWheelSetting,
  pinchFactor,
  pixelDeltas,
  wheelAction,
  WheelClassifier,
  type WheelSample,
} from "./wheel";

const sample = (overrides: Partial<WheelSample>): WheelSample => ({
  deltaX: 0,
  deltaY: 0,
  deltaMode: 0,
  ctrlKey: false,
  time: 0,
  ...overrides,
});

/** A stream of events 16 ms apart, starting after a long pause. */
function classifyAll(samples: Partial<WheelSample>[]) {
  const classifier = new WheelClassifier();
  return samples.map((s, i) => classifier.classify(sample({ time: 10_000 + i * 16, ...s })));
}

// Recorded from real devices (deltas as browsers report them).
const MOUSE_SAMPLES: Record<string, Partial<WheelSample>> = {
  "Chrome, Windows": { deltaY: 100 },
  "Chrome, Linux": { deltaY: -120 },
  "Firefox, lines": { deltaY: 3, deltaMode: 1 },
  "Firefox, pixels": { deltaY: 48 },
  "Safari, macOS notch": { deltaY: -40 },
};
const TRACKPAD_SAMPLES: Record<string, Partial<WheelSample>> = {
  "Chrome, macOS": { deltaY: 4.5 },
  "Safari, macOS": { deltaY: -2 },
  "diagonal swipe": { deltaX: 3, deltaY: 7 },
  "horizontal swipe": { deltaX: -12, deltaY: 0 },
  "Windows precision touchpad": { deltaY: 13.333333 },
};

describe("wheel heuristic", () => {
  it.each(Object.entries(MOUSE_SAMPLES))("treats %s as a mouse wheel", (_name, s) => {
    expect(classifyWheel(sample(s), null)).toBe("mouse");
  });

  it.each(Object.entries(TRACKPAD_SAMPLES))("treats %s as a trackpad", (_name, s) => {
    expect(classifyWheel(sample(s), null)).toBe("trackpad");
  });

  it("treats Ctrl+wheel (a pinch) as a pinch, whatever came before", () => {
    expect(classifyWheel(sample({ ctrlKey: true, deltaY: 3.2 }), null)).toBe("pinch");
    expect(classifyWheel(sample({ ctrlKey: true, deltaY: 100 }), { kind: "mouse", time: 0 })).toBe(
      "pinch",
    );
  });

  it("keeps one meaning for a whole gesture", () => {
    // A trackpad momentum scroll ends in large round deltas: still a pan.
    expect(
      classifyAll([{ deltaY: 2.5 }, { deltaY: 30 }, { deltaY: 120 }, { deltaY: 100 }]),
    ).toEqual(["trackpad", "trackpad", "trackpad", "trackpad"]);
    // A fast-spun wheel with an odd small delta in the middle: still zooms.
    expect(classifyAll([{ deltaY: 100 }, { deltaY: 4 }, { deltaY: 100 }])).toEqual([
      "mouse",
      "mouse",
      "mouse",
    ]);
  });

  it("starts afresh after a pause", () => {
    const classifier = new WheelClassifier();
    expect(classifier.classify(sample({ deltaY: 2, time: 0 }))).toBe("trackpad");
    expect(classifier.classify(sample({ deltaY: 100, time: GESTURE_GAP_MS + 1 }))).toBe("mouse");
    classifier.reset();
    expect(classifier.classify(sample({ deltaY: 100, time: GESTURE_GAP_MS + 2 }))).toBe("mouse");
  });

  it("does not carry a pinch over to the next scroll", () => {
    const previous = { kind: "pinch", time: 0 } as const;
    expect(classifyWheel(sample({ deltaY: 3, time: 10 }), previous)).toBe("trackpad");
  });

  it("maps device kinds to actions, honouring the user's setting", () => {
    expect(wheelAction("mouse", "auto")).toBe("zoom");
    expect(wheelAction("trackpad", "auto")).toBe("pan");
    expect(wheelAction("trackpad", "zoom")).toBe("zoom");
    expect(wheelAction("mouse", "pan")).toBe("pan");
    expect(wheelAction("pinch", "pan")).toBe("pinch");
  });

  it("converts lines and pages to pixels", () => {
    expect(pixelDeltas({ deltaX: 1, deltaY: 3, deltaMode: 1 })).toEqual({ dx: 16, dy: 48 });
    expect(pixelDeltas({ deltaX: 0, deltaY: 1, deltaMode: 2 })).toEqual({ dx: 0, dy: 800 });
    expect(pixelDeltas({ deltaX: 2, deltaY: 5, deltaMode: 0 })).toEqual({ dx: 2, dy: 5 });
  });

  it("zooms smoothly and within bounds on a pinch", () => {
    expect(pinchFactor(0)).toBe(1);
    expect(pinchFactor(10)).toBeGreaterThan(1);
    expect(pinchFactor(-10)).toBeLessThan(1);
    expect(pinchFactor(10_000)).toBe(pinchFactor(50));
  });

  it("recognises stored settings", () => {
    expect(isWheelSetting("pan")).toBe(true);
    expect(isWheelSetting("sideways")).toBe(false);
    expect(isWheelSetting(null)).toBe(false);
  });
});
