/**
 * The 2D graph's interactions through GraphView, with sigma mocked (see
 * src/test/sigmaMock.ts): drag to pin and save, unpin, multi-selection and
 * moving it, the selection rectangle, the node menu, the View menu, the
 * minimap, the keyboard and what it announces, the wheel and rotation.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sampleGraph } from "../demo/sampleGraph";
import type { InvestigationGraph } from "../domain/entities";
import { FakeSigma, sigmaEvent, waitForSigma } from "../test/sigmaMock";
import { ThemeProvider } from "../theme/ThemeProvider";
import type { NightloomGraph } from "./buildGraph";
import { GraphView } from "./GraphView";
import { PAN_STEP_PX } from "./keyboard";
import type { Positions } from "./pins";
import { WHEEL_STORAGE_KEY } from "./preferences";
import type { LayoutPersistence } from "./usePins";

vi.mock("sigma", () => import("../test/sigmaMock"));

const WWW = "sub:www.example.org";
const API = "sub:api.example.org";
const APEX = "domain:example.org";

function persistence(saved: Positions = {}, save = vi.fn(() => Promise.resolve())) {
  const layout: LayoutPersistence = {
    load: vi.fn(() => Promise.resolve(saved)),
    save,
  };
  return { layout, save };
}

async function renderView({
  layout,
  data = sampleGraph,
}: { layout?: LayoutPersistence; data?: InvestigationGraph } = {}) {
  render(
    <ThemeProvider>
      <GraphView data={data} overview={() => <p>Overview</p>} {...(layout ? { layout } : {})} />
    </ThemeProvider>,
  );
  const sigma = await waitForSigma();
  return { sigma, graph: sigma.graph as unknown as NightloomGraph };
}

/** Drags `nodes` (starting on the first) by `dx`, `dy` graph units, as sigma does. */
function drag(sigma: FakeSigma, graph: NightloomGraph, nodes: string[], dx = 30, dy = -20) {
  act(() => {
    sigma.emit("nodeDragStart", { node: nodes[0], allDraggedNodes: nodes });
    for (const node of nodes) {
      const { x, y } = graph.getNodeAttributes(node);
      graph.mergeNodeAttributes(node, { x: x + dx, y: y + dy });
    }
    sigma.emit("nodeDragEnd", { node: nodes[0], allDraggedNodes: nodes });
  });
}

const ctrlClick = () => sigmaEvent({ x: 0, y: 0 }, new MouseEvent("click", { ctrlKey: true }));

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  FakeSigma.reset();
  window.localStorage.clear();
});

