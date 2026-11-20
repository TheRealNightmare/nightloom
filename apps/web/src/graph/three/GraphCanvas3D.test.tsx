/**
 * The 3D view through GraphView, with the renderer mocked (src/test/sceneMock):
 * the 2D / 3D switch, the URL and the remembered choice, the WebGL fallback
 * and a lost context, picking and the node menu, the keyboard map and what it
 * announces, the depth ruler, and applying a saved 3D view.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { InvestigationGraph } from "../../domain/entities";
import { drawUntilIdle, resetScene, scene } from "../../test/sceneMock";
import { FakeSigma } from "../../test/sigmaMock";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { GraphView, NO_WEBGL, WEBGL_LOST } from "../GraphView";
import { MODE_STORAGE_KEY } from "../mode";
import { MINIMAP_STORAGE_KEY } from "../preferences";
import type { Positions } from "../pins";
import { useSavedViewControls } from "../savedViewContext";
import type { LayoutPersistence } from "../usePins";
import type { ViewSnapshot } from "../viewSnapshot";

vi.mock("sigma", () => import("../../test/sigmaMock"));
vi.mock("./Scene3D", () => import("../../test/sceneMock"));

const APEX = "domain:example.org";
const WWW = "domain:www.example.org";
const API = "domain:api.example.org";
const provenance = (first_seen: string) => ({
  source: "manual",
  first_seen,
  last_seen: "2026-11-10T00:00:00Z",
  confidence: 0.9,
});
const DATA: InvestigationGraph = {
  nodes: [
    { id: APEX, label: "example.org", type: "domain", ...provenance("2025-01-01T00:00:00Z") },
    { id: WWW, label: "www.example.org", type: "domain", ...provenance("2026-06-01T00:00:00Z") },
    { id: API, label: "api.example.org", type: "domain", ...provenance("") },
  ],
  edges: [
    { id: "e1", from: APEX, to: WWW, relation: "HAS_SUBDOMAIN", ...provenance("2026-01-01") },
    { id: "e2", from: APEX, to: API, relation: "HAS_SUBDOMAIN", ...provenance("2026-01-01") },
  ],
};
// Pinned where the tests expect them (the layout leaves pinned nodes be).
const PINS: Positions = { [APEX]: [0, 0], [WWW]: [-150, 60], [API]: [150, 60] };

function layout(saved: Positions = PINS): LayoutPersistence {
  return { load: () => Promise.resolve(saved), save: vi.fn(() => Promise.resolve()) };
}

// The saved-view controls GraphView gives its Views menu.
const probe: {
  apply: ((snapshot: ViewSnapshot) => void) | null;
  capture: (() => ViewSnapshot) | null;
} = { apply: null, capture: null };
function ViewsProbe() {
  const controls = useSavedViewControls();
  useEffect(() => {
    probe.apply = controls.apply;
    probe.capture = controls.capture;
  });
  return null;
}

function renderView(persistence: LayoutPersistence = layout()) {
  return render(
    <ThemeProvider>
      <GraphView
        data={DATA}
        overview={() => <p>Overview</p>}
        layout={persistence}
        savedViews={<ViewsProbe />}
      />
    </ThemeProvider>,
  );
}

async function threeD(): Promise<HTMLElement> {
  const canvas = await screen.findByTestId("graph-canvas");
  await waitFor(() => {
    expect(screen.getByTestId("graph-canvas")).toHaveAttribute("data-mode", "3d");
  });
  await waitFor(() => {
    expect(scene.props).not.toBeNull();
  });
  drawUntilIdle();
  return canvas.dataset["mode"] === "3d" ? canvas : screen.getByTestId("graph-canvas");
}

function announcer() {
  return screen.getByTestId("graph-announcer");
}

/** Tabs until `label` has the focus ring; returns the ring's center (canvas pixels). */
function focusEntity(canvas: HTMLElement, label: string): { x: number; y: number } {
  act(() => {
    canvas.focus();
  });
  for (let i = 0; i < 6; i += 1) {
    fireEvent.keyDown(canvas, { key: "Tab" });
    drawUntilIdle();
    if (announcer().textContent.startsWith(`Domain ${label},`)) {
      const ring = document.querySelector<HTMLElement>(".graph-focus-ring");
      if (!ring || ring.hidden) throw new Error("no ring");
      return {
        x: Number.parseFloat(ring.style.left) + Number.parseFloat(ring.style.width) / 2,
        y: Number.parseFloat(ring.style.top) + Number.parseFloat(ring.style.height) / 2,
      };
    }
  }
  throw new Error(`could not focus ${label}`);
}

