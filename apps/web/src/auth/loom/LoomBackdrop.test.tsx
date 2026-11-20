import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeContext } from "../../theme/context";
import type { Theme } from "../../theme/tokens";
import { hasWebGL, loomColors, stillReason, wantsSaveData, whenIdle } from "./environment";
import { LoomBackdrop, type LoomBackdropProps } from "./LoomBackdrop";
import type { LoomScene, LoomSceneOptions } from "./loomScene";

type Listener = () => void;

/** matchMedia whose reduced-motion answer the test controls. */
function stubReducedMotion(initial: boolean) {
  const listeners = new Set<Listener>();
  const query = {
    matches: initial,
    addEventListener: (_: string, listener: Listener) => listeners.add(listener),
    removeEventListener: (_: string, listener: Listener) => listeners.delete(listener),
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn((text: string) =>
      text.includes("reduced-motion")
        ? query
        : { matches: false, addEventListener() {}, removeEventListener() {} },
    ),
  );
  return {
    set(value: boolean) {
      query.matches = value;
      for (const listener of listeners) listener();
    },
    listeners,
  };
}

function stubWebGL(available: boolean) {
  const lose = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(((kind: string) =>
    available && kind === "webgl2"
      ? { getExtension: () => ({ loseContext: lose }) }
      : null) as unknown as HTMLCanvasElement["getContext"]);
  return lose;
}

function stubIdleNow() {
  vi.stubGlobal("requestIdleCallback", (callback: () => void) => {
    callback();
    return 1;
  });
  vi.stubGlobal("cancelIdleCallback", vi.fn());
}

function fakeScene() {
  const scene: LoomScene & { options: LoomSceneOptions | undefined } = {
    setColors: vi.fn(),
    dispose: vi.fn(),
    loop: { pause: vi.fn(), resume: vi.fn(), running: () => true, stop: vi.fn() },
    options: undefined,
  };
  const startLoomScene = vi.fn(
    (_canvas: HTMLCanvasElement, _colors: unknown, options?: LoomSceneOptions) => {
      scene.options = options;
      return scene;
    },
  );
  const loadScene = vi.fn(() =>
    Promise.resolve({ startLoomScene } as unknown as typeof import("./loomScene")),
  );
  return { scene, startLoomScene, loadScene };
}

function renderBackdrop(props: LoomBackdropProps, theme: Theme = "dark") {
  const ui = (value: Theme) => (
    <ThemeContext.Provider value={{ theme: value, toggleTheme: () => undefined }}>
      <div data-testid="host">
        <LoomBackdrop {...props} />
      </div>
    </ThemeContext.Provider>
  );
  const result = render(ui(theme));
  const loom = () => screen.getByTestId("host").firstElementChild as HTMLElement;
  return {
    ...result,
    loom,
    setTheme: (value: Theme) => {
      result.rerender(ui(value));
    },
  };
}

beforeEach(() => {
  stubReducedMotion(false);
  stubWebGL(true);
  stubIdleNow();
});

