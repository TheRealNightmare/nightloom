import {
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type Ref,
} from "react";
import type { Attributes } from "graphology-types";
import Sigma from "sigma";
import type { CameraState } from "sigma/types";

import type { EdgeAttributes, NightloomGraph, NodeAttributes } from "./buildGraph";
import { DEFAULT_GRAPH_PALETTE, type GraphPalette } from "../theme/graphPalette";
import type { Theme } from "../theme/tokens";
import { ENTITY_TYPE_INFO } from "../domain/entities";
import {
  CURVATURE,
  LABEL_FONT,
  LABEL_FONT_STRONG,
  LABEL_SIZE,
  PARALLEL_SPREAD,
  threadWidthStep,
} from "./encoding";
import { announceNode, focusOrder, nextFocus } from "./focusOrder";
import type { Point, Rect } from "./geometry";
import {
  attachInteractions,
  freezeFrame,
  graphCamera,
  moveCamera,
  nodeOnScreen,
  releaseFrame,
  panBy,
  rotateView,
  zoomStep,
  type GraphCamera,
  type InteractionHandlers,
  fittedCameraState,
} from "./interactions";
import { keyAction } from "./keyboard";
import { waitForLabelFont } from "./labelFont";
import { measureText, placeLabels, type KnotMark, type LabelCandidate } from "./labelLayout";
import { Minimap } from "./Minimap";
import {
  graphPrimitives,
  PIN_BOX,
  TAG_FONT_SIZE,
  TAG_HEIGHT,
  TAG_PADDING_X,
  type GraphPrimitives,
} from "./primitives";
import {
  computeHighlight,
  edgeView,
  focusSummary,
  isNodeVisible,
  NO_PULSE,
  nodeView,
  type Highlight,
  type NodeView,
  type RenderContext,
  type ViewState,
} from "./view";
import type { WheelSetting } from "./wheel";

export interface GraphCanvasHandle {
  /** Animates the camera to a node. */
  focusNode: (node: string) => void;
  /** Animates the camera back to the whole graph, unrotated. */
  resetCamera: () => void;
  /** Animates the camera one step closer. */
  zoomIn: () => void;
  /** Animates the camera one step further away. */
  zoomOut: () => void;
  /** Rotates the view by `steps` × 15° (positive: clockwise). */
  rotate: (steps: number) => void;
  /** Where the camera looks, in graph terms (null until the graph is drawn). */
  getCamera: () => GraphCamera | null;
  /** Moves the camera there over `durationMs` (0: at once). */
  setCamera: (camera: GraphCamera, durationMs: number) => void;
}

export interface GraphCanvasProps {
  graph: NightloomGraph;
  view: ViewState;
  onHoverNode: (node: string | null) => void;
  onSelectNode: (node: string | null) => void;
  /** Ctrl/Cmd+click: add the node to the multi-selection, or take it out. */
  onToggleNode?: (node: string) => void;
  /** A selection rectangle was drawn around `nodes` (`additive`: with Ctrl/Cmd held). */
  onBoxSelect?: (nodes: string[], additive: boolean) => void;
  /** The user moved these nodes (dragged, or Shift+arrows): pin them where they are. */
  onNodesMoved?: (nodes: string[]) => void;
  /** Pin or unpin a node (the P key). */
  onTogglePin?: (node: string) => void;
  /** Open a node's menu at `point` (pixels in the canvas). */
  onContextMenu?: (node: string, point: Point) => void;
  /** Escape with no node focused. */
  onClearSelection?: () => void;
  /** The ? key. */
  onShowShortcuts?: () => void;
  /** The V key (saved views). */
  onOpenViews?: () => void;
  /** The node with the keyboard focus ring changed (kept across a switch of mode). */
  onFocusChange?: (node: string | null) => void;
  /** Given the focus ring when the canvas first takes the keyboard focus. */
  initialFocus?: string | null;
  /** Whether the graph is drawn (sigma in place); called when that changes. */
  onReadyChange?: (ready: boolean) => void;
  /** What the mouse wheel does (see ./wheel). */
  wheelSetting?: WheelSetting;
  showMinimap?: boolean;
  /** Label every node, not only the larger ones (for small graphs). */
  showAllLabels?: boolean;
  /** Changes whenever the graph's nodes or edges do, so highlights are recomputed. */
  revision?: unknown;
  /** Changes whenever pins do (for what is announced about the focused node). */
  pinRevision?: unknown;
  /** Nodes to emphasize for a moment (e.g. a job's results). */
  pulse?: ReadonlySet<string>;
  /**
   * Colors of the current theme (see ./encoding). Everything sigma draws is
   * computed from it on each refresh, so a theme change repaints in place.
   * The canvas itself is transparent: the page's woven texture shows through.
   */
  palette?: GraphPalette;
  theme?: Theme;
  /**
   * What the overlays (legend, controls, minimap) cover of the canvas
   * (pixels): the initial view and Reset view frame the graph in the
   * largest part they leave free (see ./framing).
   */
  frameCover?: readonly Rect[];
  ref?: Ref<GraphCanvasHandle>;
}

