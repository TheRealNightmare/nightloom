/**
 * Pointer interactions on the sigma canvas, on top of sigma's own (pan by
 * dragging the stage, pinch and twist on touch screens, node drag):
 *
 * - Dragging a node moves it (with the rest of the selection when it is
 *   part of a multi-selection); the camera stays still. The frame of the
 *   picture is frozen from the first drag on (see {@link freezeFrame}), so
 *   a dropped node stays under the pointer and nothing else shifts. On
 *   release the moved nodes are reported, and become pinned.
 * - Ctrl/Cmd+click toggles a node in the multi-selection.
 * - Shift+drag on the stage draws a selection rectangle (screen-upright,
 *   so it works at any rotation; see ./geometry).
 * - Alt/Option+drag or right-button drag on the stage rotates the view
 *   around its center.
 * - The wheel pans or zooms toward the pointer (see ./wheel); Safari's
 *   trackpad gestures pinch-zoom and twist.
 * - Right-click on a node, or a long press on a touch screen, opens the
 *   node's menu.
 *
 * Everything here talks to sigma and the DOM; the decisions are in the pure
 * modules it imports.
 */
import type Sigma from "sigma";

import {
  dragRotation,
  isRectTooSmall,
  normalizeAngle,
  nodesInRect,
  rectFromPoints,
  rotateBySteps,
  type Point,
  type Rect,
} from "./geometry";
import { fit2d, FIT_SHARE, freeAreas, MIN_FIT_EXTENT, type ScreenKnot } from "./framing";
import { pinchFactor, pixelDeltas, wheelAction, WheelClassifier, type WheelSetting } from "./wheel";

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- any sigma instance
export type AnySigma = Sigma<any, any, any, any, any, any, any>;

export interface InteractionHandlers {
  onSelectNode: (node: string | null) => void;
  onToggleNode: (node: string) => void;
  onBoxSelect: (nodes: string[], additive: boolean) => void;
  /** Nodes the user dragged to a new place (to pin them). */
  onNodesMoved: (nodes: string[]) => void;
  /** Open the node's menu at `point` (pixels in the canvas). */
  onContextMenu: (node: string, point: Point) => void;
}

export interface InteractionOptions {
  handlers: () => InteractionHandlers;
  /** The nodes a selection rectangle may pick (the visible ones). */
  visibleNodes: () => Iterable<string>;
  wheelSetting: () => WheelSetting;
  /** Shows the selection rectangle (null hides it). */
  drawSelection: (rect: Rect | null) => void;
  /** Sets `fixed` on dragged nodes so a running layout leaves them be. */
  holdNodes: (nodes: readonly string[]) => void;
}

export const ZOOM_STEP = 1.5;
export const ZOOM_DURATION_MS = 200;
export const ROTATE_DURATION_MS = 200;
export const LONG_PRESS_MS = 550;
/** A knot's radius on screen when sigma has not drawn it yet (pixels). */
const DEFAULT_KNOT_RADIUS = 8;
/** A touch that moves further than this is not a long press. */
export const LONG_PRESS_TOLERANCE_PX = 10;

// --- camera helpers (also used by the keyboard and the buttons) -----------------------

function viewportCenter(sigma: AnySigma): Point {
  const { width, height } = sigma.getDimensions();
  return { x: width / 2, y: height / 2 };
}

/** Moves the camera by `dx`, `dy` screen pixels (in screen directions, at any rotation). */
export function panBy(sigma: AnySigma, dx: number, dy: number, animate = false): void {
  const center = viewportCenter(sigma);
  const target = sigma.viewportToFramedGraph({ x: center.x + dx, y: center.y + dy });
  const camera = sigma.getCamera();
  if (animate) void camera.animate({ x: target.x, y: target.y }, { duration: ZOOM_DURATION_MS });
  else camera.setState({ x: target.x, y: target.y });
}

/** Zooms by `factor` (< 1 zooms in) keeping the graph point under `at` (screen pixels) in place. */
export function zoomAt(sigma: AnySigma, at: Point, factor: number, animate: boolean): void {
  const camera = sigma.getCamera();
  const ratio = camera.getBoundedRatio(camera.getState().ratio * factor);
  if (ratio === camera.getState().ratio) return;
  const state = sigma.getViewportZoomedState(at, ratio);
  if (animate) void camera.animate(state, { duration: ZOOM_DURATION_MS, easing: "quadraticOut" });
  else camera.setState(state);
}

