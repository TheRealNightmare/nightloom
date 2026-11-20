import { afterEach, describe, expect, it, vi } from "vitest";

import {
  initialMode,
  isDepthMode,
  isViewMode,
  MODE_STORAGE_KEY,
  storedMode,
  storeMode,
  webglSupported,
} from "./mode";

afterEach(() => {
  window.localStorage.clear();
});

describe("view mode", () => {
  it("is 2D until 3D is chosen, and remembers the choice", () => {
    expect(storedMode()).toBe("2d");
    storeMode("3d");
    expect(window.localStorage.getItem(MODE_STORAGE_KEY)).toBe("3d");
    expect(storedMode()).toBe("3d");
    window.localStorage.setItem(MODE_STORAGE_KEY, "4d");
    expect(storedMode()).toBe("2d");
  });

  it("follows the URL first, then the remembered choice", () => {
    storeMode("3d");
    expect(initialMode("?mode=2d")).toBe("2d");
    expect(initialMode("?mode=3d")).toBe("3d");
    expect(initialMode("?mode=nope")).toBe("3d");
    expect(initialMode("")).toBe("3d");
  });

  it("works without storage (private windows, blocked site data)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => {
      storeMode("3d");
    }).not.toThrow();
    expect(storedMode()).toBe("2d");
  });

  it("recognizes modes and depths", () => {
    expect(isViewMode("3d")).toBe(true);
    expect(isViewMode("3D")).toBe(false);
    expect(isDepthMode("type")).toBe(true);
    expect(isDepthMode("depth")).toBe(false);
  });
});

describe("WebGL", () => {
  it("is supported when a WebGL 2 context can be created", () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext");
    getContext.mockReturnValue(null);
    expect(webglSupported()).toBe(false);
    getContext.mockReturnValue({} as RenderingContext);
    expect(webglSupported()).toBe(true);
    getContext.mockImplementation(() => {
      throw new Error("no");
    });
    expect(webglSupported()).toBe(false);
  });
});
