import { describe, expect, it } from "vitest";

import {
  blendDepth,
  DEPTH_ANIMATION_MS,
  depthAnimationMs,
  depthOf,
  depthSpan,
  MIN_DEPTH_SPAN,
  parseSeen,
  timeDepth,
  timeTicks,
  typeDepth,
  UNDATED_GAP_RATIO,
  UNDATED_LABEL,
  type DepthInput,
} from "./depth";

const node = (firstSeen: string, type: DepthInput["type"] = "domain"): DepthInput => ({
  type,
  firstSeen,
});

describe("time depth", () => {
  it("puts the oldest at the back and the newest at the front of the range", () => {
    const { z } = timeDepth(
      [node("2026-01-01T00:00:00Z"), node("2026-07-02T12:00:00Z"), node("2026-12-31T00:00:00Z")],
      200,
    );
    expect(z[0]).toBe(-100);
    expect(z[2]).toBe(100);
    expect(z[1]).toBeGreaterThan(-5);
    expect(z[1]).toBeLessThan(5);
  });

  it("puts undated entities on their own plane in front, with a ruler mark", () => {
    const { z, ticks } = timeDepth([node(""), node("not a date"), node("2026-05-01")], 200);
    const front = 100 + 200 * UNDATED_GAP_RATIO;
    expect(z[0]).toBeCloseTo(front, 4);
    expect(z[1]).toBeCloseTo(front, 4);
    expect(z[2]).toBe(0);
    expect(ticks.at(-1)).toEqual({ z: expect.closeTo(front, 4) as number, label: UNDATED_LABEL });
  });

  it("marks only the undated plane when nothing is dated", () => {
    expect(timeDepth([node("")], 100).ticks).toEqual([
      { z: expect.closeTo(50 + 100 * UNDATED_GAP_RATIO, 4) as number, label: UNDATED_LABEL },
    ]);
  });

  it("reads dates", () => {
    expect(parseSeen("2026-11-10T10:00:00Z")).toBe(Date.UTC(2026, 10, 10, 10));
    expect(parseSeen("  ")).toBeNull();
    expect(parseSeen("yesterday")).toBeNull();
  });
});

describe("ruler dates", () => {
  it("marks days, months or years depending on the range", () => {
    const days = timeTicks(Date.UTC(2026, 10, 1, 6), Date.UTC(2026, 10, 4, 18));
    expect(days.map((tick) => tick.label)).toEqual(["2 Nov", "3 Nov", "4 Nov"]);
    const months = timeTicks(Date.UTC(2026, 0, 15), Date.UTC(2026, 5, 15));
    expect(months.map((tick) => tick.label)).toEqual([
      "Feb 2026",
      "Mar 2026",
      "Apr 2026",
      "May 2026",
      "Jun 2026",
    ]);
    const quarters = timeTicks(Date.UTC(2025, 1, 1), Date.UTC(2026, 3, 1));
    expect(quarters.map((tick) => tick.label)).toEqual([
      "Apr 2025",
      "Jul 2025",
      "Oct 2025",
      "Jan 2026",
      "Apr 2026",
    ]);
    const years = timeTicks(Date.UTC(2014, 5, 1), Date.UTC(2026, 5, 1));
    expect(years.map((tick) => tick.label)).toEqual(["2015", "2020", "2025"]);
  });

  it("names the day of a single date, and the ends of a range shorter than a step", () => {
    expect(timeTicks(Date.UTC(2026, 10, 10), Date.UTC(2026, 10, 10))).toEqual([
      { time: Date.UTC(2026, 10, 10), label: "10 Nov 2026" },
    ]);
    const short = timeTicks(Date.UTC(2026, 10, 10, 1), Date.UTC(2026, 10, 10, 9));
    // Both ends on one day: one mark.
    expect(short.map((tick) => tick.label)).toEqual(["10 Nov"]);
    const overnight = timeTicks(Date.UTC(2026, 10, 10, 23), Date.UTC(2026, 10, 11, 1));
    // Midnight falls inside: it gets the day mark.
    expect(overnight.map((tick) => tick.label)).toEqual(["11 Nov"]);
  });

  it("adds the year to day marks that cross a new year", () => {
    const labels = timeTicks(Date.UTC(2025, 11, 30), Date.UTC(2026, 0, 2)).map((t) => t.label);
    expect(labels).toEqual(["30 Dec 2025", "31 Dec 2025", "1 Jan 2026", "2 Jan 2026"]);
  });
});

describe("type depth", () => {
  it("gives each present type its own evenly spaced layer, in the legend's order", () => {
    const { z, ticks } = typeDepth(
      [node("", "ip_address"), node("", "domain"), node("", "asn"), node("", "domain")],
      120,
    );
    // ENTITY_TYPES order: domain, ip_address, …, asn.
    expect(ticks.map((tick) => tick.label)).toEqual(["Domains", "IP addresses", "ASNs"]);
    expect(z[1]).toBe(-60);
    expect(z[3]).toBe(-60);
    expect(z[0]).toBe(0);
    expect(z[2]).toBe(60);
  });

  it("puts a single type at z = 0", () => {
    expect(typeDepth([node("", "url")], 100).z[0]).toBe(0);
  });
});

describe("depth modes", () => {
  it("is flat in flat mode, and dispatches the others", () => {
    expect(depthOf("flat", [node("2026-01-01")], 100)).toEqual({
      z: new Float32Array(1),
      ticks: [],
    });
    expect(depthOf("type", [node("", "url")], 100).ticks).toHaveLength(1);
    expect(depthOf("time", [node("2026-01-01")], 100).ticks).toHaveLength(1);
  });

  it("scales the range with the layout, with a minimum", () => {
    expect(depthSpan(10)).toBe(MIN_DEPTH_SPAN);
    expect(depthSpan(1000)).toBe(1600);
  });

  it("blends between two depths and animates only without reduced motion", () => {
    const out = new Float32Array(2);
    blendDepth([0, 10], [10, 20], 0.5, out);
    expect([...out]).toEqual([5, 15]);
    blendDepth([0, 10], [10, 20], 2, out);
    expect([...out]).toEqual([10, 20]);
    expect(depthAnimationMs(false)).toBe(DEPTH_ANIMATION_MS);
    expect(DEPTH_ANIMATION_MS).toBeLessThanOrEqual(500);
    expect(depthAnimationMs(true)).toBe(0);
  });
});
