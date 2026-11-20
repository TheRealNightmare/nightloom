import { describe, expect, it } from "vitest";

import { announceNode, focusOrder, nextFocus, ROW_HEIGHT } from "./focusOrder";

describe("keyboard focus order", () => {
  it("reads the screen in rows, top to bottom, left to right", () => {
    const order = focusOrder([
      { id: "bottom-left", point: { x: 10, y: 300 } },
      { id: "top-right", point: { x: 400, y: 12 } },
      { id: "top-left", point: { x: 20, y: 20 } },
      // A little higher than top-left but on the same row: still after it.
      { id: "top-middle", point: { x: 200, y: 5 } },
      { id: "middle", point: { x: 100, y: 150 } },
    ]);
    expect(order).toEqual(["top-left", "top-middle", "top-right", "middle", "bottom-left"]);
  });

  it("breaks ties by id, so the order is stable", () => {
    const point = { x: 5, y: 5 };
    expect(
      focusOrder([
        { id: "b", point },
        { id: "a", point },
      ]),
    ).toEqual(["a", "b"]);
    expect(ROW_HEIGHT).toBeGreaterThan(0);
  });

  it("moves forward and back, and leaves the graph past either end", () => {
    const order = ["a", "b", "c"];
    expect(nextFocus(order, null, false)).toBe("a");
    expect(nextFocus(order, null, true)).toBe("c");
    expect(nextFocus(order, "a", false)).toBe("b");
    expect(nextFocus(order, "b", true)).toBe("a");
    expect(nextFocus(order, "c", false)).toBeNull();
    expect(nextFocus(order, "a", true)).toBeNull();
    // A focused node that is no longer in the order starts over.
    expect(nextFocus(order, "gone", false)).toBe("a");
    expect(nextFocus([], null, false)).toBeNull();
  });

  it("announces the type, the value, the neighbours and the pin", () => {
    expect(
      announceNode({ type: "domain", label: "www.example.org", neighbours: 3, pinned: true }),
    ).toBe("Domain www.example.org, 3 neighbours, pinned");
    expect(
      announceNode({ type: "ip_address", label: "192.0.2.1", neighbours: 1, pinned: false }),
    ).toBe("IP address 192.0.2.1, 1 neighbour");
    expect(
      announceNode({ type: "url", label: "312 URLs", neighbours: 1, pinned: false, members: 312 }),
    ).toBe("Group of 312 URLs, 1 neighbour");
  });
});