// eslint-disable-next-line @typescript-eslint/no-empty-object-type -- sigma's "no custom state"
type CanvasSigma = Sigma<NodeAttributes, EdgeAttributes, Attributes, {}, {}, {}, GraphPrimitives>;

const CAMERA_DURATION_MS = 450;
/** Room sigma keeps around the whole graph in its own reset view (pixels). */
const STAGE_PADDING = 64;
const FOCUS_RETRY_FRAMES = 10;
const FOCUS_RATIO = 0.7;
/** Only knots drawn at least this many pixels wide get a label (all of them in small graphs). */
const LABEL_SIZE_THRESHOLD = 8;
/** A label's line box as a share of its font size. */
const LABEL_LINE_HEIGHT = 1.2;
/** Space sigma leaves between a label and its attachment (pin, group tag; pixels). */
const LABEL_ATTACHMENT_GAP = 2;
/** Space between a focused knot and its focus ring (pixels). */
const FOCUS_RING_GAP = 5;
/**
 * A node focused by keyboard is brought into view when it is closer than this
 * to an edge: less than the room the framing leaves around the graph (see
 * ./framing), so focusing an entity of a freshly framed graph never moves it.
 */
const FOCUS_MARGIN = 24;

const NO_OP = () => undefined;
const NO_COVER: readonly Rect[] = [];

export const GRAPH_HELP =
  "Tab moves through the entities, Enter selects one, P pins it. Arrow keys pan, plus and minus " +
  "zoom, Q and E rotate, 0 resets the view. Press question mark for every shortcut.";

/**
 * Renders the graph with sigma (WebGL). All visual decisions live in the pure
 * functions of ./encoding and ./view, interactions in ./interactions and
 * ./keyboard; this component wires them to sigma and the DOM.
 */
