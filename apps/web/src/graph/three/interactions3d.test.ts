/**
 * The 3D view's pointer interactions on their own: synthetic pointer, wheel
 * and gesture events on a container, with every decision the module hands
 * back recorded.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LONG_PRESS_MS, ZOOM_STEP } from "../interactions";
import { attachInteractions3d, NOTCH_GAP_MS, type Interaction3DOptions } from "./interactions3d";

interface PointerInit {
  x?: number;
  y?: number;
  id?: number;
  type?: "mouse" | "touch" | "pen";
  primary?: boolean;
  button?: number;
  buttons?: number;
  shift?: boolean;
  ctrl?: boolean;
  target?: EventTarget;
}

function pointer(name: string, init: PointerInit = {}): MouseEvent {
  const event = new MouseEvent(name, {
    bubbles: true,
    cancelable: true,
    clientX: init.x ?? 0,
    clientY: init.y ?? 0,
    button: init.button ?? 0,
    buttons: init.buttons ?? 0,
    shiftKey: init.shift ?? false,
    ctrlKey: init.ctrl ?? false,
  });
  Object.defineProperties(event, {
    pointerId: { value: init.id ?? 1 },
    pointerType: { value: init.type ?? "mouse" },
    isPrimary: { value: init.primary ?? true },
  });
  return event;
}

function wheel(init: WheelEventInit & { time?: number }): WheelEvent {
  const event = new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init });
  if (init.time !== undefined) Object.defineProperty(event, "timeStamp", { value: init.time });
  return event;
}

let container: HTMLDivElement;
let detach: () => void;
let options: { [K in keyof Interaction3DOptions]: ReturnType<typeof vi.fn> } & {
  handlers: () => Record<string, ReturnType<typeof vi.fn>>;
};
let handlers: Record<string, ReturnType<typeof vi.fn>>;
/** Knots by position: "a" at (10, 10), "b" at (50, 50). */
const pick = (point: { x: number; y: number }) =>
  Math.hypot(point.x - 10, point.y - 10) < 5
    ? "a"
    : Math.hypot(point.x - 50, point.y - 50) < 5
      ? "b"
      : null;

function attach(setting: "auto" | "zoom" | "pan" = "auto") {
  handlers = {
    onSelectNode: vi.fn(),
    onToggleNode: vi.fn(),
    onBoxSelect: vi.fn(),
    onContextMenu: vi.fn(),
    onHoverNode: vi.fn(),
  };
  options = {
    handlers: () => handlers,
    pick: vi.fn(pick),
    nodesInRect: vi.fn(() => ["a", "b"]),
    wheelSetting: vi.fn(() => setting),
    drawSelection: vi.fn(),
    beginDrag: vi.fn(() => ["a"]),
    dragTo: vi.fn(),
    endDrag: vi.fn(),
    zoomAt: vi.fn(),
    panBy: vi.fn(),
    flyTo: vi.fn(),
    onUserCamera: vi.fn(),
    setOverNode: vi.fn(),
  } as unknown as typeof options;
  detach = attachInteractions3d(container, options as unknown as Interaction3DOptions);
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  attach();
});

afterEach(() => {
  detach();
  container.remove();
  vi.useRealTimers();
});

