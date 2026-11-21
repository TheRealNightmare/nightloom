import { describe, expect, it } from "vitest";

import {
  boxesIntersect,
  LABEL_MARGIN,
  labelBox,
  measureText,
  placeLabels,
  type LabelCandidate,
} from "./labelLayout";

/** A small deterministic random generator (mulberry32). */
function random(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const HOSTS = [
  "www.example.org",
  "api.example.org",
  "shop.example.com",
  "cdn.example.net",
  "mail.example.org",
  "198.51.100.0/24",
  "2001:db8::10",
  "https://shop.example.com/cart",
  "AS64500",
];

function candidate(node: string, x: number, y: number, extra: Partial<LabelCandidate> = {}) {
  return {
    node,
    x,
    y,
    radius: 7,
    textWidth: measureText("www.example.org", "400 sans", 12),
    textHeight: 14,
    position: "right",
    mandatory: false,
    priority: 0,
    ...extra,
  } satisfies LabelCandidate;
}

/** `count` knots packed into a `width` × `height` patch, as a dense part of a graph. */
function denseFixture(count: number, width: number, height: number, seed = 7): LabelCandidate[] {
  const next = random(seed);
  return Array.from({ length: count }, (_, i) => {
    const text = HOSTS[i % HOSTS.length] ?? "";
    return {
      node: `n${String(i)}`,
      x: 100 + next() * width,
      y: 100 + next() * height,
      radius: 5 + next() * 6,
      textWidth: measureText(text, "400 sans", 12),
      textHeight: 14,
      position: i % 17 === 0 ? "over" : "right",
      ...(i % 17 === 0 ? { below: { width: 50, height: 18 } } : {}),
      ...(i % 5 === 0 ? { before: 14 } : {}),
      mandatory: false,
      priority: Math.floor(next() * 10),
    } satisfies LabelCandidate;
  });
}

function expectNoOverlap(
  placed: Map<string, ReturnType<typeof labelBox>>,
  exempt: ReadonlySet<string> = new Set(),
) {
  const entries = [...placed];
  for (let i = 0; i < entries.length; i += 1) {
    for (let j = i + 1; j < entries.length; j += 1) {
      const [a, boxA] = entries[i] ?? [];
      const [b, boxB] = entries[j] ?? [];
      if (!a || !b || !boxA || !boxB) continue;
      // Only mandatory labels may sit over each other.
      if (exempt.has(a) && exempt.has(b)) continue;
      expect(boxesIntersect(boxA, boxB), `${a} and ${b} overlap`).toBe(false);
    }
  }
}

describe("labelBox", () => {
  it("starts past the knot, with the pin before the text", () => {
    const plain = labelBox(candidate("a", 100, 50));
    expect(plain.x).toBeLessThan(100 + 7 + LABEL_MARGIN);
    expect(plain.x).toBeGreaterThan(100 + 7);
    const pinned = labelBox(candidate("a", 100, 50, { before: 14 }));
    expect(pinned.x).toBeCloseTo(plain.x - 14, 9);
    expect(pinned.width).toBeCloseTo(plain.width + 14, 9);
  });

  it("centers a group's count on its knot, with the type tag under it", () => {
    const box = labelBox(
      candidate("g", 100, 50, {
        position: "over",
        textWidth: 20,
        below: { width: 60, height: 18 },
      }),
    );
    expect(box.x + box.width / 2).toBeCloseTo(100, 9);
    expect(box.width).toBeGreaterThanOrEqual(60);
    expect(box.y + box.height).toBeGreaterThan(50 + 7 + 18);
  });
});

describe("placeLabels", () => {
  it("draws no two labels whose boxes intersect, for a dense graph", () => {
    const fixture = denseFixture(400, 360, 240);
    const placed = placeLabels(fixture, { width: 1200, height: 800 });
    expect(placed.size).toBeGreaterThan(10);
    expect(placed.size).toBeLessThan(fixture.length);
    expectNoOverlap(placed);
  });

  it("keeps the example hosts apart (they used to overlap)", () => {
    // Where the 2D screenshot drew them: on one line, 75 to 90 px apart.
    const fixture = [
      candidate("www", 545, 645, { priority: 3 }),
      candidate("api", 620, 645, {
        priority: 1,
        textWidth: measureText("api.example.org", "400 sans", 12),
      }),
      candidate("shop", 708, 641, {
        priority: 2,
        textWidth: measureText("shop.example.com", "400 sans", 12),
      }),
    ];
    const placed = placeLabels(fixture);
    expectNoOverlap(placed);
    expect([...placed.keys()].sort()).toEqual(["shop", "www"]);
  });

  it("always draws hovered, selected and focused labels, and keeps the others off them", () => {
    const fixture = denseFixture(300, 300, 200, 11);
    const exempt = new Set(["n3", "n4", "n5"]);
    const marked = fixture.map((c) => (exempt.has(c.node) ? { ...c, mandatory: true } : c));
    const placed = placeLabels(marked, { width: 1200, height: 800 });
    for (const node of exempt) expect(placed.has(node)).toBe(true);
    expectNoOverlap(placed, exempt);
  });

  it("gives a contested spot to the higher priority, then the earlier node", () => {
    const low = candidate("a", 100, 100, { priority: 1 });
    const high = candidate("b", 104, 102, { priority: 5 });
    expect([...placeLabels([low, high]).keys()]).toEqual(["b"]);
    const tieA = candidate("a", 100, 100, { priority: 3 });
    const tieB = candidate("b", 104, 102, { priority: 3 });
    expect([...placeLabels([tieB, tieA]).keys()]).toEqual(["a"]);
  });

  it("keeps labels off other knots, but not off their own", () => {
    const fixture = denseFixture(300, 360, 240, 5);
    const knots = fixture.map(({ node, x, y, radius }) => ({ node, x, y, radius }));
    const placed = placeLabels(fixture, { width: 1200, height: 800 }, knots);
    expect(placed.size).toBeGreaterThan(5);
    for (const [node, box] of placed) {
      for (const knot of knots) {
        if (knot.node === node) continue;
        const knotBox = {
          x: knot.x - knot.radius,
          y: knot.y - knot.radius,
          width: 2 * knot.radius,
          height: 2 * knot.radius,
        };
        expect(boxesIntersect(box, knotBox), `${node} covers ${knot.node}`).toBe(false);
      }
    }
    // A group's count over its own knot is still drawn.
    const group = candidate("g", 400, 400, { position: "over", textWidth: 10 });
    expect(
      placeLabels([group], undefined, [{ node: "g", x: 400, y: 400, radius: 12 }]).has("g"),
    ).toBe(true);
  });

  it("skips labels well off screen, unless mandatory", () => {
    const off = candidate("off", -500, 100);
    const hovered = candidate("hovered", -500, 300, { mandatory: true });
    const placed = placeLabels([off, hovered], { width: 800, height: 600 });
    expect([...placed.keys()]).toEqual(["hovered"]);
  });

  it("places 2,000 labels quickly", () => {
    const fixture = denseFixture(2000, 1600, 1000, 3);
    const viewport = { width: 1800, height: 1200 };
    for (let i = 0; i < 5; i += 1) placeLabels(fixture, viewport);
    const timings: number[] = [];
    for (let i = 0; i < 9; i += 1) {
      const start = performance.now();
      placeLabels(fixture, viewport);
      timings.push(performance.now() - start);
    }
    timings.sort((a, b) => a - b);
    expect(placeLabels(fixture, viewport).size).toBeGreaterThan(50);
    // About 3 ms on a desktop CPU; generous for coverage instrumentation and
    // a busy machine, yet far below what a quadratic placement would take.
    expect(timings[4]).toBeLessThan(50 * (import.meta.env["CI"] ? 4 : 1));
  });
});