describe("when the scene may load", () => {
  it("is decorative: hidden from assistive technology, nothing to focus", () => {
    const { loom } = renderBackdrop({ loadScene: () => new Promise(() => undefined) });
    expect(loom()).toHaveAttribute("aria-hidden", "true");
    expect(loom().querySelectorAll("a, button, input, [tabindex]")).toHaveLength(0);
  });

  it("shows the still drawing, then the scene once it has drawn a frame", async () => {
    const { scene, startLoomScene, loadScene } = fakeScene();
    const { loom } = renderBackdrop({ loadScene });
    await waitFor(() => {
      expect(startLoomScene).toHaveBeenCalledTimes(1);
    });
    expect(loom()).toHaveAttribute("data-loom", "loading");
    expect(screen.getByTestId("loom-static")).toBeInTheDocument();
    expect(startLoomScene.mock.calls[0]?.[0]).toBe(screen.getByTestId("loom-canvas"));
    expect(startLoomScene.mock.calls[0]?.[1]).toEqual(loomColors("dark"));

    act(() => {
      scene.options?.onReady?.();
    });
    expect(loom()).toHaveAttribute("data-loom", "scene");
    expect(screen.queryByTestId("loom-static")).not.toBeInTheDocument();
    expect(screen.getByTestId("loom-canvas")).toBeInTheDocument();
  });

  it("re-reads the tokens when the theme changes, and disposes the scene on the way out", async () => {
    const { scene, startLoomScene, loadScene } = fakeScene();
    const { setTheme, unmount } = renderBackdrop({ loadScene });
    await waitFor(() => {
      expect(startLoomScene).toHaveBeenCalled();
    });
    setTheme("light");
    expect(scene.setColors).toHaveBeenCalledWith(loomColors("light"));
    setTheme("light");
    expect(scene.setColors).toHaveBeenCalledTimes(1);
    unmount();
    expect(scene.dispose).toHaveBeenCalled();
  });

  it("falls back to the still drawing when the scene cannot be fetched", async () => {
    const { loom } = renderBackdrop({ loadScene: () => Promise.reject(new Error("offline")) });
    await waitFor(() => {
      expect(loom()).toHaveAttribute("data-loom-reason", "failed");
    });
    expect(screen.getByTestId("loom-static")).toBeInTheDocument();
    expect(screen.queryByTestId("loom-canvas")).not.toBeInTheDocument();
  });

  it("falls back when the renderer cannot start", async () => {
    const { startLoomScene, loadScene } = fakeScene();
    startLoomScene.mockImplementation(() => {
      throw new Error("no context");
    });
    const { loom } = renderBackdrop({ loadScene });
    await waitFor(() => {
      expect(loom()).toHaveAttribute("data-loom-reason", "failed");
    });
  });

  it("falls back, disposing the scene, when it reports an error", async () => {
    const { scene, startLoomScene, loadScene } = fakeScene();
    const { loom } = renderBackdrop({ loadScene });
    await waitFor(() => {
      expect(startLoomScene).toHaveBeenCalled();
    });
    act(() => {
      scene.options?.onError?.(new Error("context lost"));
    });
    expect(loom()).toHaveAttribute("data-loom-reason", "failed");
    expect(scene.dispose).toHaveBeenCalled();
  });

  it("goes back to the still drawing when reduced motion is switched on", async () => {
    const motion = stubReducedMotion(false);
    const { scene, startLoomScene, loadScene } = fakeScene();
    const { loom, unmount } = renderBackdrop({ loadScene });
    await waitFor(() => {
      expect(startLoomScene).toHaveBeenCalled();
    });
    act(() => {
      motion.set(false);
    });
    expect(scene.dispose).not.toHaveBeenCalled();
    act(() => {
      motion.set(true);
    });
    expect(scene.dispose).toHaveBeenCalled();
    expect(loom()).toHaveAttribute("data-loom-reason", "reduced-motion");
    unmount();
    expect(motion.listeners.size).toBe(0);
  });

  it("does not start a scene that arrives after the page has gone", async () => {
    let resolve: (module: typeof import("./loomScene")) => void = () => undefined;
    const { startLoomScene } = fakeScene();
    const loadScene = vi.fn(
      () =>
        new Promise<typeof import("./loomScene")>((done) => {
          resolve = done;
        }),
    );
    const { unmount } = renderBackdrop({ loadScene });
    await waitFor(() => {
      expect(loadScene).toHaveBeenCalled();
    });
    unmount();
    await act(async () => {
      resolve({ startLoomScene } as unknown as typeof import("./loomScene"));
      await Promise.resolve();
    });
    expect(startLoomScene).not.toHaveBeenCalled();
  });

  it("ignores a late ready or error after the page has gone", async () => {
    const { scene, startLoomScene, loadScene } = fakeScene();
    const { unmount } = renderBackdrop({ loadScene });
    await waitFor(() => {
      expect(startLoomScene).toHaveBeenCalled();
    });
    unmount();
    scene.options?.onReady?.();
    scene.options?.onError?.(new Error("late"));
    expect(scene.dispose).toHaveBeenCalledTimes(1);
  });
});