describe("clicks", () => {
  it("selects a knot, toggles it with Ctrl, and clears the selection on empty space", () => {
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10 }));
    window.dispatchEvent(pointer("pointerup", { x: 10, y: 10 }));
    expect(handlers["onSelectNode"]).toHaveBeenLastCalledWith("a");

    container.dispatchEvent(pointer("pointerdown", { x: 50, y: 50, ctrl: true }));
    window.dispatchEvent(pointer("pointerup", { x: 50, y: 50 }));
    expect(handlers["onToggleNode"]).toHaveBeenCalledWith("b");

    container.dispatchEvent(pointer("pointerdown", { x: 30, y: 30 }));
    expect(options.onUserCamera).toHaveBeenCalled();
    window.dispatchEvent(pointer("pointerup", { x: 31, y: 30 }));
    expect(handlers["onSelectNode"]).toHaveBeenLastCalledWith(null);
  });

  it("keeps the selection after an orbit drag or a cancelled press", () => {
    container.dispatchEvent(pointer("pointerdown", { x: 30, y: 30 }));
    window.dispatchEvent(pointer("pointermove", { x: 60, y: 30 }));
    window.dispatchEvent(pointer("pointerup", { x: 60, y: 30 }));
    expect(handlers["onSelectNode"]).not.toHaveBeenCalled();

    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10 }));
    window.dispatchEvent(pointer("pointercancel", { x: 10, y: 10 }));
    container.dispatchEvent(pointer("pointerdown", { x: 30, y: 30 }));
    window.dispatchEvent(pointer("pointercancel", { x: 30, y: 30 }));
    expect(handlers["onSelectNode"]).not.toHaveBeenCalled();
  });

  it("ignores other mouse buttons and the release of another pointer", () => {
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10, button: 2 }));
    window.dispatchEvent(pointer("pointerup", { x: 10, y: 10 }));
    expect(handlers["onSelectNode"]).not.toHaveBeenCalled();

    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10, id: 1 }));
    window.dispatchEvent(pointer("pointermove", { x: 40, y: 40, id: 2 }));
    window.dispatchEvent(pointer("pointerup", { x: 10, y: 10, id: 2 }));
    expect(options.beginDrag).not.toHaveBeenCalled();
    expect(handlers["onSelectNode"]).not.toHaveBeenCalled();
    window.dispatchEvent(pointer("pointerup", { x: 10, y: 10, id: 1 }));
    expect(handlers["onSelectNode"]).toHaveBeenCalledWith("a");
  });

  it("flies to a double-clicked knot, and not to empty space", () => {
    container.dispatchEvent(pointer("dblclick", { x: 30, y: 30 }));
    expect(options.flyTo).not.toHaveBeenCalled();
    container.dispatchEvent(pointer("dblclick", { x: 50, y: 50 }));
    expect(options.flyTo).toHaveBeenCalledWith("b");
  });

  it("opens a knot's menu on right-click, never the browser's", () => {
    const outside = pointer("contextmenu", { x: 30, y: 30 });
    container.dispatchEvent(outside);
    expect(outside.defaultPrevented).toBe(true);
    expect(handlers["onContextMenu"]).not.toHaveBeenCalled();
    container.dispatchEvent(pointer("contextmenu", { x: 10, y: 10 }));
    expect(handlers["onContextMenu"]).toHaveBeenCalledWith("a", { x: 10, y: 10 });
  });
});

describe("dragging a knot", () => {
  it("starts past the threshold, follows the pointer and pins on release", () => {
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10 }));
    window.dispatchEvent(pointer("pointermove", { x: 12, y: 10 }));
    expect(options.beginDrag).not.toHaveBeenCalled();
    window.dispatchEvent(pointer("pointermove", { x: 30, y: 10 }));
    expect(options.beginDrag).toHaveBeenCalledWith("a", { x: 10, y: 10 });
    window.dispatchEvent(pointer("pointermove", { x: 40, y: 10 }));
    expect(options.dragTo).toHaveBeenLastCalledWith({ x: 40, y: 10 });
    expect(options.beginDrag).toHaveBeenCalledTimes(1);
    window.dispatchEvent(pointer("pointerup", { x: 40, y: 10 }));
    expect(options.endDrag).toHaveBeenCalled();
    expect(handlers["onSelectNode"]).not.toHaveBeenCalled();
  });
});