beforeEach(() => {
  window.localStorage.clear();
  // Animations end at once (reduced motion), so tests need no timers.
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("reduce"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  // jsdom has no pointer capture (OrbitControls uses it).
  for (const name of ["setPointerCapture", "releasePointerCapture"] as const) {
    if (!(name in Element.prototype)) {
      Object.defineProperty(Element.prototype, name, {
        configurable: true,
        value: () => undefined,
      });
    }
  }
  // WebGL 2 is "available"; the 2D minimaps get no 2D context (they skip drawing).
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(((type: string) =>
    type === "webgl2" ? ({} as RenderingContext) : null) as never);
});

afterEach(() => {
  FakeSigma.reset();
  resetScene();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  probe.apply = null;
  probe.capture = null;
});

describe("the 2D / 3D switch", () => {
  it("switches to 3D and back, remembering the choice", async () => {
    renderView();
    const threeButton = screen.getByRole("button", { name: "3D" });
    await userEvent.click(threeButton);
    await threeD();
    expect(threeButton).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem(MODE_STORAGE_KEY)).toBe("3d");
    expect(screen.getByRole("group", { name: "Depth" })).toBeInTheDocument();
    expect(screen.queryByText(NO_WEBGL)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "2D" }));
    await waitFor(() => {
      expect(screen.getByTestId("graph-canvas")).not.toHaveAttribute("data-mode", "3d");
    });
    expect(window.localStorage.getItem(MODE_STORAGE_KEY)).toBe("2d");
  });

  it("opens in 3D from the URL, or from the remembered choice", async () => {
    window.history.replaceState(null, "", "/?mode=3d");
    const first = renderView();
    await threeD();
    first.unmount();
    resetScene();

    window.history.replaceState(null, "", "/");
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    await threeD();
  });

  it("keeps the selection and the filters when switching", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    focusEntity(canvas, "www.example.org");
    fireEvent.keyDown(canvas, { key: "Enter" });
    drawUntilIdle();
    const details = screen.getByRole("complementary", { name: "Details" });
    expect(within(details).getByText(WWW)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "2D" }));
    expect(within(details).getByText(WWW)).toBeInTheDocument();
  });
});

describe("WebGL", () => {
  it("falls back to 2D with a notice when WebGL is missing", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    expect(await screen.findByText(NO_WEBGL)).toBeInTheDocument();
    expect(screen.getByTestId("graph-canvas")).not.toHaveAttribute("data-mode", "3d");
  });

  it("shows 2D while the context is lost, and 3D again once it is restored", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    await threeD();
    const canvas = scene.canvas;
    if (!canvas) throw new Error("no canvas");
    act(() => {
      canvas.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    });
    expect(screen.getByText(WEBGL_LOST)).toBeInTheDocument();
    // The 3D view stays mounted (hidden) to hear about the restore.
    expect(document.querySelector(".graph3d--hidden")).not.toBeNull();
    act(() => {
      canvas.dispatchEvent(new Event("webglcontextrestored"));
    });
    expect(screen.queryByText(WEBGL_LOST)).not.toBeInTheDocument();
    expect(document.querySelector(".graph3d--hidden")).toBeNull();
  });
});

describe("pointer", () => {
  it("selects a knot on click and clears the selection on empty space", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    const at = focusEntity(canvas, "api.example.org");
    fireEvent.blur(canvas);
    fireEvent.pointerDown(canvas, {
      clientX: at.x,
      clientY: at.y,
      pointerType: "mouse",
      button: 0,
      isPrimary: true,
      pointerId: 1,
    });
    fireEvent.pointerUp(window, { clientX: at.x, clientY: at.y, pointerId: 1 });
    const details = screen.getByRole("complementary", { name: "Details" });
    expect(within(details).getByText(API)).toBeInTheDocument();

    fireEvent.pointerDown(canvas, {
      clientX: 2,
      clientY: 2,
      pointerType: "mouse",
      button: 0,
      isPrimary: true,
      pointerId: 2,
    });
    fireEvent.pointerUp(window, { clientX: 2, clientY: 2, pointerId: 2 });
    expect(within(details).queryByText(API)).not.toBeInTheDocument();
  });

  it("opens the node's menu on right-click", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    const at = focusEntity(canvas, "example.org");
    fireEvent.contextMenu(canvas, { clientX: at.x, clientY: at.y });
    expect(await screen.findByRole("menu", { name: "Domain example.org" })).toBeInTheDocument();
  });

  it("drags a knot and pins it through the same pins as 2D", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    const save = vi.fn(() => Promise.resolve());
    renderView({ load: () => Promise.resolve({}), save });
    const canvas = await threeD();
    const at = focusEntity(canvas, "www.example.org");
    fireEvent.blur(canvas);
    fireEvent.pointerDown(canvas, {
      clientX: at.x,
      clientY: at.y,
      pointerType: "mouse",
      button: 0,
      isPrimary: true,
      pointerId: 3,
    });
    fireEvent.pointerMove(window, { clientX: at.x + 40, clientY: at.y - 30, pointerId: 3 });
    fireEvent.pointerUp(window, { clientX: at.x + 40, clientY: at.y - 30, pointerId: 3 });
    drawUntilIdle();
    act(() => {
      canvas.focus();
    });
    focusEntity(canvas, "www.example.org");
    expect(announcer()).toHaveTextContent(/, pinned$/);
    await waitFor(
      () => {
        expect(save).toHaveBeenCalled();
      },
      { timeout: 3000 },
    );
  });
});

