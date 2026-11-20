import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MINIMAP_STORAGE_KEY,
  storedMinimap,
  storedWheelSetting,
  storeMinimap,
  storeWheelSetting,
  WHEEL_STORAGE_KEY,
} from "./preferences";

afterEach(() => {
  window.localStorage.clear();
});

describe("graph preferences", () => {
  it("defaults to automatic", () => {
    expect(storedWheelSetting()).toBe("auto");
    expect(storedMinimap()).toBe("auto");
  });

  it("remembers choices and ignores anything else", () => {
    storeWheelSetting("pan");
    storeMinimap("off");
    expect(storedWheelSetting()).toBe("pan");
    expect(storedMinimap()).toBe("off");
    window.localStorage.setItem(WHEEL_STORAGE_KEY, "sideways");
    window.localStorage.setItem(MINIMAP_STORAGE_KEY, "maybe");
    expect(storedWheelSetting()).toBe("auto");
    expect(storedMinimap()).toBe("auto");
  });

  it("works without storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => {
      storeWheelSetting("zoom");
      storeMinimap("on");
    }).not.toThrow();
    expect(storedWheelSetting()).toBe("auto");
    expect(storedMinimap()).toBe("auto");
  });
});
