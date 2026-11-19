import { afterEach, describe, expect, it, vi } from "vitest";

import { LABEL_FONT_SPEC, LABEL_FONT_TIMEOUT_MS, waitForLabelFont } from "./labelFont";

function fonts(loaded: boolean, load: () => Promise<unknown> = () => Promise.resolve([])) {
  const check = vi.fn(() => loaded);
  const loadFont = vi.fn(load);
  return { set: { check, load: loadFont } as unknown as FontFaceSet, check, load: loadFont };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("waitForLabelFont", () => {
  it("draws at once without a font loading API or once the font is there", () => {
    expect(waitForLabelFont(undefined)).toBeNull();
    const ready = fonts(true);
    expect(waitForLabelFont(ready.set)).toBeNull();
    expect(ready.check).toHaveBeenCalledWith(LABEL_FONT_SPEC);
  });

  it("waits for both weights of Source Sans 3", async () => {
    const pending = fonts(false);
    await waitForLabelFont(pending.set);
    expect(pending.load).toHaveBeenCalledWith(LABEL_FONT_SPEC);
    expect(pending.load).toHaveBeenCalledWith(`600 ${LABEL_FONT_SPEC}`);
    expect(LABEL_FONT_SPEC).toContain("Source Sans 3");
  });

  it("gives up waiting after a while", async () => {
    vi.useFakeTimers();
    const stuck = fonts(false, () => new Promise(() => undefined));
    let done = false;
    void waitForLabelFont(stuck.set)?.then(() => {
      done = true;
    });
    await vi.advanceTimersByTimeAsync(LABEL_FONT_TIMEOUT_MS - 1);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(done).toBe(true);
  });
});