describe("the still drawing stays, and nothing is fetched,", () => {
  it.each([
    [
      "with reduced motion",
      () => {
        stubReducedMotion(true);
      },
      "reduced-motion",
    ],
    [
      "with Save-Data",
      () => {
        Object.defineProperty(navigator, "connection", {
          configurable: true,
          value: { saveData: true },
        });
      },
      "save-data",
    ],
    [
      "without WebGL",
      () => {
        stubWebGL(false);
      },
      "no-webgl",
    ],
  ])("%s", (_name, setUp, reason) => {
    setUp();
    const loadScene = vi.fn();
    const { loom } = renderBackdrop({ loadScene });
    expect(loom()).toHaveAttribute("data-loom", "still");
    expect(loom()).toHaveAttribute("data-loom-reason", reason);
    expect(screen.getByTestId("loom-static")).toBeInTheDocument();
    expect(screen.queryByTestId("loom-canvas")).not.toBeInTheDocument();
    expect(loadScene).not.toHaveBeenCalled();
  });
});

afterEach(() => {
  Reflect.deleteProperty(navigator, "connection");
});

describe("helpers", () => {
  it("hasWebGL releases its test context, and survives a throwing canvas", () => {
    const lose = stubWebGL(true);
    expect(hasWebGL()).toBe(true);
    expect(lose).toHaveBeenCalled();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(hasWebGL()).toBe(false);
  });

  it("stillReason checks reduced motion first and copes without matchMedia", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(stillReason()).toBeNull();
    expect(wantsSaveData()).toBe(false);
  });

  it("loomColors reads the tokens of each theme", () => {
    const dark = loomColors("dark");
    const light = loomColors("light");
    expect(dark.background).not.toEqual(light.background);
    expect(dark.knots).toHaveLength(8);
    for (const channel of [...dark.background, ...light.weft]) {
      expect(channel).toBeGreaterThanOrEqual(0);
      expect(channel).toBeLessThanOrEqual(1);
    }
  });
});

describe("whenIdle", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("waits for the load event, then for an idle moment", () => {
    const readyState = vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    const callback = vi.fn();
    whenIdle(callback);
    expect(callback).not.toHaveBeenCalled();
    readyState.mockReturnValue("complete");
    window.dispatchEvent(new Event("load"));
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("can be cancelled before the page has loaded, or while waiting to be idle", () => {
    const readyState = vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    const callback = vi.fn();
    whenIdle(callback)();
    window.dispatchEvent(new Event("load"));
    expect(callback).not.toHaveBeenCalled();

    readyState.mockReturnValue("complete");
    let pending: () => void = () => undefined;
    const cancelIdle = vi.fn();
    vi.stubGlobal("requestIdleCallback", (fn: () => void) => {
      pending = fn;
      return 42;
    });
    vi.stubGlobal("cancelIdleCallback", cancelIdle);
    whenIdle(callback)();
    expect(cancelIdle).toHaveBeenCalledWith(42);
    pending();
    expect(callback).not.toHaveBeenCalled();
  });

  it("uses a short timer without requestIdleCallback", () => {
    vi.useFakeTimers();
    vi.stubGlobal("requestIdleCallback", undefined);
    vi.stubGlobal("cancelIdleCallback", undefined);
    const callback = vi.fn();
    whenIdle(callback);
    vi.advanceTimersByTime(499);
    expect(callback).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(callback).toHaveBeenCalledTimes(1);

    const cancelled = vi.fn();
    whenIdle(cancelled)();
    vi.advanceTimersByTime(1000);
    expect(cancelled).not.toHaveBeenCalled();
  });
});
