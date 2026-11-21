import { describe, expect, it, vi } from "vitest";

import { createFrameLoop } from "./frameLoop";

/** A manual frame scheduler: `frame(now)` runs what was requested. */
function scheduler() {
  let next = 1;
  const pending = new Map<number, (now: number) => void>();
  return {
    requestFrame: (callback: (now: number) => void) => {
      const handle = next++;
      pending.set(handle, callback);
      return handle;
    },
    cancelFrame: (handle: number) => {
      pending.delete(handle);
    },
    frame(now: number) {
      const callbacks = [...pending.values()];
      pending.clear();
      for (const callback of callbacks) callback(now);
    },
    pending: () => pending.size,
  };
}

describe("createFrameLoop", () => {
  it("draws at most fps frames a second, at loom time", () => {
    const s = scheduler();
    const draw = vi.fn();
    createFrameLoop({ draw, startSeconds: 100, fps: 30, ...s });
    // 60 Hz frames for one second.
    for (let i = 0; i <= 60; i++) s.frame(i * (1000 / 60));
    expect(draw.mock.calls.length).toBeGreaterThanOrEqual(30);
    expect(draw.mock.calls.length).toBeLessThanOrEqual(31);
    expect(draw.mock.calls[0]?.[0]).toBe(100);
    expect(draw.mock.calls.at(-1)?.[0]).toBeCloseTo(101, 1);
  });

  it("stops scheduling frames while paused, and resumes without jumping ahead", () => {
    const s = scheduler();
    const draw = vi.fn();
    const loop = createFrameLoop({ draw, startSeconds: 0, fps: 30, ...s });
    s.frame(0);
    s.frame(40);
    expect(loop.running()).toBe(true);

    loop.pause("hidden");
    expect(loop.running()).toBe(false);
    expect(s.pending()).toBe(0);
    s.frame(80);
    const drawn = draw.mock.calls.length;

    // Both reasons must clear.
    loop.pause("offscreen");
    loop.resume("hidden");
    expect(s.pending()).toBe(0);
    loop.resume("offscreen");
    expect(loop.running()).toBe(true);
    // A minute later: the loom carries on from where it stopped.
    s.frame(60_000);
    s.frame(60_040);
    expect(draw.mock.calls.length).toBe(drawn + 2);
    expect(draw.mock.calls.at(-1)?.[0]).toBeCloseTo(0.08, 2);
  });

  it("moves at most a tenth of a second after a stalled frame", () => {
    const s = scheduler();
    const draw = vi.fn();
    createFrameLoop({ draw, startSeconds: 5, fps: 30, ...s });
    s.frame(0);
    s.frame(5_000);
    expect(draw.mock.calls.at(-1)?.[0]).toBeCloseTo(5.1);
  });

  it("does nothing after stop", () => {
    const s = scheduler();
    const draw = vi.fn();
    const loop = createFrameLoop({ draw, startSeconds: 0, fps: 30, ...s });
    loop.stop();
    expect(s.pending()).toBe(0);
    loop.resume("hidden");
    expect(s.pending()).toBe(0);
    expect(loop.running()).toBe(false);
    s.frame(10);
    expect(draw).not.toHaveBeenCalled();
    loop.stop();
  });

  it("ignores a frame that arrives after stopping", () => {
    const s = scheduler();
    const draw = vi.fn();
    const loop = createFrameLoop({
      draw,
      startSeconds: 0,
      fps: 30,
      requestFrame: s.requestFrame,
      // A browser may still run a frame it was asked for.
      cancelFrame: () => undefined,
    });
    loop.stop();
    s.frame(10);
    expect(draw).not.toHaveBeenCalled();
  });

  it("uses requestAnimationFrame by default", () => {
    const request = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(7);
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
    const loop = createFrameLoop({ draw: vi.fn(), startSeconds: 0, fps: 30 });
    expect(request).toHaveBeenCalledTimes(1);
    loop.stop();
    expect(cancel).toHaveBeenCalledWith(7);
  });
});