/** Zooms one step toward (direction 1) or away from the center of the view. */
export function zoomStep(sigma: AnySigma, direction: 1 | -1): void {
  zoomAt(sigma, viewportCenter(sigma), direction > 0 ? 1 / ZOOM_STEP : ZOOM_STEP, true);
}

/** Rotates the view by `steps` × 15° around its center. */
export function rotateView(sigma: AnySigma, steps: number): void {
  const camera = sigma.getCamera();
  void camera.animate(
    { angle: rotateBySteps(camera.getState().angle, steps) },
    { duration: ROTATE_DURATION_MS },
  );
}

/**
 * Freezes the mapping from graph to screen at the graph's current extent.
 * With sigma's default auto-rescale, moving a node past the graph's edge
 * would re-fit the whole picture (on every frame of a drag, and again after
 * it), so the node would jump away from the pointer and everything else
 * would shift. Frozen, the picture only changes when the user moves it.
 * {@link releaseFrame} (Reset view) fits the graph again.
 */
export function freezeFrame(sigma: AnySigma): void {
  if (!sigma.getCustomBBox()) sigma.setCustomBBox(sigma.getBBox());
}

/** Lets the picture fit the whole graph again (Reset view). */
export function releaseFrame(sigma: AnySigma): void {
  if (sigma.getCustomBBox()) sigma.setCustomBBox(null);
}

/** The camera of sigma's reset view (the whole graph, as sigma frames it). */
const RESET_STATE = { x: 0.5, y: 0.5, ratio: 1, angle: 0 };

/**
 * The camera state that frames the visible `nodes` (see ./framing): their
 * bounds, knots included, fill {@link FIT_SHARE} of the largest part of the
 * viewport that the `covered` rectangles (pixels: legend, controls,
 * minimap) leave free, centered there, unrotated. A graph narrower than
 * {@link MIN_FIT_EXTENT} layout units is framed as if it were that wide.
 * Null when no node is visible. The graph's frame must be current (after
 * {@link releaseFrame}, refresh sigma first).
 */
export function fittedCameraState(
  sigma: AnySigma,
  nodes: Iterable<string>,
  covered: readonly Rect[] = [],
): { x: number; y: number; ratio: number; angle: number } | null {
  const { width, height } = sigma.getDimensions();
  if (!(width > 0) || !(height > 0)) return null;
  const graph = sigma.getGraph();
  const at = { cameraState: RESET_STATE };
  const knots: ScreenKnot[] = [];
  for (const node of nodes) {
    if (!graph.hasNode(node)) continue;
    const { x, y } = graph.getNodeAttributes(node) as { x: number; y: number };
    const point = sigma.graphToViewport({ x, y }, at);
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
    const size = (sigma.getNodeDisplayData(node) as { size?: number } | undefined)?.size;
    knots.push({ x: point.x, y: point.y, radius: size ?? DEFAULT_KNOT_RADIUS });
  }
  // Pixels the minimum extent spans in the reset view. sigma frames a graph
  // without extent (one entity, or all on one spot) as if it were one unit
  // wide, so there the frame stands for the minimum extent.
  const bbox = sigma.getBBox();
  const flat = bbox.x[0] === bbox.x[1] && bbox.y[0] === bbox.y[1];
  const origin = sigma.graphToViewport({ x: 0, y: 0 }, at);
  const far = sigma.graphToViewport({ x: flat ? 1 : MIN_FIT_EXTENT, y: 0 }, at);
  const minExtentPx = Math.hypot(far.x - origin.x, far.y - origin.y);
  const camera = sigma.getCamera();
  // Nor closer, or farther, than the camera may go (its ratio bounds).
  const maxZoom = Math.min(
    minExtentPx > 0 ? (FIT_SHARE * Math.min(width, height)) / minExtentPx : Infinity,
    1 / camera.getBoundedRatio(0),
  );
  const minZoom = 1 / camera.getBoundedRatio(Infinity);
  const fit = fit2d(knots, width, height, covered, maxZoom, FIT_SHARE, minZoom);
  if (!fit) return null;
  const center = sigma.viewportToFramedGraph(fit.anchor, at);
  return { x: center.x, y: center.y, ratio: camera.getBoundedRatio(1 / fit.zoom), angle: 0 };
}