describe("box selection", () => {
  it("draws a rectangle with Shift and selects what is inside (Ctrl adds)", () => {
    container.dispatchEvent(pointer("pointerdown", { x: 0, y: 0, shift: true, ctrl: true }));
    window.dispatchEvent(pointer("pointermove", { x: 80, y: 80 }));
    expect(options.drawSelection).toHaveBeenLastCalledWith(
      expect.objectContaining({ width: 80, height: 80 }),
    );
    window.dispatchEvent(pointer("pointerup", { x: 80, y: 80 }));
    expect(options.drawSelection).toHaveBeenLastCalledWith(null);
    expect(handlers["onBoxSelect"]).toHaveBeenCalledWith(["a", "b"], true);
  });

  it("ignores a rectangle too small to mean anything", () => {
    container.dispatchEvent(pointer("pointerdown", { x: 0, y: 0, shift: true }));
    window.dispatchEvent(pointer("pointerup", { x: 1, y: 1 }));
    expect(handlers["onBoxSelect"]).not.toHaveBeenCalled();
  });
});

describe("hover", () => {
  it("reports the knot under a free pointer, once per change, and clears on leave", () => {
    window.dispatchEvent(pointer("pointermove", { x: 10, y: 10, target: container }));
    // Dispatched on window: the target is not inside the container.
    expect(handlers["onHoverNode"]).not.toHaveBeenCalled();
    container.dispatchEvent(pointer("pointermove", { x: 10, y: 10 }));
    container.dispatchEvent(pointer("pointermove", { x: 11, y: 10 }));
    expect(handlers["onHoverNode"]).toHaveBeenCalledTimes(1);
    expect(options.setOverNode).toHaveBeenLastCalledWith(true);
    // A button held: no hover changes.
    container.dispatchEvent(pointer("pointermove", { x: 30, y: 30, buttons: 1 }));
    expect(handlers["onHoverNode"]).toHaveBeenCalledTimes(1);
    container.dispatchEvent(new MouseEvent("pointerleave"));
    expect(handlers["onHoverNode"]).toHaveBeenLastCalledWith(null);
    expect(options.setOverNode).toHaveBeenLastCalledWith(false);
  });

  it("keeps the hover while a press is under way", () => {
    container.dispatchEvent(pointer("pointermove", { x: 10, y: 10 }));
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10 }));
    container.dispatchEvent(new MouseEvent("pointerleave"));
    expect(handlers["onHoverNode"]).toHaveBeenCalledTimes(1);
  });
});

describe("touch", () => {
  it("opens the menu after a long press that stays still", () => {
    vi.useFakeTimers();
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10, type: "touch" }));
    vi.advanceTimersByTime(LONG_PRESS_MS);
    expect(handlers["onContextMenu"]).toHaveBeenCalledWith("a", { x: 10, y: 10 });
    // The press is over: lifting the finger selects nothing.
    window.dispatchEvent(pointer("pointerup", { x: 10, y: 10, type: "touch" }));
    expect(handlers["onSelectNode"]).not.toHaveBeenCalled();
  });

  it("cancels the long press when the finger moves, or a second finger lands", () => {
    vi.useFakeTimers();
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10, type: "touch" }));
    window.dispatchEvent(pointer("pointermove", { x: 30, y: 10, type: "touch" }));
    vi.advanceTimersByTime(LONG_PRESS_MS);
    expect(handlers["onContextMenu"]).not.toHaveBeenCalled();
    window.dispatchEvent(pointer("pointerup", { x: 30, y: 10, type: "touch" }));

    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10, type: "touch" }));
    container.dispatchEvent(
      pointer("pointerdown", { x: 60, y: 60, type: "touch", id: 2, primary: false }),
    );
    vi.advanceTimersByTime(LONG_PRESS_MS);
    expect(handlers["onContextMenu"]).not.toHaveBeenCalled();
    // The first finger's press was dropped (a pinch, not a tap).
    window.dispatchEvent(pointer("pointerup", { x: 10, y: 10, type: "touch" }));
    expect(handlers["onSelectNode"]).not.toHaveBeenCalled();
  });

  it("lets a second finger join a drag that has started", () => {
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10, type: "touch" }));
    window.dispatchEvent(pointer("pointermove", { x: 40, y: 10, type: "touch" }));
    container.dispatchEvent(
      pointer("pointerdown", { x: 60, y: 60, type: "touch", id: 2, primary: false }),
    );
    window.dispatchEvent(pointer("pointerup", { x: 40, y: 10, type: "touch" }));
    expect(options.endDrag).toHaveBeenCalled();
  });
});