describe("dragging and pinning", () => {
  it("pins a dragged node where it was dropped and saves it after a pause", async () => {
    const { layout, save } = persistence();
    const { sigma, graph } = await renderView({ layout });
    await waitFor(() => {
      expect(layout.load).toHaveBeenCalled();
    });

    drag(sigma, graph, [WWW]);

    // The camera never moved, and the frame was frozen: nothing else shifted, then or after.
    expect(sigma.camera.setState).not.toHaveBeenCalled();
    expect(sigma.setCustomBBox).toHaveBeenCalledTimes(1);
    expect(sigma.setCustomBBox).toHaveBeenCalledWith(sigma.getBBox());
    // A second drag keeps the same frame; Reset view fits the graph again.
    drag(sigma, graph, [API]);
    expect(sigma.setCustomBBox).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(sigma.setCustomBBox).toHaveBeenLastCalledWith(null);
    const { pinned, fixed } = graph.getNodeAttributes(WWW);
    expect([pinned, fixed]).toEqual([true, true]);
    expect(sigma.nodeView(WWW)["pinSize"]).toBeGreaterThan(0);
    expect(save).not.toHaveBeenCalled();
    const www = graph.getNodeAttributes(WWW);
    const api = graph.getNodeAttributes(API);
    await waitFor(() => {
      expect(save).toHaveBeenCalledWith(
        { [WWW]: [www.x, www.y], [API]: [api.x, api.y] },
        { keepalive: false },
      );
    });
  });

  it("keeps the node under the pointer from the first move of a drag", async () => {
    const { sigma, graph } = await renderView();
    const before = { ...graph.getNodeAttributes(WWW) };
    const down = sigma.screenOf(WWW);
    act(() => {
      sigma.emit("downNode", { node: WWW, event: sigmaEvent(down) });
      // sigma starts the drag on the first move, 12 px further on.
      sigma.emit("nodeDragStart", {
        node: WWW,
        allDraggedNodes: [WWW],
        event: sigmaEvent({ x: down.x + 12, y: down.y }),
      });
    });
    const after = sigma.screenOf(WWW);
    expect(after.x).toBeCloseTo(down.x + 12);
    expect(after.y).toBeCloseTo(down.y);
    expect(graph.getNodeAttribute(WWW, "y")).toBeCloseTo(before.y);
  });

  it("puts saved pins back where they were when the graph opens", async () => {
    const { layout } = persistence({ [WWW]: [123, -45] });
    const { graph } = await renderView({ layout });
    await waitFor(() => {
      expect(graph.getNodeAttributes(WWW)).toMatchObject({ x: 123, y: -45, pinned: true });
    });
  });

  it("says when the layout could not be saved, and keeps retrying", async () => {
    const save = vi.fn(() => Promise.reject(new Error("down")));
    const { layout } = persistence({}, save);
    const { sigma, graph } = await renderView({ layout });
    await waitFor(() => {
      expect(layout.load).toHaveBeenCalled();
    });
    drag(sigma, graph, [WWW]);
    expect(await screen.findByRole("status", {}, { timeout: 3000 })).toHaveTextContent(
      "Couldn’t save layout — retrying",
    );
    // Nothing was lost: the node stays pinned.
    expect(graph.getNodeAttribute(WWW, "pinned")).toBe(true);
  });

  it("saves the pins at once when the page is hidden", async () => {
    const { layout, save } = persistence();
    const { sigma, graph } = await renderView({ layout });
    await waitFor(() => {
      expect(layout.load).toHaveBeenCalled();
    });
    drag(sigma, graph, [WWW]);
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ [WWW]: expect.any(Array) }), {
      keepalive: true,
    });
  });

  it("unpins from the side panel, and pins again", async () => {
    const { layout, save } = persistence({ [WWW]: [10, 10] });
    const { sigma, graph } = await renderView({ layout });
    await waitFor(() => {
      expect(graph.getNodeAttribute(WWW, "pinned")).toBe(true);
    });
    act(() => {
      sigma.emit("clickNode", { node: WWW });
    });
    const panel = screen.getByRole("complementary", { name: "Details" });
    await userEvent.click(within(panel).getByRole("button", { name: "Unpin" }));
    expect(graph.getNodeAttribute(WWW, "pinned")).toBeUndefined();
    expect(graph.getNodeAttribute(WWW, "fixed")).toBeUndefined();
    await waitFor(() => {
      expect(save).toHaveBeenLastCalledWith({}, { keepalive: false });
    });
    await userEvent.click(within(panel).getByRole("button", { name: "Pin" }));
    expect(graph.getNodeAttribute(WWW, "pinned")).toBe(true);
  });

  it("keeps pins for the page only when there is nowhere to save them (the demo)", async () => {
    const { sigma, graph } = await renderView();
    drag(sigma, graph, [WWW]);
    expect(graph.getNodeAttribute(WWW, "pinned")).toBe(true);
  });
});