/**
 * The camera that shows `point` (framed graph coordinates, as sigma's node
 * display data) at `ratio` in the middle of the largest area the `covered`
 * overlays leave free, rather than under the legend. The current rotation is kept.
 */
export function focusCameraState(
  sigma: AnySigma,
  point: { x: number; y: number },
  ratio: number,
  covered: readonly Rect[] = [],
): { x: number; y: number; ratio: number } {
  const { width, height } = sigma.getDimensions();
  const centered = { x: point.x, y: point.y, ratio };
  if (!(width > 0) || !(height > 0)) return centered;
  const area = freeAreas(width, height, covered).reduce((best, free) =>
    free.width * free.height > best.width * best.height ? free : best,
  );
  // The point sits at the canvas's center with the camera on it; the camera
  // that moves it to the area's center is as far the other way.
  const at = { cameraState: { ...centered, angle: sigma.getCamera().getState().angle } };
  const target = sigma.viewportToFramedGraph(
    { x: width - (area.x + area.width / 2), y: height - (area.y + area.height / 2) },
    at,
  );
  return { x: target.x, y: target.y, ratio };
}

/** The camera in graph terms: the point at the center, graph units per pixel, the rotation. */
export interface GraphCamera {
  x: number;
  y: number;
  ratio: number;
  angle: number;
}

/** Graph units per screen pixel with the camera at `ratio` 1 (or as it is now). */
function unitsPerPixel(sigma: AnySigma, cameraState?: { ratio: number }): number {
  const center = viewportCenter(sigma);
  const override = cameraState
    ? { cameraState: { ...sigma.getCamera().getState(), ...cameraState } }
    : undefined;
  const a = sigma.viewportToGraph(center, override);
  const b = sigma.viewportToGraph({ x: center.x + 1, y: center.y }, override);
  return Math.hypot(b.x - a.x, b.y - a.y);
}

/**
 * Where the camera looks, independent of sigma's framing (which follows the
 * graph's extent): a saved view means the same on another screen or layout.
 */
export function graphCamera(sigma: AnySigma): GraphCamera {
  const center = sigma.viewportToGraph(viewportCenter(sigma));
  return {
    x: center.x,
    y: center.y,
    ratio: unitsPerPixel(sigma),
    angle: normalizeAngle(sigma.getCamera().getState().angle),
  };
}

/**
 * Moves the camera to `target` (see {@link graphCamera}), over `durationMs`
 * (0: at once). The frame is frozen first, so the picture stays put while a
 * layout moves nodes that are not pinned.
 */
export function moveCamera(sigma: AnySigma, target: GraphCamera, durationMs: number): void {
  sigma.refresh();
  freezeFrame(sigma);
  const camera = sigma.getCamera();
  const framed = sigma.graphToFramedGraph({ x: target.x, y: target.y });
  const atOne = unitsPerPixel(sigma, { ratio: 1 });
  const ratio = camera.getBoundedRatio(atOne > 0 ? target.ratio / atOne : 1);
  const state = { x: framed.x, y: framed.y, ratio, angle: target.angle };
  if (durationMs > 0)
    void camera.animate(state, { duration: durationMs, easing: "quadraticInOut" });
  else camera.setState(state);
}

/** A node's position on screen (null when it is not in the graph). */
export function nodeOnScreen(sigma: AnySigma, node: string): Point | null {
  const graph = sigma.getGraph();
  if (!graph.hasNode(node)) return null;
  const { x, y } = graph.getNodeAttributes(node) as { x: number; y: number };
  return sigma.graphToViewport({ x, y });
}

// --- pointer wiring -----------------------------------------------------------------------

type Mode =
  | { kind: "idle" }
  | { kind: "box"; start: Point; additive: boolean }
  | { kind: "rotate"; start: Point; startAngle: number };

function isMouse(event: { original: MouseEvent | TouchEvent }): event is { original: MouseEvent } {
  return typeof MouseEvent !== "undefined" && event.original instanceof MouseEvent;
}

function positionIn(container: HTMLElement, event: MouseEvent | Touch): Point {
  const box = container.getBoundingClientRect();
  return { x: event.clientX - box.left, y: event.clientY - box.top };
}