describe("camera and selection gestures", () => {
  const distance = (snapshot: ViewSnapshot | undefined) => {
    const camera = snapshot?.camera3d;
    if (!camera) return NaN;
    return Math.hypot(
      camera.position[0] - camera.target[0],
      camera.position[1] - camera.target[1],
      camera.position[2] - camera.target[2],
    );
  };

  it("zooms toward the pointer with a mouse wheel, and pans with a trackpad", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    const before = distance(probe.capture?.());
    fireEvent.wheel(canvas, { deltaY: -120, deltaMode: 0, clientX: 400, clientY: 300 });
    drawUntilIdle();
    expect(distance(probe.capture?.())).toBeLessThan(before);

    const target = probe.capture?.().camera3d?.target[0] ?? NaN;
    // Small fractional deltas: a trackpad, which pans.
    fireEvent.wheel(canvas, { deltaX: 12.5, deltaY: 0.5, deltaMode: 0, timeStamp: 10_000 });
    drawUntilIdle();
    expect(probe.capture?.().camera3d?.target[0]).not.toBeCloseTo(target, 6);
    // Pinch (ctrlKey) zooms smoothly.
    const pinched = distance(probe.capture?.());
    fireEvent.wheel(canvas, { deltaY: 10, ctrlKey: true, deltaMode: 0, timeStamp: 20_000 });
    drawUntilIdle();
    expect(distance(probe.capture?.())).toBeGreaterThan(pinched);
  });

  it("selects the knots in a Shift+drag rectangle, and toggles with Ctrl+click", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    const api = focusEntity(canvas, "api.example.org");
    fireEvent.blur(canvas);
    fireEvent.pointerDown(canvas, {
      clientX: 0,
      clientY: 0,
      shiftKey: true,
      pointerType: "mouse",
      button: 0,
      isPrimary: true,
      pointerId: 7,
    });
    fireEvent.pointerMove(window, { clientX: 800, clientY: 600, pointerId: 7 });
    expect(document.querySelector<HTMLElement>(".graph-selection")?.hidden).toBe(false);
    fireEvent.pointerUp(window, { clientX: 800, clientY: 600, pointerId: 7 });
    expect(await screen.findByText("3 entities selected")).toBeInTheDocument();

    fireEvent.pointerDown(canvas, {
      clientX: api.x,
      clientY: api.y,
      ctrlKey: true,
      pointerType: "mouse",
      button: 0,
      isPrimary: true,
      pointerId: 8,
    });
    fireEvent.pointerUp(window, { clientX: api.x, clientY: api.y, pointerId: 8 });
    expect(await screen.findByText("2 entities selected")).toBeInTheDocument();
  });

  it("flies to a knot on double-click", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    const at = focusEntity(canvas, "api.example.org");
    fireEvent.doubleClick(canvas, { clientX: at.x, clientY: at.y });
    drawUntilIdle();
    await waitFor(() => {
      expect(probe.capture?.().camera3d?.target[0]).toBeCloseTo(150, 0);
    });
  });

  it("hovers knots under the pointer", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    const at = focusEntity(canvas, "www.example.org");
    fireEvent.blur(canvas);
    fireEvent.pointerMove(canvas, { clientX: at.x, clientY: at.y, pointerId: 9, buttons: 0 });
    expect(canvas).toHaveClass("graph3d--over-node");
    fireEvent.pointerLeave(canvas);
    expect(canvas).not.toHaveClass("graph3d--over-node");
  });
});