describe("wheel", () => {
  it("zooms toward the pointer one notch per burst with a mouse wheel", () => {
    container.dispatchEvent(wheel({ deltaY: 100, clientX: 5, clientY: 6, time: 1000 }));
    expect(options.zoomAt).toHaveBeenLastCalledWith({ x: 5, y: 6 }, ZOOM_STEP, true);
    // The same direction within the burst gap: one notch.
    container.dispatchEvent(wheel({ deltaY: 100, time: 1000 + NOTCH_GAP_MS / 2 }));
    expect(options.zoomAt).toHaveBeenCalledTimes(1);
    container.dispatchEvent(wheel({ deltaY: -100, time: 1000 + NOTCH_GAP_MS }));
    expect(options.zoomAt).toHaveBeenLastCalledWith({ x: 0, y: 0 }, 1 / ZOOM_STEP, true);
    container.dispatchEvent(wheel({ deltaY: 0, deltaX: 100, time: 2000 }));
    expect(options.zoomAt).toHaveBeenCalledTimes(2);
  });

  it("pans with the Pan setting, Shift turning vertical scrolling sideways", () => {
    detach();
    attach("pan");
    container.dispatchEvent(wheel({ deltaX: 3, deltaY: 4 }));
    expect(options.panBy).toHaveBeenLastCalledWith(3, 4);
    container.dispatchEvent(wheel({ deltaY: 7, shiftKey: true }));
    expect(options.panBy).toHaveBeenLastCalledWith(7, 0);
    const scrolled = wheel({ deltaY: 1 });
    container.dispatchEvent(scrolled);
    expect(scrolled.defaultPrevented).toBe(true);
  });

  it("zooms smoothly with a trackpad pinch (Ctrl + wheel)", () => {
    container.dispatchEvent(wheel({ deltaY: -10, ctrlKey: true, clientX: 2, clientY: 3 }));
    const [at, factor, animate] = options.zoomAt.mock.calls[0] ?? [];
    expect(at).toEqual({ x: 2, y: 3 });
    expect(factor).toBeLessThan(1);
    expect(animate).toBe(false);
  });

  it("zooms with Safari's gesture events", () => {
    const gesture = (type: string, scale: number) => {
      const event = new Event(type, { cancelable: true });
      Object.assign(event, { scale, clientX: 4, clientY: 4 });
      container.dispatchEvent(event);
      return event;
    };
    expect(gesture("gesturestart", 1).defaultPrevented).toBe(true);
    gesture("gesturechange", 2);
    expect(options.zoomAt).toHaveBeenLastCalledWith({ x: 4, y: 4 }, 0.5, false);
    gesture("gesturechange", 0);
    expect(options.zoomAt).toHaveBeenLastCalledWith({ x: 4, y: 4 }, 200, false);
    gesture("gestureend", 1);
    expect(options.onUserCamera).toHaveBeenCalledTimes(2);
  });
});

describe("detach", () => {
  it("removes every listener and a pending long press", () => {
    vi.useFakeTimers();
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10, type: "touch" }));
    detach();
    vi.advanceTimersByTime(LONG_PRESS_MS);
    container.dispatchEvent(pointer("pointerdown", { x: 10, y: 10 }));
    window.dispatchEvent(pointer("pointerup", { x: 10, y: 10 }));
    container.dispatchEvent(wheel({ deltaY: 100 }));
    expect(handlers["onContextMenu"]).not.toHaveBeenCalled();
    expect(handlers["onSelectNode"]).not.toHaveBeenCalled();
    expect(options.zoomAt).not.toHaveBeenCalled();
    // afterEach detaches again: harmless.
  });
});
