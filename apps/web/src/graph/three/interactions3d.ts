/**
 * Pointer interactions of the 3D view. three's OrbitControls (three/addons)
 * does what it is good at: left-drag (one finger) orbits around the target,
 * right-drag (two fingers) pans, pinch zooms. Everything that starts on a
 * knot, or with Shift, is handled here first and never reaches it:
 *
 * - Dragging a knot moves it on the plane facing the camera (with the rest
 *   of the selection when it is part of it); on release it is pinned.
 * - Click selects, Ctrl/Cmd+click toggles, double-click flies to the knot,
 *   a click on empty space (without a drag) clears the selection.
 * - Shift+drag draws a selection rectangle (screen space).
 * - The wheel zooms toward the pointer or pans, as in 2D (../wheel and the
 *   "Scroll wheel" setting); a trackpad pinch zooms.
 * - Right-click on a knot, or a long press on a touch screen, opens its menu.
 *
 * The decisions are the pure functions of ./picking, ./camera3d and ../wheel.
 */
import { isRectTooSmall, rectFromPoints, type Point, type Rect } from "../geometry";
import { LONG_PRESS_MS, LONG_PRESS_TOLERANCE_PX, ZOOM_STEP } from "../interactions";
import {
  pinchFactor,
  pixelDeltas,
  wheelAction,
  WheelClassifier,
  type WheelSetting,
} from "../wheel";

/** A press that moves less than this (pixels) is a click. */
export const DRAG_THRESHOLD_PX = 4;
/** Wheel notches closer than this (ms) in one direction are one notch (a burst). */
export const NOTCH_GAP_MS = 40;

export interface Interaction3DHandlers {
  onSelectNode: (node: string | null) => void;
  onToggleNode: (node: string) => void;
  onBoxSelect: (nodes: string[], additive: boolean) => void;
  onContextMenu: (node: string, point: Point) => void;
  onHoverNode: (node: string | null) => void;
}

export interface Interaction3DOptions {
  handlers: () => Interaction3DHandlers;
  /** The knot drawn at a point of the container (pixels), if any. */
  pick: (point: Point) => string | null;
  /** The visible knots inside a rectangle of the container. */
  nodesInRect: (rect: Rect) => string[];
  wheelSetting: () => WheelSetting;
  drawSelection: (rect: Rect | null) => void;
  /** Starts moving `node` (and the selection with it); returns the nodes moved. */
  beginDrag: (node: string, at: Point) => string[];
  dragTo: (at: Point) => void;
  /** The drag is over: pin what moved. */
  endDrag: () => void;
  /** Zoom by `factor` (< 1: in) toward `at`, animated or not. */
  zoomAt: (at: Point, factor: number, animate: boolean) => void;
  panBy: (dx: number, dy: number) => void;
  flyTo: (node: string) => void;
  /** The user started moving the camera (stops automatic framing and animations). */
  onUserCamera: () => void;
  /** Whether the pointer shows the "over a knot" cursor. */
  setOverNode: (over: boolean) => void;
}

type Press =
  | {
      kind: "node";
      node: string;
      start: Point;
      pointerId: number;
      toggle: boolean;
      dragging: boolean;
    }
  | { kind: "box"; start: Point; pointerId: number; additive: boolean }
  | { kind: "stage"; start: Point; pointerId: number; moved: boolean };

function positionIn(container: HTMLElement, event: { clientX: number; clientY: number }): Point {
  const box = container.getBoundingClientRect();
  return { x: event.clientX - box.left, y: event.clientY - box.top };
}

