import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sampleGraph } from "../demo/sampleGraph";
import { buildGraph } from "./buildGraph";
import { groupId } from "./groups";
import {
  applyPins,
  isSavedPin,
  LayoutSaver,
  mergeLoadedPins,
  PinStore,
  SAVE_BACKOFF_MS,
  SAVE_DEBOUNCE_MS,
  type Positions,
  type SaveOptions,
  type SaveStatus,
} from "./pins";

describe("merging the saved layout with this session's changes", () => {
  it("takes the saved pins when nothing changed", () => {
    const merged = mergeLoadedPins({ a: [1, 2], b: [3, 4] }, new Map(), new Set(), false);
    expect(Object.fromEntries(merged)).toEqual({ a: [1, 2], b: [3, 4] });
  });

  it("keeps what the user pinned or unpinned before it arrived", () => {
    const local = new Map([["a", [9, 9] as const]]);
    // "b" was unpinned locally: it stays unpinned.
    const merged = mergeLoadedPins(
      { a: [1, 2], b: [3, 4], c: [5, 6] },
      local,
      new Set(["a", "b"]),
      false,
    );
    expect(Object.fromEntries(merged)).toEqual({ a: [9, 9], c: [5, 6] });
  });

  it("drops every saved pin after Unpin all, and malformed ones", () => {
    const local = new Map([["n", [1, 1] as const]]);
    expect(Object.fromEntries(mergeLoadedPins({ a: [1, 2] }, local, new Set(), true))).toEqual({
      n: [1, 1],
    });
    const broken = { a: [1, Number.NaN], b: [1] } as unknown as Positions;
    expect(mergeLoadedPins(broken, new Map(), new Set(), false).size).toBe(0);
  });
});

describe("PinStore", () => {
  it("merges the layout it loads with earlier changes, then saves entities only", () => {
    const store = new PinStore();
    const listener = vi.fn();
    store.subscribe(listener);
    store.pin([["domain:a.example", [1, 1]]]);
    store.unpin(["domain:b.example"]);
    store.pin([[groupId("domain:a.example", "url"), [5, 5]]]);
    store.load({ "domain:b.example": [2, 2], "domain:c.example": [3, 3] });
    expect(store.loaded).toBe(true);
    expect(store.saved()).toEqual({ "domain:a.example": [1, 1], "domain:c.example": [3, 3] });
    // The group's pin is kept for the session, not saved.
    expect(store.has(groupId("domain:a.example", "url"))).toBe(true);
    expect(isSavedPin(groupId("domain:a.example", "url"))).toBe(false);
    expect(listener).toHaveBeenCalledTimes(4);
  });

  it("only notifies when something changed", () => {
    const store = new PinStore();
    store.loaded = true;
    const listener = vi.fn();
    store.subscribe(listener);
    store.pin([["a", [1, 1]]]);
    store.pin([["a", [1, 1]]]);
    store.unpin(["missing"]);
    expect(listener).toHaveBeenCalledTimes(1);
    store.clear();
    expect(store.size).toBe(0);
    expect(store.ids()).toEqual([]);
  });
});

describe("applyPins", () => {
  it("places and fixes pinned nodes, and releases unpinned ones", () => {
    const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
    const [first, second] = graph.nodes();
    if (!first || !second) throw new Error("sample too small");
    const store = new PinStore();
    store.loaded = true;
    store.pin([
      [first, [42, -7]],
      [second, [1, 2]],
    ]);
    expect(applyPins(graph, store)).toEqual([]);
    expect(graph.getNodeAttributes(first)).toMatchObject({
      x: 42,
      y: -7,
      pinned: true,
      fixed: true,
    });

    store.unpin([first]);
    expect(applyPins(graph, store)).toEqual([first]);
    expect(graph.getNodeAttribute(first, "pinned")).toBeUndefined();
    expect(graph.getNodeAttribute(first, "fixed")).toBeUndefined();
    // Nothing changes the second time.
    expect(applyPins(graph, store)).toEqual([]);
  });
});