/** Wires the interactions to `sigma` and its `container`; returns the function that undoes it. */
export function attachInteractions(
  sigma: AnySigma,
  container: HTMLElement,
  options: InteractionOptions,
): () => void {
  const camera = sigma.getCamera();
  let mode: Mode = { kind: "idle" };
  // A stage click right after a rectangle or a rotation is not a "clear selection".
  let suppressStageClick = false;
  let longPress: { timer: ReturnType<typeof setTimeout>; start: Point } | null = null;
  // Where the pointer went down on a node (sigma starts a drag on the first move).
  let pressed: { node: string; at: Point } | null = null;
  const cleanups: (() => void)[] = [];

  const cancelLongPress = () => {
    if (longPress) clearTimeout(longPress.timer);
    longPress = null;
  };

  // --- nodes: click, drag, menu -------------------------------------------------------
  sigma.on("clickNode", ({ node, event }) => {
    const original = event.original as MouseEvent;
    if (isMouse(event) && (original.ctrlKey || original.metaKey)) {
      options.handlers().onToggleNode(node);
      return;
    }
    options.handlers().onSelectNode(node);
  });
  sigma.on("clickStage", () => {
    if (suppressStageClick) {
      suppressStageClick = false;
      return;
    }
    options.handlers().onSelectNode(null);
  });
  sigma.on("rightClickNode", ({ node, event }) => {
    options.handlers().onContextMenu(node, { x: event.x, y: event.y });
  });

  sigma.on("nodeDragStart", ({ node, allDraggedNodes, event }) => {
    cancelLongPress();
    freezeFrame(sigma);
    options.holdNodes(allDraggedNodes);
    // sigma measures the drag from this first move, so the pointer's travel
    // since it went down would be lost and the node would trail the pointer.
    // Make that up now: sigma reads the start positions after this event.
    if (pressed?.node === node) {
      const from = sigma.viewportToGraph(pressed.at);
      const to = sigma.viewportToGraph({ x: event.x, y: event.y });
      const graph = sigma.getGraph();
      for (const dragged of allDraggedNodes) {
        if (!graph.hasNode(dragged)) continue;
        const { x, y } = graph.getNodeAttributes(dragged) as { x: number; y: number };
        graph.mergeNodeAttributes(dragged, { x: x + to.x - from.x, y: y + to.y - from.y });
      }
    }
    pressed = null;
  });
  sigma.on("nodeDragEnd", ({ allDraggedNodes }) => {
    options.handlers().onNodesMoved(allDraggedNodes);
  });

  // Long press (touch): the node's menu.
  sigma.on("downNode", ({ node, event }) => {
    pressed = { node, at: { x: event.x, y: event.y } };
    if (isMouse(event)) return;
    cancelLongPress();
    const start = { x: event.x, y: event.y };
    longPress = {
      start,
      timer: setTimeout(() => {
        longPress = null;
        options.handlers().onContextMenu(node, start);
      }, LONG_PRESS_MS),
    };
  });
  const endTouch = () => {
    cancelLongPress();
  };
  sigma.on("upNode", endTouch);
  sigma.on("upStage", endTouch);

  // --- stage: rectangle and rotation ---------------------------------------------------
  const finish = (event: MouseEvent) => {
    window.removeEventListener("mouseup", finish, true);
    const current = mode;
    mode = { kind: "idle" };
    if (current.kind === "box") {
      const rect = rectFromPoints(current.start, positionIn(container, event));
      options.drawSelection(null);
      if (isRectTooSmall(rect)) return;
      suppressStageClick = true;
      const nodes = nodesInRect(options.visibleNodes(), (node) => nodeOnScreen(sigma, node), rect);
      options.handlers().onBoxSelect(nodes, current.additive);
    } else if (current.kind === "rotate") {
      suppressStageClick = true;
    }
    // The click (if any) follows this mouseup at once; forget the flag after it.
    setTimeout(() => {
      suppressStageClick = false;
    }, 0);
  };

  sigma.on("downStage", ({ event }) => {
    if (!isMouse(event)) return;
    const original = event.original;
    const start = { x: event.x, y: event.y };
    if (original.button === 0 && original.shiftKey) {
      mode = { kind: "box", start, additive: original.ctrlKey || original.metaKey };
    } else if ((original.button === 0 && original.altKey) || original.button === 2) {
      mode = { kind: "rotate", start, startAngle: camera.getState().angle };
    } else {
      return;
    }
    window.addEventListener("mouseup", finish, true);
  });

  sigma.on("moveBody", (payload) => {
    const { event } = payload;
    if (
      longPress &&
      Math.hypot(event.x - longPress.start.x, event.y - longPress.start.y) > LONG_PRESS_TOLERANCE_PX
    ) {
      cancelLongPress();
    }
    if (mode.kind === "idle") return;
    // Not a pan: the camera stays where it is.
    payload.preventSigmaDefault();
    const point = { x: event.x, y: event.y };
    if (mode.kind === "box") {
      options.drawSelection(rectFromPoints(mode.start, point));
    } else {
      const { width, height } = sigma.getDimensions();
      camera.setState({
        angle: dragRotation({ x: width / 2, y: height / 2 }, mode.start, point, mode.startAngle),
      });
    }
  });

  // The browser's menu never opens over the graph (right-drag rotates).
  const onContextMenu = (event: MouseEvent) => {
    event.preventDefault();
  };
  container.addEventListener("contextmenu", onContextMenu);
  cleanups.push(() => {
    container.removeEventListener("contextmenu", onContextMenu);
  });

  // --- wheel and gestures ------------------------------------------------------------------
  const classifier = new WheelClassifier();
  let lastZoom = { direction: 0, time: 0 };
  const onWheel = (event: WheelEvent) => {
    // Ours entirely: sigma's own wheel zoom never runs, and the page never scrolls.
    event.preventDefault();
    event.stopImmediatePropagation();
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
      // Shift+wheel scrolls sideways.
      if (event.shiftKey && dx === 0) [dx, dy] = [dy, 0];
      panBy(sigma, dx, dy);
    } else if (action === "pinch") {
      zoomAt(sigma, at, pinchFactor(pixelDeltas(event).dy), false);
    } else {
      const { dy } = pixelDeltas(event);
      if (dy === 0) return;
      const direction = dy > 0 ? 1 : -1;
      // One step per notch: skip events of one notch arriving in a burst.
      if (
        direction === lastZoom.direction &&
        event.timeStamp - lastZoom.time < ZOOM_DURATION_MS / 5
      ) {
        return;
      }
      lastZoom = { direction, time: event.timeStamp };
      zoomAt(sigma, at, direction > 0 ? ZOOM_STEP : 1 / ZOOM_STEP, true);
    }
  };
  container.addEventListener("wheel", onWheel, { capture: true, passive: false });
  cleanups.push(() => {
    container.removeEventListener("wheel", onWheel, { capture: true });
  });

  // Safari reports trackpad pinch and twist as gesture events.
  let gesture: { ratio: number; angle: number } | null = null;
  const onGestureStart = (event: Event) => {
    event.preventDefault();
    const state = camera.getState();
    gesture = { ratio: state.ratio, angle: state.angle };
  };
  const onGestureChange = (event: Event) => {
    event.preventDefault();
    if (!gesture) return;
    const { scale, rotation, clientX, clientY } = event as Event & {
      scale: number;
      rotation: number;
      clientX: number;
      clientY: number;
    };
    const at = positionIn(container, { clientX, clientY } as MouseEvent);
    const target = camera.getBoundedRatio(gesture.ratio / Math.max(scale, 0.01));
    camera.setState({
      ...sigma.getViewportZoomedState(at, target),
      angle: gesture.angle + (rotation * Math.PI) / 180,
    });
  };
  const onGestureEnd = (event: Event) => {
    event.preventDefault();
    gesture = null;
  };
  container.addEventListener("gesturestart", onGestureStart);
  container.addEventListener("gesturechange", onGestureChange);
  container.addEventListener("gestureend", onGestureEnd);
  cleanups.push(() => {
    container.removeEventListener("gesturestart", onGestureStart);
    container.removeEventListener("gesturechange", onGestureChange);
    container.removeEventListener("gestureend", onGestureEnd);
  });

  return () => {
    cancelLongPress();
    window.removeEventListener("mouseup", finish, true);
    for (const cleanup of cleanups) cleanup();
  };
}