describe("selecting several entities", () => {
  it("toggles entities with Ctrl/Cmd+click and moves the selection together", async () => {
    const { sigma, graph } = await renderView();
    act(() => {
      sigma.emit("clickNode", { node: WWW });
    });
    act(() => {
      sigma.emit("clickNode", { node: API, event: ctrlClick() });
    });
    const panel = screen.getByRole("complementary", { name: "Details" });
    expect(within(panel).getByRole("heading", { name: "2 entities selected" })).toBeVisible();
    expect(sigma.nodeView(WWW)["haloSize"]).toBeGreaterThan(0);
    expect(sigma.nodeView(API)["haloSize"]).toBeGreaterThan(0);

    // Dragging one selected node drags them all.
    const draggedNodes = sigma.getSetting("getDraggedNodes") as (node: string) => string[];
    expect(draggedNodes(API).sort()).toEqual([API, WWW].sort());
    expect(draggedNodes(APEX)).toEqual([APEX]);
    drag(sigma, graph, [WWW, API]);
    expect(graph.getNodeAttribute(WWW, "pinned")).toBe(true);
    expect(graph.getNodeAttribute(API, "pinned")).toBe(true);
    expect(within(panel).getByText(/2 pinned/)).toBeVisible();

    await userEvent.click(within(panel).getByRole("button", { name: "Unpin all" }));
    expect(graph.getNodeAttribute(WWW, "pinned")).toBeUndefined();
    await userEvent.click(within(panel).getByRole("button", { name: "Pin all" }));
    expect(graph.getNodeAttribute(API, "pinned")).toBe(true);

    // Ctrl+click again takes one out: a single selection is left.
    act(() => {
      sigma.emit("clickNode", { node: API, event: ctrlClick() });
    });
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent("www.example.org");
    act(() => {
      sigma.emit("clickNode", { node: API, event: ctrlClick() });
    });
    await userEvent.click(within(panel).getByRole("button", { name: "Clear" }));
    expect(within(panel).getByText("Overview")).toBeVisible();
  });

  it("selects what a Shift+drag rectangle covers, and a stage click right after does not clear it", async () => {
    const { sigma, graph } = await renderView();
    // Spread three nodes apart on screen and draw a box around two of them.
    act(() => {
      graph.mergeNodeAttributes(WWW, { x: -300, y: 200 });
      graph.mergeNodeAttributes(API, { x: -250, y: 220 });
      graph.mergeNodeAttributes(APEX, { x: 300, y: -200 });
    });
    const www = sigma.screenOf(WWW);
    const api = sigma.screenOf(API);
    const start = { x: Math.min(www.x, api.x) - 10, y: Math.min(www.y, api.y) - 10 };
    const end = { x: Math.max(www.x, api.x) + 10, y: Math.max(www.y, api.y) + 10 };
    const prevented = sigmaEvent(end, new MouseEvent("mousemove"));
    act(() => {
      sigma.emit("downStage", {
        event: sigmaEvent(start, new MouseEvent("mousedown", { button: 0, shiftKey: true })),
      });
      sigma.emit("moveBody", { event: prevented });
    });
    // While drawing, the stage does not pan, and the rectangle shows.
    expect(prevented.sigmaDefaultPrevented).toBe(true);
    const box = document.querySelector<HTMLElement>(".graph-selection");
    expect(box?.hidden).toBe(false);
    act(() => {
      window.dispatchEvent(new MouseEvent("mouseup", { clientX: end.x, clientY: end.y }));
      sigma.emit("clickStage");
    });
    expect(box?.hidden).toBe(true);
    const panel = screen.getByRole("complementary", { name: "Details" });
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent(
      /^\d+ entities selected$/,
    );
    expect(sigma.nodeView(WWW)["haloSize"]).toBeGreaterThan(0);
    expect(sigma.nodeView(API)["haloSize"]).toBeGreaterThan(0);
    expect(sigma.nodeView(APEX)["haloSize"]).toBe(0);
  });

  it("selects under rotation what is inside the rectangle on screen", async () => {
    const { sigma, graph } = await renderView();
    act(() => {
      graph.forEachNode((node) => {
        graph.mergeNodeAttributes(node, { x: 1000, y: 1000 });
      });
      // A row along the graph's x axis; turned a quarter, it runs down the screen.
      graph.mergeNodeAttributes(WWW, { x: -100, y: 0 });
      graph.mergeNodeAttributes(API, { x: 0, y: 0 });
      graph.mergeNodeAttributes(APEX, { x: 100, y: 0 });
      sigma.camera.setState({ angle: Math.PI / 2 });
    });
    // A tall, narrow box down the middle of the screen.
    act(() => {
      sigma.emit("downStage", {
        event: sigmaEvent({ x: 390, y: 50 }, new MouseEvent("mousedown", { shiftKey: true })),
      });
      window.dispatchEvent(new MouseEvent("mouseup", { clientX: 410, clientY: 550 }));
    });
    expect(screen.getByRole("heading", { name: "3 entities selected" })).toBeVisible();
  });
});