/** Wires the interactions to `container` (OrbitControls' element); returns the function that undoes it. */
export function attachInteractions3d(
  container: HTMLElement,
  options: Interaction3DOptions,
): () => void {
  let press: Press | null = null;
  let hovered: string | null = null;
  let longPress: { timer: ReturnType<typeof setTimeout>; start: Point } | null = null;
  const cleanups: (() => void)[] = [];
  const listen = <K extends keyof HTMLElementEventMap>(
    target: HTMLElement | Window,
    type: K,
    listener: (event: HTMLElementEventMap[K]) => void,
    capture = true,
    passive?: boolean,
  ) => {
    const options_ = passive === undefined ? { capture } : { capture, passive };
    target.addEventListener(type, listener as EventListener, options_);
    cleanups.push(() => {
      target.removeEventListener(type, listener as EventListener, { capture });
    });
  };

  const cancelLongPress = () => {
    if (longPress) clearTimeout(longPress.timer);
    longPress = null;
  };

  const setHover = (node: string | null) => {
    if (node === hovered) return;
    hovered = node;
    options.setOverNode(node !== null);
    options.handlers().onHoverNode(node);
  };

  // --- press ------------------------------------------------------------------------
  listen(container, "pointerdown", (event: PointerEvent) => {
    if (!event.isPrimary) {
      // A second finger: the gesture is a pan / pinch for OrbitControls.
      cancelLongPress();
      if (press?.kind === "node" && !press.dragging) press = null;
      return;
    }
    const at = positionIn(container, event);
    const mouse = event.pointerType === "mouse";
    if (mouse && event.button !== 0) return;
    if (mouse && event.shiftKey) {
      press = {
        kind: "box",
        start: at,
        pointerId: event.pointerId,
        additive: event.ctrlKey || event.metaKey,
      };
      event.stopImmediatePropagation();
      event.preventDefault();
      return;
    }
    const node = options.pick(at);
    if (node === null) {
      // Orbit (OrbitControls); a press without a drag clears the selection.
      press = { kind: "stage", start: at, pointerId: event.pointerId, moved: false };
      options.onUserCamera();
      return;
    }
    press = {
      kind: "node",
      node,
      start: at,
      pointerId: event.pointerId,
      toggle: event.ctrlKey || event.metaKey,
      dragging: false,
    };
    event.stopImmediatePropagation();
    event.preventDefault();
    if (!mouse) {
      cancelLongPress();
      longPress = {
        start: at,
        timer: setTimeout(() => {
          longPress = null;
          press = null;
          options.handlers().onContextMenu(node, at);
        }, LONG_PRESS_MS),
      };
    }
  });

  listen(window, "pointermove", (event: PointerEvent) => {
    const at = positionIn(container, event);
    if (
      longPress &&
      Math.hypot(at.x - longPress.start.x, at.y - longPress.start.y) > LONG_PRESS_TOLERANCE_PX
    ) {
      cancelLongPress();
    }
    if (press === null) {
      if (event.target instanceof Node && container.contains(event.target) && event.buttons === 0) {
        setHover(options.pick(at));
      }
      return;
    }
    if (event.pointerId !== press.pointerId) return;
    const distance = Math.hypot(at.x - press.start.x, at.y - press.start.y);
    if (press.kind === "box") {
      options.drawSelection(rectFromPoints(press.start, at));
    } else if (press.kind === "stage") {
      if (distance > DRAG_THRESHOLD_PX) press.moved = true;
    } else {
      if (!press.dragging && distance > DRAG_THRESHOLD_PX) {
        press.dragging = true;
        cancelLongPress();
        options.beginDrag(press.node, press.start);
      }
      if (press.dragging) options.dragTo(at);
    }
  });

  const finish = (event: PointerEvent) => {
    if (press?.pointerId !== event.pointerId) return;
    const current = press;
    press = null;
    cancelLongPress();
    const at = positionIn(container, event);
    if (current.kind === "box") {
      options.drawSelection(null);
      const rect = rectFromPoints(current.start, at);
      if (isRectTooSmall(rect)) return;
      options.handlers().onBoxSelect(options.nodesInRect(rect), current.additive);
    } else if (current.kind === "stage") {
      if (!current.moved && event.type === "pointerup") options.handlers().onSelectNode(null);
    } else if (current.dragging) {
      options.endDrag();
    } else if (event.type === "pointerup") {
      if (current.toggle) options.handlers().onToggleNode(current.node);
      else options.handlers().onSelectNode(current.node);
    }
  };
  listen(window, "pointerup", finish);
  listen(window, "pointercancel", finish);

  listen(
    container,
    "pointerleave",
    () => {
      if (press === null) setHover(null);
    },
    false,
  );

  listen(container, "dblclick", (event: MouseEvent) => {
    const node = options.pick(positionIn(container, event));
    if (node !== null) options.flyTo(node);
  });

  // The browser's menu never opens over the graph; a knot's menu does.
  listen(container, "contextmenu", (event: MouseEvent) => {
    event.preventDefault();
    const at = positionIn(container, event);
    const node = options.pick(at);
    if (node !== null) options.handlers().onContextMenu(node, at);
  });

  // --- wheel and gestures ---------------------------------------------------------------
  const classifier = new WheelClassifier();
  let lastZoom = { direction: 0, time: 0 };
  listen(
    container,
    "wheel",
    (event: WheelEvent) => {
      // Ours entirely: OrbitControls' wheel zoom never runs, the page never scrolls.
      event.preventDefault();
      event.stopImmediatePropagation();
      options.onUserCamera();
      const kind = classifier.classify({
        deltaX: event.deltaX,
        deltaY: event.deltaY,
        deltaMode: event.deltaMode,
        ctrlKey: event.ctrlKey,
        time: event.timeStamp,
      });
      const action = wheelAction(kind, options.wheelSetting());
      const at = positionIn(container, event);
      if (action === "pan") {
        let { dx, dy } = pixelDeltas(event);
        if (event.shiftKey && dx === 0) [dx, dy] = [dy, 0];
        options.panBy(dx, dy);
        return;
      }
      if (action === "pinch") {
        options.zoomAt(at, pinchFactor(pixelDeltas(event).dy), false);
        return;
      }
      const { dy } = pixelDeltas(event);
      if (dy === 0) return;
      const direction = dy > 0 ? 1 : -1;
      if (direction === lastZoom.direction && event.timeStamp - lastZoom.time < NOTCH_GAP_MS)
        return;
      lastZoom = { direction, time: event.timeStamp };
      options.zoomAt(at, direction > 0 ? ZOOM_STEP : 1 / ZOOM_STEP, true);
    },
    true,
    false,
  );

  // Safari reports trackpad pinches as gesture events.
  let gestureScale = 1;
  const onGestureStart = (event: Event) => {
    event.preventDefault();
    gestureScale = 1;
    options.onUserCamera();
  };
  const onGestureChange = (event: Event) => {
    event.preventDefault();
    const { scale, clientX, clientY } = event as Event & {
      scale: number;
      clientX: number;
      clientY: number;
    };
    const next = Math.max(scale, 0.01);
    options.zoomAt(positionIn(container, { clientX, clientY }), gestureScale / next, false);
    gestureScale = next;
  };
  container.addEventListener("gesturestart", onGestureStart);
  container.addEventListener("gesturechange", onGestureChange);
  container.addEventListener("gestureend", onGestureStart);
  cleanups.push(() => {
    container.removeEventListener("gesturestart", onGestureStart);
    container.removeEventListener("gesturechange", onGestureChange);
    container.removeEventListener("gestureend", onGestureStart);
  });

  return () => {
    cancelLongPress();
    for (const cleanup of cleanups) cleanup();
  };
}