describe("LayoutSaver", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function makeSaver(save: (p: Positions) => Promise<void>, extra = {}) {
    const statuses: SaveStatus[] = [];
    const saver = new LayoutSaver({
      save: (positions) => save(positions),
      onStatus: (status) => statuses.push(status),
      ...extra,
    });
    return { saver, statuses };
  }

  it("debounces: only the last pins of a burst are sent, once", async () => {
    const save = vi.fn(() => Promise.resolve());
    const { saver, statuses } = makeSaver(save);
    saver.request({ a: [1, 1] });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS - 100);
    saver.request({ a: [2, 2] });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS - 1);
    expect(save).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ a: [2, 2] });
    expect(statuses).toEqual(["pending", "saving", "idle"]);
    expect(saver.status).toBe("idle");
  });

  it("retries with a growing delay, sending the newest pins only", async () => {
    let fail = 2;
    const save = vi.fn(() => (fail-- > 0 ? Promise.reject(new Error("down")) : Promise.resolve()));
    const { saver, statuses } = makeSaver(save);
    saver.request({ a: [1, 1] });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS);
    expect(saver.status).toBe("retrying");
    // A newer change while retrying: the retry carries it, not the old one.
    saver.request({ a: [5, 5] });
    await vi.advanceTimersByTimeAsync(SAVE_BACKOFF_MS[0] - 1);
    expect(save).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith({ a: [5, 5] });
    await vi.advanceTimersByTimeAsync(SAVE_BACKOFF_MS[1]);
    expect(save).toHaveBeenCalledTimes(3);
    expect(save).toHaveBeenLastCalledWith({ a: [5, 5] });
    expect(statuses).toEqual(["pending", "saving", "retrying", "idle"]);
  });

  it("waits as long as a 429 says", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("429")).mockResolvedValue(undefined);
    const { saver } = makeSaver(save, { retryAfterSeconds: () => 30 });
    saver.request({ a: [1, 1] });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS);
    await vi.advanceTimersByTimeAsync(29_000);
    expect(save).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("stops on errors retrying cannot fix, and tries again on the next change", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("403")).mockResolvedValue(undefined);
    const { saver } = makeSaver(save, { isPermanent: () => true });
    saver.request({ a: [1, 1] });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS);
    expect(saver.status).toBe("failed");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(save).toHaveBeenCalledTimes(1);
    saver.request({ a: [2, 2] });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS);
    expect(save).toHaveBeenCalledTimes(2);
    expect(saver.status).toBe("idle");
  });

  it("sends at once with keepalive when the page is hidden", async () => {
    const save = vi.fn<(positions: Positions, options: SaveOptions) => Promise<void>>(() =>
      Promise.resolve(),
    );
    const saver = new LayoutSaver({ save });
    saver.flush();
    expect(save).not.toHaveBeenCalled();
    saver.request({ a: [1, 1] });
    saver.flush();
    expect(save).toHaveBeenCalledWith({ a: [1, 1] }, { keepalive: true });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS * 2);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("sends one request at a time; changes made meanwhile follow it", async () => {
    let resolve: () => void = () => undefined;
    const save = vi.fn(
      () =>
        new Promise<void>((r) => {
          resolve = r;
        }),
    );
    const { saver } = makeSaver(save);
    saver.request({ a: [1, 1] });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS);
    saver.request({ a: [2, 2] });
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS);
    expect(save).toHaveBeenCalledTimes(1);
    resolve();
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS);
    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith({ a: [2, 2] });
  });

  it("does nothing once disposed", async () => {
    const save = vi.fn(() => Promise.resolve());
    const { saver } = makeSaver(save);
    saver.request({ a: [1, 1] });
    saver.dispose();
    saver.request({ a: [2, 2] });
    saver.flush();
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS * 2);
    expect(save).not.toHaveBeenCalled();
  });
});