describe("node menu", () => {
  it("opens on right-click with pin, select neighbours and hide type", async () => {
    const { sigma, graph } = await renderView();
    act(() => {
      sigma.emit("rightClickNode", { node: APEX, event: sigmaEvent({ x: 120, y: 80 }) });
    });
    const menu = screen.getByRole("menu", { name: "Domain example.org" });
    expect(within(menu).getByRole("menuitem", { name: "Pin" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(within(menu).getByRole("menuitem", { name: "Select neighbours" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: `${String(graph.degree(APEX) + 1)} entities selected` }),
    ).toBeVisible();

    act(() => {
      sigma.emit("rightClickNode", { node: APEX, event: sigmaEvent({ x: 120, y: 80 }) });
    });
    await userEvent.click(screen.getByRole("menuitem", { name: "Pin" }));
    expect(graph.getNodeAttribute(APEX, "pinned")).toBe(true);

    act(() => {
      sigma.emit("rightClickNode", { node: APEX, event: sigmaEvent({ x: 120, y: 80 }) });
    });
    expect(screen.getByRole("menuitem", { name: "Unpin" })).toBeVisible();
    await userEvent.click(screen.getByRole("menuitem", { name: "Hide Domains" }));
    expect(sigma.nodeView(APEX)["visibility"]).toBe("hidden");
  });

  it("closes with Escape", async () => {
    const { sigma } = await renderView();
    act(() => {
      sigma.emit("rightClickNode", { node: APEX });
    });
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("opens on a long press on a touch screen", async () => {
    // Rendered before faking timers: waiting for sigma needs real ones.
    const { sigma } = await renderView();
    vi.useFakeTimers();
    try {
      const touch = sigmaEvent({ x: 50, y: 60 }, new TouchEvent("touchstart"));
      act(() => {
        sigma.emit("downNode", { node: WWW, event: touch });
        vi.advanceTimersByTime(600);
      });
      expect(screen.getByRole("menu", { name: "Domain www.example.org" })).toBeVisible();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not open when the finger moves or lifts first", async () => {
    // Rendered before faking timers: waiting for sigma needs real ones.
    const { sigma } = await renderView();
    vi.useFakeTimers();
    try {
      act(() => {
        sigma.emit("downNode", {
          node: WWW,
          event: sigmaEvent({ x: 0, y: 0 }, new TouchEvent("touchstart")),
        });
        sigma.emit("moveBody", { event: sigmaEvent({ x: 40, y: 0 }, new TouchEvent("touchmove")) });
        sigma.emit("downNode", {
          node: WWW,
          event: sigmaEvent({ x: 0, y: 0 }, new TouchEvent("touchstart")),
        });
        sigma.emit("upNode", { node: WWW });
        vi.advanceTimersByTime(1000);
      });
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("View menu", () => {
  it("toggles the minimap, sets the scroll wheel and unpins everything after confirming", async () => {
    const { sigma, graph } = await renderView();
    drag(sigma, graph, [WWW, API]);
    const view = screen.getByRole("button", { name: "View" });
    await userEvent.click(view);
    const menu = screen.getByRole("menu", { name: "View" });
    const minimap = within(menu).getByRole("menuitemcheckbox", { name: "Minimap" });
    // Small graph: off by default.
    expect(minimap).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByTestId("graph-minimap")).not.toBeInTheDocument();
    await userEvent.click(minimap);
    expect(minimap).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("graph-minimap")).toHaveAttribute("aria-hidden", "true");

    expect(within(menu).getByRole("menuitemradio", { name: "Automatic" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await userEvent.click(within(menu).getByRole("menuitemradio", { name: "Pan" }));
    expect(within(menu).getByRole("menuitemradio", { name: "Pan" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(window.localStorage.getItem(WHEEL_STORAGE_KEY)).toBe("pan");

    await userEvent.click(within(menu).getByRole("menuitem", { name: "Unpin all (2)…" }));
    const dialog = screen.getByRole("dialog", { name: "Unpin all entities?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Unpin all" }));
    expect(graph.getNodeAttribute(WWW, "pinned")).toBeUndefined();
    expect(graph.getNodeAttribute(API, "pinned")).toBeUndefined();
  });

  it("moves between items with the arrow keys and closes with Escape", async () => {
    await renderView();
    const view = screen.getByRole("button", { name: "View" });
    await userEvent.click(view);
    expect(screen.getByRole("menuitemcheckbox", { name: "Minimap" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitemradio", { name: "Automatic" })).toHaveFocus();
    await userEvent.keyboard("{End}");
    // Unpin all is disabled with nothing pinned: the last enabled item.
    expect(screen.getByRole("menuitemradio", { name: "Pan" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(view).toHaveFocus();
  });

  it("shows the minimap by default for more than 60 entities", async () => {
    const many: InvestigationGraph = {
      nodes: Array.from({ length: 61 }, (_, i) => ({
        ...sampleGraph.nodes[0]!,
        id: `domain:host-${String(i)}.example.org`,
        label: `host-${String(i)}.example.org`,
      })),
      edges: [],
    };
    await renderView({ data: many });
    expect(screen.getByTestId("graph-minimap")).toBeInTheDocument();
  });
});

describe("minimap", () => {
  it("moves the camera to where it is clicked", async () => {
    const context = {
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      closePath: vi.fn(),
      stroke: vi.fn(),
      fill: vi.fn(),
      setTransform: vi.fn(),
    };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      context as unknown as CanvasRenderingContext2D,
    );
    const { sigma } = await renderView();
    await userEvent.click(screen.getByRole("button", { name: "View" }));
    await userEvent.click(screen.getByRole("menuitemcheckbox", { name: "Minimap" }));
    await waitFor(() => {
      expect(context.fillRect).toHaveBeenCalled();
    });
    const before = { x: sigma.camera.x, y: sigma.camera.y };
    // The top-left corner of the map: further left and up in the graph.
    fireEvent.pointerDown(screen.getByTestId("graph-minimap"), {
      clientX: 8,
      clientY: 8,
      pointerId: 1,
      buttons: 1,
    });
    expect(sigma.camera.x).toBeLessThan(before.x);
    expect(sigma.camera.y).toBeGreaterThan(before.y);
  });
});

describe("keyboard", () => {
  function canvas() {
    return screen.getByRole("application", { name: "Investigation graph" });
  }

  it("is a focusable application with a description and a help dialog", async () => {
    await renderView();
    const graphCanvas = canvas();
    expect(graphCanvas).toHaveAttribute("tabindex", "0");
    expect(graphCanvas).toHaveAccessibleDescription(/Tab moves through the entities/);
    graphCanvas.focus();
    await userEvent.keyboard("?");
    const dialog = screen.getByRole("dialog", { name: "Graph controls" });
    expect(within(dialog).getByText("Rotate left / right by 15°")).toBeVisible();
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByRole("button", { name: "Keyboard and mouse shortcuts" }));
    expect(screen.getByRole("dialog", { name: "Graph controls" })).toBeVisible();
  });

  it("pans, zooms, rotates and resets", async () => {
    const { sigma } = await renderView();
    canvas().focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(sigma.camera.x).toBeCloseTo(PAN_STEP_PX);
    await userEvent.keyboard("{ArrowUp}");
    expect(sigma.camera.y).toBeCloseTo(PAN_STEP_PX);
    await userEvent.keyboard("+");
    expect(sigma.camera.ratio).toBeLessThan(1);
    await userEvent.keyboard("-");
    expect(sigma.camera.ratio).toBeCloseTo(1);
    await userEvent.keyboard("e");
    expect(sigma.camera.angle).toBeCloseTo(Math.PI / 12);
    await userEvent.keyboard("qq");
    expect(sigma.camera.angle).toBeCloseTo(-Math.PI / 12);
    await userEvent.keyboard("0");
    expect(sigma.camera.getState()).toEqual({ x: 0, y: 0, ratio: 1, angle: 0 });
  });

  it("moves a focus ring through the entities, announces them, selects and pins", async () => {
    const { sigma, graph } = await renderView();
    // Put two nodes at the top-left of the screen, the rest further down.
    act(() => {
      graph.forEachNode((node, { x }) => {
        graph.mergeNodeAttributes(node, { y: -250 - (x % 7) });
      });
      graph.mergeNodeAttributes(APEX, { x: -350, y: 250 });
      graph.mergeNodeAttributes(WWW, { x: -100, y: 250 });
    });
    canvas().focus();
    const announcer = screen.getByTestId("graph-announcer");
    await userEvent.keyboard("{Tab}");
    expect(announcer).toHaveTextContent(
      `Domain example.org, ${String(graph.degree(APEX))} neighbours`,
    );
    expect(document.querySelector<HTMLElement>(".graph-focus-ring")?.hidden).toBe(false);
    // The focused entity's neighbourhood is highlighted, like a hover.
    expect(sigma.nodeView(APEX)["zIndex"]).toBe(1);
    await userEvent.keyboard("{Tab}");
    expect(announcer).toHaveTextContent(/^Domain www\.example\.org, \d+ neighbours?$/);
    await userEvent.keyboard("{Shift>}{Tab}{/Shift}");
    expect(announcer).toHaveTextContent(/^Domain example\.org/);

    await userEvent.keyboard("p");
    expect(announcer).toHaveTextContent(/^Domain example\.org, \d+ neighbours, pinned$/);
    expect(graph.getNodeAttribute(APEX, "pinned")).toBe(true);

    // Shift+arrows move a pinned node by 10 px on screen.
    const before = sigma.screenOf(APEX);
    await userEvent.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(sigma.screenOf(APEX).x).toBeCloseTo(before.x + 10);
    expect(sigma.camera.x).toBe(0);

    await userEvent.keyboard("{Enter}");
    const panel = screen.getByRole("complementary", { name: "Details" });
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent("example.org");

    // Escape leaves the node, then clears the selection.
    await userEvent.keyboard("{Escape}");
    expect(announcer).toHaveTextContent("");
    expect(canvas()).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(within(panel).getByText("Overview")).toBeVisible();
  });

  it("asks to pin before moving an entity with Shift+arrows", async () => {
    await renderView();
    canvas().focus();
    await userEvent.keyboard("{Tab}{Shift>}{ArrowLeft}{/Shift}");
    expect(screen.getByTestId("graph-announcer")).toHaveTextContent(/Pin it first \(P\)/);
  });

  it("lets Tab leave the graph after the last entity", async () => {
    const small: InvestigationGraph = { nodes: sampleGraph.nodes.slice(0, 1), edges: [] };
    render(
      <ThemeProvider>
        <GraphView data={small} overview={() => <p>Overview</p>} />
        <button type="button">After</button>
      </ThemeProvider>,
    );
    await waitForSigma();
    canvas().focus();
    await userEvent.keyboard("{Tab}");
    expect(screen.getByTestId("graph-announcer")).not.toHaveTextContent("");
    await userEvent.tab();
    expect(canvas()).not.toHaveFocus();
    expect(screen.getByTestId("graph-announcer")).toHaveTextContent("");
  });
});

describe("wheel, trackpad and rotation", () => {
  function wheel(init: WheelEventInit, timeStamp = 0) {
    const target = screen.getByTestId("graph-canvas");
    const event = new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init });
    // jsdom ignores timeStamp in the init.
    Object.defineProperty(event, "timeStamp", { value: timeStamp });
    act(() => {
      target.dispatchEvent(event);
    });
    return event;
  }

  it("zooms with a mouse wheel and pans with a trackpad, never scrolling the page", async () => {
    const { sigma } = await renderView();
    const event = wheel({ deltaY: 100 });
    expect(event.defaultPrevented).toBe(true);
    expect(sigma.camera.ratio).toBeGreaterThan(1);
    const ratio = sigma.camera.ratio;
    // A trackpad, after a pause: pans by its deltas.
    const x = sigma.camera.x;
    wheel({ deltaX: 12.5, deltaY: 3.5 }, 10_000);
    expect(sigma.camera.ratio).toBe(ratio);
    expect(sigma.camera.x).not.toBe(x);
  });

  it("zooms toward the pointer on a pinch (Ctrl+wheel)", async () => {
    const { sigma } = await renderView();
    wheel({ deltaY: -10, ctrlKey: true, clientX: 700, clientY: 100 });
    expect(sigma.camera.ratio).toBeLessThan(1);
    // Toward the top-right: the camera moved that way.
    expect(sigma.camera.x).toBeGreaterThan(0);
    expect(sigma.camera.y).toBeGreaterThan(0);
  });

  it("follows the Scroll wheel setting", async () => {
    window.localStorage.setItem(WHEEL_STORAGE_KEY, "pan");
    const { sigma } = await renderView();
    wheel({ deltaY: 100 });
    expect(sigma.camera.ratio).toBe(1);
    expect(sigma.camera.y).toBeLessThan(0);
    await userEvent.click(screen.getByRole("button", { name: "View" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Zoom" }));
    wheel({ deltaY: 3 }, 10_000);
    expect(sigma.camera.ratio).toBeGreaterThan(1);
  });

  it("rotates with Alt+drag and right-button drag on the stage, not with a plain drag", async () => {
    const { sigma } = await renderView();
    const rotate = (init: MouseEventInit) => {
      const move = sigmaEvent({ x: 400, y: 500 }, new MouseEvent("mousemove"));
      act(() => {
        sigma.emit("downStage", {
          event: sigmaEvent({ x: 600, y: 300 }, new MouseEvent("mousedown", init)),
        });
        sigma.emit("moveBody", { event: move });
        window.dispatchEvent(new MouseEvent("mouseup"));
      });
      return move;
    };
    const altMove = rotate({ button: 0, altKey: true });
    expect(altMove.sigmaDefaultPrevented).toBe(true);
    // From the right of the center to below it: a quarter turn clockwise.
    expect(sigma.camera.angle).toBeCloseTo(Math.PI / 2);
    sigma.camera.setState({ angle: 0 });
    rotate({ button: 2 });
    expect(sigma.camera.angle).toBeCloseTo(Math.PI / 2);
    sigma.camera.setState({ angle: 0 });
    const plain = rotate({ button: 0 });
    expect(plain.sigmaDefaultPrevented).toBe(false);
    expect(sigma.camera.angle).toBe(0);
  });

  it("keeps the browser's menu closed over the graph", async () => {
    await renderView();
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    screen.getByTestId("graph-canvas").dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it("pinches and twists with Safari's gesture events", async () => {
    const { sigma } = await renderView();
    const gesture = (type: string, extra: Record<string, number> = {}) => {
      const event = Object.assign(new Event(type, { cancelable: true }), {
        scale: 1,
        rotation: 0,
        clientX: 400,
        clientY: 300,
        ...extra,
      });
      act(() => {
        screen.getByTestId("graph-canvas").dispatchEvent(event);
      });
    };
    gesture("gesturestart");
    gesture("gesturechange", { scale: 2, rotation: 90 });
    expect(sigma.camera.ratio).toBeCloseTo(0.5);
    expect(sigma.camera.angle).toBeCloseTo(Math.PI / 2);
    gesture("gestureend");
    gesture("gesturechange", { scale: 4 });
    expect(sigma.camera.ratio).toBeCloseTo(0.5);
  });
});

describe("canvas labels", () => {
  it("shortens long labels on the canvas, not in the side panel", async () => {
    const long = "https://www.example.org/a/very/long/path/to/an/archived/page.html";
    const data: InvestigationGraph = {
      nodes: [{ ...sampleGraph.nodes[0]!, id: "url:long", label: long, type: "url" }],
      edges: [],
    };
    const { sigma } = await renderView({ data });
    expect(sigma.nodeView("url:long")["label"]).toHaveLength(32);
    act(() => {
      sigma.emit("clickNode", { node: "url:long" });
    });
    expect(screen.getByRole("heading", { name: long })).toBeVisible();
  });
});