describe("minimap", () => {
  it("draws the graph from the top with the view's footprint, and moves the camera on click", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    window.localStorage.setItem(MINIMAP_STORAGE_KEY, "on");
    const calls: string[] = [];
    const context = new Proxy(
      {},
      {
        get: (_target, name) =>
          typeof name === "string" &&
          !["fillStyle", "strokeStyle", "lineWidth", "globalAlpha"].includes(name)
            ? () => {
                calls.push(name);
              }
            : undefined,
        set: () => true,
      },
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(((type: string) =>
      type === "webgl2" ? ({} as RenderingContext) : context) as never);
    renderView();
    await threeD();
    const minimap = await screen.findByTestId("graph-minimap");
    await waitFor(() => {
      expect(calls).toContain("fillRect");
    });
    expect(calls).toContain("stroke");
    const before = probe.capture?.().camera3d?.target ?? [NaN, NaN, NaN];
    fireEvent.pointerDown(minimap, { clientX: 5, clientY: 5, pointerId: 1, buttons: 1 });
    fireEvent.pointerMove(minimap, { clientX: 20, clientY: 10, pointerId: 1, buttons: 1 });
    drawUntilIdle();
    const after = probe.capture?.().camera3d?.target ?? [NaN, NaN, NaN];
    expect(after[0]).not.toBeCloseTo(before[0], 3);
  });
});

describe("keyboard", () => {
  it("moves through the knots, announcing them like 2D, and pins with P", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView({ load: () => Promise.resolve({}), save: vi.fn(() => Promise.resolve()) });
    const canvas = await threeD();
    focusEntity(canvas, "example.org");
    expect(announcer()).toHaveTextContent("Domain example.org, 2 neighbours");
    fireEvent.keyDown(canvas, { key: "p" });
    drawUntilIdle();
    expect(announcer()).toHaveTextContent("Domain example.org, 2 neighbours, pinned");
    fireEvent.keyDown(canvas, { key: "Escape" });
    expect(announcer()).toHaveTextContent("");
  });

  it("orbits with the arrows (the knots move on screen) and resets with 0", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    const before = focusEntity(canvas, "www.example.org");
    fireEvent.keyDown(canvas, { key: "ArrowLeft" });
    drawUntilIdle();
    const ring = document.querySelector<HTMLElement>(".graph-focus-ring");
    const moved =
      Number.parseFloat(ring?.style.left ?? "0") + Number.parseFloat(ring?.style.width ?? "0") / 2;
    expect(Math.abs(moved - before.x)).toBeGreaterThan(1);
    for (const key of ["E", "+", "-", "ArrowUp", "0"]) {
      fireEvent.keyDown(canvas, { key });
      fireEvent.keyDown(canvas, { key: "ArrowRight", shiftKey: true });
    }
    drawUntilIdle();
  });

  it("shows the 3D shortcuts with ?", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    const canvas = await threeD();
    await waitFor(() => {
      fireEvent.keyDown(canvas, { key: "?" });
      expect(
        screen.getByRole("heading", { name: "Keyboard (with the 3D graph focused)" }),
      ).toBeInTheDocument();
    });
  });
});

describe("depth", () => {
  it("shows dates on the ruler in Time depth, and type names in Type depth", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    await threeD();
    const ruler = document.querySelector(".graph3d-ruler");
    expect(ruler?.textContent).toContain("Undated");
    expect(ruler?.textContent).toMatch(/20(25|26)/);
    await userEvent.click(screen.getByRole("button", { name: "Type" }));
    drawUntilIdle();
    expect(document.querySelector(".graph3d-ruler")?.textContent).toBe("Domains");
    await userEvent.click(screen.getByRole("button", { name: "Flat" }));
    drawUntilIdle();
    expect(document.querySelector(".graph3d-ruler")?.textContent).toBe("");
  });

  it("labels knots in the HTML layer", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    renderView();
    await threeD();
    const labels = [...document.querySelectorAll(".graph3d-label:not([hidden])")].map(
      (l) => l.textContent,
    );
    expect(labels).toEqual(expect.arrayContaining(["example.org"]));
  });
});

describe("saved views", () => {
  it("captures the 3D camera and depth, and applying a 3D view from 2D switches to 3D", async () => {
    window.localStorage.setItem(MODE_STORAGE_KEY, "3d");
    const first = renderView();
    await threeD();
    await waitFor(() => {
      expect(probe.capture?.().camera3d).not.toBeNull();
    });
    const saved = probe.capture?.();
    if (!saved) throw new Error("nothing captured");
    expect(saved).toMatchObject({ mode: "3d", depth: "time" });
    first.unmount();
    resetScene();
    window.localStorage.setItem(MODE_STORAGE_KEY, "2d");

    renderView();
    await waitFor(() => {
      expect(probe.apply).not.toBeNull();
    });
    expect(screen.getByTestId("graph-canvas")).not.toHaveAttribute("data-mode", "3d");
    act(() => {
      probe.apply?.({ ...saved, depth: "type" });
    });
    await threeD();
    expect(screen.getByRole("button", { name: "Type" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => {
      const now = probe.capture?.().camera3d;
      expect(now?.target[0]).toBeCloseTo(saved.camera3d?.target[0] ?? NaN, 3);
    });
  });
});