export function GraphCanvas({
  graph,
  view,
  onHoverNode,
  onSelectNode,
  onToggleNode = NO_OP,
  onBoxSelect = NO_OP,
  onNodesMoved = NO_OP,
  onTogglePin = NO_OP,
  onContextMenu = NO_OP,
  onClearSelection = NO_OP,
  onShowShortcuts = NO_OP,
  onOpenViews = NO_OP,
  onReadyChange = NO_OP,
  onFocusChange = NO_OP,
  initialFocus = null,
  wheelSetting = "auto",
  showMinimap = false,
  showAllLabels = false,
  revision,
  pinRevision,
  pulse = NO_PULSE,
  palette = DEFAULT_GRAPH_PALETTE,
  theme = "dark",
  frameCover = NO_COVER,
  ref,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<CanvasSigma | null>(null);
  const selectionRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);
  // Read by the reducers on every refresh; updated before each refresh.
  const viewRef = useRef<{ state: ViewState; highlight: Highlight; context: RenderContext }>({
    state: view,
    highlight: computeHighlight(graph, view),
    context: { pulse, palette, theme, cameraRatio: 1 },
  });
  const handlers = {
    onHoverNode,
    onSelectNode,
    onToggleNode,
    onBoxSelect,
    onNodesMoved,
    onContextMenu,
  };
  const callbacksRef = useRef(handlers);
  const wheelRef = useRef(wheelSetting);
  const showAllLabelsRef = useRef(showAllLabels);
  // The visible graph is framed where the overlays leave room (see fittedCameraState).
  const coverKey = frameCover
    .map((r) => `${String(r.x)},${String(r.y)},${String(r.width)},${String(r.height)}`)
    .join(";");
  const coverRef = useRef(frameCover);
  const coverKeyRef = useRef(coverKey);
  const fittedRef = useRef<CameraState | null>(null);
  /** Frames the visible graph (animated unless `duration` is 0). */
  const fit = useCallback(
    (sigma: CanvasSigma, duration: number) => {
      const camera = sigma.getCamera();
      const visible = graph.filterNodes((node) =>
        isNodeVisible(graph, node, viewRef.current.state),
      );
      const state = fittedCameraState(sigma, visible, coverRef.current);
      if (state === null) {
        if (duration > 0) void camera.reset({ duration });
        else camera.setState({ x: 0.5, y: 0.5, ratio: 1, angle: 0 });
        fittedRef.current = null;
        return;
      }
      fittedRef.current = state;
      if (duration > 0) void camera.animate(state, { duration });
      else camera.setState(state);
    },
    [graph],
  );
  // The sigma instance once it exists (it may wait for the label font).
  const [sigmaInstance, setSigmaInstance] = useState<CanvasSigma | null>(null);
  // The node with the keyboard focus ring.
  const [focused, setFocused] = useState<string | null>(null);
  const focusedRef = useRef<string | null>(null);
  const [hint, setHint] = useState("");
  const initialFocusRef = useRef(initialFocus);
  const focusChangeRef = useRef(onFocusChange);

  // Labels (see ./labelLayout): the node reducer notes which nodes may show
  // one, planLabels picks those drawn after each render (none overlapping),
  // and the reducer forces exactly those; sigma's own choice is off.
  const labelsRef = useRef<{ wanted: Map<string, boolean>; shown: ReadonlySet<string> }>({
    wanted: new Map(),
    shown: new Set(),
  });
  /** Hovered, selected or keyboard-focused: labelled whatever else is near. */
  const isLabelMandatory = useCallback((node: string) => {
    const state = viewRef.current.state;
    return (
      node === state.hoveredNode ||
      node === state.selectedNode ||
      node === focusedRef.current ||
      (state.selectedNodes?.has(node) ?? false)
    );
  }, []);
  const withPlannedLabel = useCallback(
    (node: string, view: NodeView): NodeView => {
      const { wanted, shown } = labelsRef.current;
      if (view.visibility === "hidden" || view.labelVisibility === "hidden" || !view.label) {
        wanted.delete(node);
        return view;
      }
      // "visible" from the view: asked for (a group's count, the hovered
      // entity's neighbours), placed before the others.
      wanted.set(node, view.labelVisibility === "visible");
      const drawn = shown.has(node) || isLabelMandatory(node);
      return { ...view, labelVisibility: drawn ? "visible" : "hidden" };
    },
    [isLabelMandatory],
  );
  const planLabels = useCallback(
    (sigma: CanvasSigma) => {
      const { wanted, shown } = labelsRef.current;
      const { width, height } = sigma.getDimensions();
      const threshold = showAllLabelsRef.current ? 0 : LABEL_SIZE_THRESHOLD;
      const candidates: LabelCandidate[] = [];
      for (const [node, asked] of wanted) {
        if (!graph.hasNode(node)) continue;
        const data = sigma.getNodeDisplayData(node) as
          (NodeView & { x: number; y: number; size: number }) | undefined;
        if (!data || typeof data.label !== "string" || data.label === "") continue;
        const mandatory = isLabelMandatory(node);
        const radius = sigma.scaleSize(data.size);
        if (!mandatory && !asked && radius < threshold) continue;
        const point = sigma.framedGraphToViewport(data);
        const size = data.labelSize ?? LABEL_SIZE;
        const font = data.labelFont ?? LABEL_FONT;
        const attributes = graph.getNodeAttributes(node);
        const group = data.labelPosition === "over";
        candidates.push({
          node,
          x: point.x,
          y: point.y,
          radius,
          textWidth: measureText(data.label, font, size),
          textHeight: size * LABEL_LINE_HEIGHT,
          position: group ? "over" : "right",
          before: !group && attributes.pinned === true ? PIN_BOX + LABEL_ATTACHMENT_GAP : 0,
          ...(group
            ? {
                below: {
                  width:
                    measureText(
                      ENTITY_TYPE_INFO[attributes.entity.type].plural,
                      LABEL_FONT_STRONG,
                      TAG_FONT_SIZE,
                    ) +
                    2 * TAG_PADDING_X,
                  height: TAG_HEIGHT + LABEL_ATTACHMENT_GAP,
                },
              }
            : {}),
          mandatory,
          // Group counts, then labels the view asks for, then the
          // best-connected and largest knots.
          priority: (group ? 2e6 : 0) + (asked ? 1e6 : 0) + graph.degree(node) * 100 + radius,
        });
      }
      // Every knot drawn, which other labels keep off.
      const knots: KnotMark[] = [];
      graph.forEachNode((node) => {
        if (!isNodeVisible(graph, node, viewRef.current.state)) return;
        const data = sigma.getNodeDisplayData(node) as
          { x: number; y: number; size: number } | undefined;
        if (!data) return;
        const point = sigma.framedGraphToViewport(data);
        knots.push({ node, x: point.x, y: point.y, radius: sigma.scaleSize(data.size) });
      });
      const next = new Set(placeLabels(candidates, { width, height }, knots).keys());
      // Repaint only the nodes whose label the reducer now draws differently
      // (a mandatory label is drawn either way: repainting the hovered node
      // would make sigma pick it afresh, and could drop the hover).
      const changed: string[] = [];
      for (const node of next) {
        if (!shown.has(node) && !isLabelMandatory(node)) changed.push(node);
      }
      for (const node of shown) {
        if (!next.has(node) && graph.hasNode(node) && !isLabelMandatory(node)) changed.push(node);
      }
      labelsRef.current.shown = next;
      if (changed.length > 0) {
        sigma.refresh({ partialGraph: { nodes: changed }, skipIndexation: true, schedule: true });
      }
    },
    [graph, isLabelMandatory],
  );

  useEffect(() => {
    callbacksRef.current = handlers;
    wheelRef.current = wheelSetting;
    focusChangeRef.current = onFocusChange;
  });

  // The overlays changed: refit if the camera is still where the last fit put it.
  useEffect(() => {
    coverRef.current = frameCover;
    if (coverKeyRef.current === coverKey) return;
    coverKeyRef.current = coverKey;
    const sigma = sigmaInstance;
    const fitted = fittedRef.current;
    if (!sigma || !fitted) return;
    const now = sigma.getCamera().getState();
    const untouched =
      Math.abs(now.x - fitted.x) < 1e-6 &&
      Math.abs(now.y - fitted.y) < 1e-6 &&
      Math.abs(now.ratio - fitted.ratio) < 1e-6 &&
      Math.abs(now.angle - fitted.angle) < 1e-6;
    if (untouched) fit(sigma, 0);
  }, [coverKey, frameCover, sigmaInstance, fit]);
  // (Before sigma exists the cover is only remembered: creation fits with it.)

  // The focus ring follows its node through camera moves, drags and layouts.
  const placeRing = useCallback(() => {
    const ring = ringRef.current;
    const sigma = sigmaRef.current;
    const node = focusedRef.current;
    if (!ring) return;
    const point = sigma && node !== null ? nodeOnScreen(sigma, node) : null;
    if (!sigma || node === null || !point) {
      ring.hidden = true;
      return;
    }
    const radius = (sigma.getNodeDisplayData(node)?.size ?? 8) + FOCUS_RING_GAP;
    ring.hidden = false;
    ring.style.left = `${String(point.x - radius)}px`;
    ring.style.top = `${String(point.y - radius)}px`;
    ring.style.width = `${String(radius * 2)}px`;
    ring.style.height = `${String(radius * 2)}px`;
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let sigma: CanvasSigma | null = null;
    let detach: (() => void) | null = null;
    let cancelled = false;

    const create = () => {
      if (cancelled) return;
      const created: CanvasSigma = new Sigma(graph, container, {
        primitives: graphPrimitives(),
        settings: {
          // Node sizes are pixels (sigma 4 defaults to layout units).
          itemSizesReference: "screen",
          // A frame scheduled before the page navigates away can run after the
          // container left the DOM (zero width) but before sigma is killed;
          // skip drawing then instead of throwing.
          allowInvalidContainer: true,
          renderEdgeLabels: true,
          // Which labels show is decided here (see ./labelLayout and
          // planLabels below): only the labels it forces are drawn.
          labelDensity: 0,
          stagePadding: STAGE_PADDING,
          minCameraRatio: 0.08,
          maxCameraRatio: 4,
          // Dragging moves nodes (see ./interactions); a drag of a selected
          // node moves the whole multi-selection.
          enableNodeDrag: true,
          getDraggedNodes: (node: string) => {
            const selection = viewRef.current.state.selectedNodes;
            if (!selection?.has(node)) return [node];
            return [...selection].filter((id) => graph.hasNode(id));
          },
          // Right-drag rotation is ours (only on the stage, not over a node).
          enableCameraMouseRotation: false,
        },
        styles: {
          nodes: {
            x: { attribute: "x" },
            y: { attribute: "y" },
            size: { attribute: "size", defaultValue: 8 },
            color: { attribute: "color", defaultValue: DEFAULT_GRAPH_PALETTE.label },
            label: { attribute: "label" },
            // Labels stay horizontal, whatever the camera does.
            labelRotationAlignment: "viewport",
            cursor: "pointer",
          },
          edges: {
            size: { attribute: "size", defaultValue: 1 },
            color: { attribute: "color", defaultValue: DEFAULT_GRAPH_PALETTE.edge },
            label: { attribute: "label" },
            path: "curved",
            curvature: CURVATURE,
            parallelSpread: PARALLEL_SPREAD,
            // Only the focused entity's threads show their label (./view).
            labelVisibility: "hidden",
          },
        },
        nodeReducer: (node) =>
          withPlannedLabel(
            node,
            nodeView(
              graph,
              node,
              viewRef.current.state,
              viewRef.current.highlight,
              viewRef.current.context,
            ),
          ),
        edgeReducer: (edge) =>
          edgeView(
            graph,
            edge,
            viewRef.current.state,
            viewRef.current.highlight,
            viewRef.current.context,
          ),
      });
      sigma = created;
      sigmaRef.current = created;

      created.on("enterNode", ({ node }) => {
        callbacksRef.current.onHoverNode(node);
      });
      created.on("leaveNode", () => {
        callbacksRef.current.onHoverNode(null);
      });
      detach = attachInteractions(created, container, {
        handlers: (): InteractionHandlers => callbacksRef.current,
        visibleNodes: () =>
          graph.filterNodes((node) => isNodeVisible(graph, node, viewRef.current.state)),
        wheelSetting: () => wheelRef.current,
        drawSelection: (rect: Rect | null) => {
          const box = selectionRef.current;
          if (!box) return;
          box.hidden = rect === null;
          if (!rect) return;
          box.style.left = `${String(rect.x)}px`;
          box.style.top = `${String(rect.y)}px`;
          box.style.width = `${String(rect.width)}px`;
          box.style.height = `${String(rect.height)}px`;
        },
        holdNodes: (nodes) => {
          for (const node of nodes) {
            if (graph.hasNode(node)) graph.setNodeAttribute(node, "fixed", true);
          }
        },
      });
      // Threads get a little wider as the camera zooms in; repaint only when
      // the rounded width changes, not on every frame of a zoom.
      const camera = created.getCamera();
      fit(created, 0);
      fittedRef.current ??= camera.getState();
      let step = threadWidthStep(camera.ratio);
      camera.on("updated", ({ ratio }) => {
        const next = threadWidthStep(ratio);
        viewRef.current.context = { ...viewRef.current.context, cameraRatio: ratio };
        if (next === step) return;
        step = next;
        created.refresh({ skipIndexation: true });
      });
      created.on("afterRender", placeRing);
      created.on("afterRender", () => {
        planLabels(created);
      });
      setSigmaInstance(created);
    };

    const waiting = waitForLabelFont(typeof document === "undefined" ? undefined : document.fonts);
    if (waiting === null) create();
    else void waiting.then(create);

    return () => {
      cancelled = true;
      detach?.();
      sigma?.kill();
      sigmaRef.current = null;
      setSigmaInstance(null);
    };
  }, [graph, placeRing, fit, withPlannedLabel, planLabels]);

  const readyRef = useRef(onReadyChange);
  useEffect(() => {
    readyRef.current = onReadyChange;
  });
  const ready = sigmaInstance !== null;
  useEffect(() => {
    readyRef.current(ready);
  }, [ready]);

  useEffect(() => {
    if (showAllLabelsRef.current === showAllLabels) return;
    showAllLabelsRef.current = showAllLabels;
    if (sigmaInstance) planLabels(sigmaInstance);
  }, [sigmaInstance, showAllLabels, planLabels]);

  const highlight = computeHighlight(graph, view);
  useEffect(() => {
    const cameraRatio = viewRef.current.context.cameraRatio;
    viewRef.current = {
      state: view,
      highlight: computeHighlight(graph, view),
      context: { pulse, palette, theme, cameraRatio },
    };
    sigmaInstance?.refresh({ skipIndexation: true });
  }, [graph, view, revision, pulse, palette, theme, sigmaInstance]);
  const summary = focusSummary(graph, view, highlight);
  const summaryId = useId();
  const helpId = useId();

  // A focused node that left the graph or was hidden loses the focus.
  const focusValid =
    focused !== null && graph.hasNode(focused) && isNodeVisible(graph, focused, view);
  useEffect(() => {
    focusedRef.current = focusValid ? focused : null;
    placeRing();
  }, [focused, focusValid, placeRing, revision]);

  const announcement = useMemo(() => {
    if (!focusValid) return hint;
    const attributes = graph.getNodeAttributes(focused);
    let neighbours = 0;
    graph.forEachNeighbor(focused, (neighbor) => {
      if (isNodeVisible(graph, neighbor, view)) neighbours += 1;
    });
    const text = announceNode({
      type: attributes.entityType,
      label: attributes.entity.label,
      neighbours,
      pinned: attributes.pinned === true,
      ...(attributes.entity.group ? { members: attributes.entity.group.members.length } : {}),
    });
    return hint ? `${text}. ${hint}` : text;
    // pinRevision: the pinned state lives in the graph's attributes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, focused, focusValid, view, revision, pinRevision, hint]);

  const moveFocus = useCallback(
    (node: string | null) => {
      setHint("");
      setFocused(node);
      focusedRef.current = node;
      callbacksRef.current.onHoverNode(node);
      focusChangeRef.current(node);
      const sigma = sigmaRef.current;
      if (!sigma || node === null) {
        placeRing();
        return;
      }
      // Bring the node into view if it is near an edge or off screen.
      const point = nodeOnScreen(sigma, node);
      const { width, height } = sigma.getDimensions();
      if (
        point &&
        (point.x < FOCUS_MARGIN ||
          point.y < FOCUS_MARGIN ||
          point.x > width - FOCUS_MARGIN ||
          point.y > height - FOCUS_MARGIN)
      ) {
        panBy(sigma, point.x - width / 2, point.y - height / 2, true);
      }
      placeRing();
    },
    [placeRing],
  );

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    // Only keys pressed on the canvas itself, not on what is inside the stage.
    if (event.target !== event.currentTarget) return;
    const action = keyAction(event, focusValid);
    const sigma = sigmaRef.current;
    if (!action) return;
    switch (action.kind) {
      case "focusNext": {
        if (!sigma) return;
        const order = focusOrder(
          graph
            .filterNodes((node) => isNodeVisible(graph, node, view))
            .flatMap((id) => {
              const point = nodeOnScreen(sigma, id);
              return point ? [{ id, point }] : [];
            }),
        );
        const next = nextFocus(order, focusValid ? focused : null, action.backwards);
        if (next === null) {
          // Past either end: let Tab take focus on, out of the graph.
          moveFocus(null);
          return;
        }
        event.preventDefault();
        moveFocus(next);
        return;
      }
      case "escape":
        event.preventDefault();
        if (focusValid) moveFocus(null);
        else onClearSelection();
        return;
      case "select":
        event.preventDefault();
        if (focusValid) onSelectNode(focused);
        return;
      case "togglePin":
        event.preventDefault();
        if (focusValid) onTogglePin(focused);
        return;
      case "help":
        event.preventDefault();
        onShowShortcuts();
        return;
      case "views":
        event.preventDefault();
        onOpenViews();
        return;
      default:
        break;
    }
    if (!sigma) return;
    event.preventDefault();
    switch (action.kind) {
      case "pan":
        panBy(sigma, action.dx, action.dy, true);
        return;
      case "zoom":
        zoomStep(sigma, action.direction);
        return;
      case "rotate":
        rotateView(sigma, action.steps);
        return;
      case "reset":
        releaseFrame(sigma);
        sigma.refresh();
        fit(sigma, CAMERA_DURATION_MS);
        return;
      case "moveNode": {
        if (!focusValid) return;
        if (graph.getNodeAttribute(focused, "pinned") !== true) {
          setHint("Pin it first (P) to move it");
          return;
        }
        const point = nodeOnScreen(sigma, focused);
        if (!point) return;
        freezeFrame(sigma);
        const target = sigma.viewportToGraph({ x: point.x + action.dx, y: point.y + action.dy });
        graph.mergeNodeAttributes(focused, { x: target.x, y: target.y });
        callbacksRef.current.onNodesMoved([focused]);
        return;
      }
    }
  };

  useImperativeHandle(
    ref,
    () => ({
      focusNode(node) {
        // A node added a moment ago has no display data until sigma's next
        // render, so try again for a few frames.
        let attempts = 0;
        const tryFocus = () => {
          const sigma = sigmaRef.current;
          if (!sigma) return;
          const position = sigma.getNodeDisplayData(node);
          if (!position) {
            attempts += 1;
            if (attempts <= FOCUS_RETRY_FRAMES) requestAnimationFrame(tryFocus);
            return;
          }
          void sigma
            .getCamera()
            .animate(
              { x: position.x, y: position.y, ratio: FOCUS_RATIO },
              { duration: CAMERA_DURATION_MS },
            );
        };
        tryFocus();
      },
      resetCamera() {
        const sigma = sigmaRef.current;
        if (!sigma) return;
        // Fit the whole graph again, wherever nodes were dragged.
        releaseFrame(sigma);
        sigma.refresh();
        fit(sigma, CAMERA_DURATION_MS);
      },
      zoomIn() {
        const sigma = sigmaRef.current;
        if (sigma) zoomStep(sigma, 1);
      },
      zoomOut() {
        const sigma = sigmaRef.current;
        if (sigma) zoomStep(sigma, -1);
      },
      rotate(steps) {
        const sigma = sigmaRef.current;
        if (sigma) rotateView(sigma, steps);
      },
      getCamera() {
        const sigma = sigmaRef.current;
        return sigma ? graphCamera(sigma) : null;
      },
      setCamera(camera, durationMs) {
        const sigma = sigmaRef.current;
        if (sigma) moveCamera(sigma, camera, durationMs);
      },
    }),
    [fit],
  );

  return (
    <>
      {/* role="application": a focusable widget that handles its own keys (see ./keyboard). */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        ref={containerRef}
        className="graph-canvas"
        role="application"
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        aria-label="Investigation graph"
        aria-describedby={`${helpId} ${summaryId}`}
        aria-roledescription="graph"
        data-testid="graph-canvas"
        // "true" once sigma draws the graph (tests wait for it).
        data-ready={sigmaInstance ? "true" : "false"}
        onKeyDown={onKeyDown}
        onFocus={(event) => {
          if (event.target !== event.currentTarget) return;
          const initial = initialFocusRef.current;
          initialFocusRef.current = null;
          if (initial !== null && graph.hasNode(initial) && isNodeVisible(graph, initial, view)) {
            moveFocus(initial);
          }
        }}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) {
            if (focusedRef.current !== null) moveFocus(null);
          }
        }}
      />
      <div ref={ringRef} className="graph-focus-ring" hidden aria-hidden="true" />
      <div ref={selectionRef} className="graph-selection" hidden aria-hidden="true" />
      {showMinimap && sigmaInstance && (
        <Minimap sigma={sigmaInstance} graph={graph} view={view} palette={palette} />
      )}
      <p id={helpId} className="visually-hidden">
        {GRAPH_HELP}
      </p>
      {/*
       * The relationships whose labels the canvas shows (the hovered or
       * selected entity's): the canvas's description, announced politely.
       */}
      <p id={summaryId} className="visually-hidden" aria-live="polite">
        {summary}
      </p>
      {/* The entity with the keyboard focus ring. */}
      <p className="visually-hidden" aria-live="polite" data-testid="graph-announcer">
        {announcement}
      </p>
    </>
  );
}
