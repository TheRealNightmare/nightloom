/**
 * The 3D view of the graph: the same graphology graph, layout, pins,
 * selection, filters and groups as 2D (GraphView owns them all), drawn with
 * three.js through React Three Fiber (./Scene3D). x / y are the layout's,
 * z comes from the depth mode (./depth).
 *
 * This component is the DOM side: the focusable canvas element and its
 * keyboard map, pointer interactions (./interactions3d, with three's
 * OrbitControls for orbit / pan / touch), the HTML label layer, the depth
 * ruler, the focus ring, the selection rectangle, the minimap and what is
 * announced. Each drawn frame it brings the GPU buffers up to date and
 * places the labels; between changes nothing runs (frameloop "demand").
 *
 * Loaded lazily (its own chunk with three.js), only once the user picks 3D.
 */
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
import { PerspectiveCamera, Quaternion, Vector3 } from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

import { ENTITY_TYPE_INFO } from "../../domain/entities";
import { DEFAULT_GRAPH_PALETTE, type GraphPalette } from "../../theme/graphPalette";
import { token, type Theme } from "../../theme/tokens";
import type { NightloomGraph } from "../buildGraph";
import { announceNode, nextFocus } from "../focusOrder";
import type { Point, Rect } from "../geometry";
import type { GraphCanvasHandle } from "../GraphCanvas";
import { ZOOM_DURATION_MS, ZOOM_STEP, type GraphCamera } from "../interactions";
import type { Camera3D, DepthMode } from "../mode";
import type { MinimapDot } from "../minimap";
import { computeHighlight, focusSummary, isNodeVisible, NO_PULSE, type ViewState } from "../view";
import type { WheelSetting } from "../wheel";
import {
  boundedCamera,
  boundingSphere,
  camera2dTo3d,
  camera3dTo2d,
  distanceToShow,
  FOV_DEGREES,
  inView2d,
  distanceOf,
  dolly,
  dollyToward,
  easeInOut,
  flyTo,
  frameSphere,
  interpolateCamera,
  moveTarget,
  orbit,
  pan,
  RESET_DIRECTION,
  type Vec3,
} from "./camera3d";
import { depthAnimationMs, type RulerTick } from "./depth";
import { attachInteractions3d } from "./interactions3d";
import { GRAPH_HELP_3D, keyAction3d, TURN_STEP } from "./keyboard3d";
import { MAX_LABELS, selectLabels, type LabelCandidate } from "./labels3d";
import { lookAtOnMap } from "./minimap3d";
import { dragDelta, focusOrder3d, knotScreenRadius, nodesInScreenRect, pickNode } from "./picking";
import { clipPlanes, projectAll, viewProjection, type Viewport } from "./projection";
import {
  buildModel,
  ensureKnotCapacity,
  ensureThreadCapacity,
  hexToRgb,
  knotBuffers,
  threadBuffers,
  updatePositions,
  writeKnotPositions,
  writeKnotStyles,
  writeThreadColors,
  writeThreadPositions,
  type Extent,
  type SceneModel,
} from "./scene";
import { Minimap3D, type Minimap3DSource } from "./Minimap3D";
// The R3F canvas (three's renderer): the only part the component tests replace.
import { Scene3D, type SceneApi, type SceneBuffers, type SceneColors } from "./Scene3D";

/** What the 3D canvas adds to the 2D canvas's handle. */
export interface GraphCanvas3DHandle extends GraphCanvasHandle {
  getCamera3d: () => Camera3D | null;
  setCamera3d: (camera: Camera3D, durationMs: number) => void;
}

export interface GraphCanvas3DProps {
  graph: NightloomGraph;
  view: ViewState;
  depth: DepthMode;
  onHoverNode: (node: string | null) => void;
  onSelectNode: (node: string | null) => void;
  onToggleNode?: (node: string) => void;
  onBoxSelect?: (nodes: string[], additive: boolean) => void;
  onNodesMoved?: (nodes: string[]) => void;
  onTogglePin?: (node: string) => void;
  onContextMenu?: (node: string, point: Point) => void;
  onClearSelection?: () => void;
  onShowShortcuts?: () => void;
  onOpenViews?: () => void;
  onReadyChange?: (ready: boolean) => void;
  /** The node with the keyboard focus ring changed (kept across a switch of mode). */
  onFocusChange?: (node: string | null) => void;
  /** Given the focus ring when the canvas first takes the keyboard focus. */
  initialFocus?: string | null;
  /** Where the camera starts (else it frames the graph while the layout settles). */
  initialCamera?: Camera3D | null;
  /** The WebGL context was lost / restored (the page shows 2D meanwhile). */
  onContextLost?: () => void;
  onContextRestored?: () => void;
  /** Kept mounted but not shown (while the context is lost). */
  hidden?: boolean;
  wheelSetting?: WheelSetting;
  showMinimap?: boolean;
  revision?: unknown;
  pinRevision?: unknown;
  pulse?: ReadonlySet<string>;
  palette?: GraphPalette;
  theme?: Theme;
  ref?: Ref<GraphCanvas3DHandle>;
}

const NO_OP = () => undefined;
/** The focus ring's gap around its knot, and the margin that brings a focused knot into view. */
const FOCUS_RING_GAP = 5;
const FOCUS_MARGIN = 48;
/** Fly-to: how long (≤ 600 ms), and how much of the view the graph's radius takes. */
export const FLY_MS = 550;
const FLY_DISTANCE_RATIO = 0.6;
const RESET_MS = 450;
/** Knots are labelled beside them, group counts over them; labels this far beyond a group count. */
const RULER_OFFSET = 0.08;
const WARP_THREADS = 9;

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

function readCamera(camera: PerspectiveCamera, target: Vector3): Camera3D {
  return {
    position: [camera.position.x, camera.position.y, camera.position.z],
    target: [target.x, target.y, target.z],
    up: [camera.up.x, camera.up.y, camera.up.z],
  };
}

const Y_AXIS = new Vector3(0, 1, 0);

/** OrbitControls caches the rotation from the camera's up to +y when it is created. */
function syncControlsUp(controls: OrbitControls, up: Vector3): void {
  const internal = controls as unknown as { _quat?: Quaternion; _quatInverse?: Quaternion };
  if (!internal._quat || !internal._quatInverse) return;
  internal._quat.setFromUnitVectors(up.clone().normalize(), Y_AXIS);
  internal._quatInverse.copy(internal._quat).invert();
}

interface Animation {
  from: Camera3D;
  to: Camera3D;
  start: number;
  duration: number;
}

interface Drag {
  nodes: string[];
  starts: Map<string, [number, number]>;
  anchor: Vec3;
  start: Point;
}

/** Whatever changes each frame; kept outside React state (no re-render per frame). */
interface Runtime {
  model: SceneModel | null;
  modelDirty: boolean;
  positionsDirty: boolean;
  depthStart: number;
  depthDuration: number;
  extent: Extent;
  autoFrame: boolean;
  animation: Animation | null;
  drag: Drag | null;
  frozenSpan: number | null;
  projected: Float32Array;
  radii: Float32Array;
  depthScratch: Float32Array;
  viewport: Viewport;
  candidates: LabelCandidate[];
  frame: number;
  /** A 2D camera just applied: what it showed must stay on screen (see onFrame). */
  keepInView: GraphCamera | null;
}

interface Stage {
  camera: PerspectiveCamera;
  buffers: SceneBuffers;
}

function createStage(initial: Camera3D | null): Stage {
  const camera = new PerspectiveCamera(FOV_DEGREES, 1, 0.1, 10_000);
  const start = initial ?? { position: [0, 0, 600], target: [0, 0, 0], up: [0, 1, 0] };
  camera.position.set(...start.position);
  camera.up.set(...start.up);
  camera.lookAt(...start.target);
  return {
    camera,
    buffers: {
      entities: knotBuffers(64),
      groups: knotBuffers(16),
      threads: threadBuffers(64),
      warp: new Float32Array((WARP_THREADS + 16) * 2 * 3),
      warpVertices: 0,
      allocation: 0,
      styleVersion: 0,
      positionVersion: 0,
    },
  };
}

function stageOf(ref: { current: Stage | null }): Stage {
  if (ref.current === null) throw new Error("The 3D stage is created on the first render.");
  return ref.current;
}

/** The warp guide: threads along the depth axis under the graph, a weft line at each ruler mark. */
function writeWarp(
  buffers: SceneBuffers,
  model: SceneModel,
  extent: Extent,
  depth: DepthMode,
): void {
  const out = buffers.warp;
  if (depth === "flat" || model.count === 0) {
    buffers.warpVertices = 0;
    return;
  }
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < model.count; i += 1) {
    const z = model.positions[i * 3 + 2] ?? 0;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }
  const margin = extent.radius * RULER_OFFSET;
  const floor = extent.cy - extent.radius - margin;
  const left = extent.cx - extent.radius;
  let at = 0;
  const segment = (a: Vec3, b: Vec3) => {
    if (at + 6 > out.length) return;
    out.set(a, at);
    out.set(b, at + 3);
    at += 6;
  };
  // Warp threads along the depth axis, under the graph.
  for (let k = 0; k < WARP_THREADS; k += 1) {
    const x = left + (2 * extent.radius * k) / (WARP_THREADS - 1);
    segment([x, floor, minZ - margin], [x, floor, maxZ + margin]);
  }
  // One weft line across at each ruler mark.
  for (const tick of model.ticks.slice(0, 16)) {
    const z = tick.z * model.span;
    segment([left, floor, z], [left + 2 * extent.radius, floor, z]);
  }
  buffers.warpVertices = at / 3;
}

function placeLabels(
  layer: HTMLDivElement | null,
  pool: HTMLSpanElement[],
  model: SceneModel,
  r: Runtime,
  viewport: Viewport,
  graph: NightloomGraph,
  state: ViewState,
  focusedNode: string | null,
): void {
  if (!layer) return;
  const anyFocus = computeHighlight(graph, state).focus !== null;
  const candidates = r.candidates;
  candidates.length = 0;
  for (let i = 0; i < model.count; i += 1) {
    const id = model.ids[i] ?? "";
    const group = model.isGroup[i] === 1;
    const mandatory =
      group || model.selected[i] === 1 || id === state.hoveredNode || id === focusedNode;
    // While something is hovered or selected, only its neighbourhood is labelled (as in 2D).
    if (!mandatory && anyFocus && model.near[i] !== 1) continue;
    candidates.push({
      index: i,
      x: r.projected[i * 3] ?? 0,
      y: r.projected[i * 3 + 1] ?? 0,
      depth: r.projected[i * 3 + 2] ?? 0,
      radius: r.radii[i] ?? 0,
      chars: (model.labels[i] ?? "").length,
      degree: (model.degree[i] ?? 0) + (model.near[i] === 1 ? 1e6 : 0),
      mandatory,
      centered: group,
    });
  }
  const placed = selectLabels(candidates, viewport, MAX_LABELS);
  while (pool.length < placed.length) {
    const element = document.createElement("span");
    element.className = "graph3d-label";
    layer.appendChild(element);
    pool.push(element);
  }
  placed.forEach((label, n) => {
    const element = pool[n];
    if (!element) return;
    const i = label.index;
    const entity = model.entities[i];
    const text = model.labels[i] ?? "";
    const group = model.isGroup[i] === 1;
    const className = [
      "graph3d-label",
      group ? "graph3d-label--group" : "",
      model.selected[i] === 1 ? "graph3d-label--selected" : "",
      model.pinned[i] === 1 ? "graph3d-label--pinned" : "",
    ]
      .filter(Boolean)
      .join(" ");
    if (element.className !== className) element.className = className;
    const content = group && entity ? `${text} ${ENTITY_TYPE_INFO[entity.type].plural}` : text;
    if (element.dataset["content"] !== content) {
      element.dataset["content"] = content;
      if (group && entity) {
        element.replaceChildren(text);
        const kind = document.createElement("small");
        kind.textContent = ENTITY_TYPE_INFO[entity.type].plural;
        element.appendChild(kind);
      } else {
        element.textContent = text;
      }
    }
    element.hidden = false;
    element.style.transform = `translate(${String(Math.round(label.box.x))}px, ${String(Math.round(label.box.y))}px)`;
  });
  for (let n = placed.length; n < pool.length; n += 1) {
    const element = pool[n];
    if (element && !element.hidden) element.hidden = true;
  }
}

function placeRuler(
  layer: HTMLDivElement | null,
  pool: readonly (HTMLSpanElement | undefined)[],
  ticks: readonly RulerTick[],
  model: SceneModel,
  r: Runtime,
  matrix: Float64Array,
  viewport: Viewport,
): void {
  if (!layer) return;
  const { cx, cy, radius } = r.extent;
  const margin = radius * RULER_OFFSET;
  const point = new Float32Array(3);
  const out = new Float32Array(3);
  ticks.forEach((tick, n) => {
    const element = pool[n];
    if (!element) return;
    point[0] = cx - radius - margin;
    point[1] = cy - radius - margin;
    point[2] = tick.z * model.span;
    projectAll(matrix, point, 1, viewport, out);
    const visible = (out[2] ?? 0) > 0;
    element.hidden = !visible;
    if (visible) {
      element.style.transform = `translate(${String(Math.round(out[0] ?? 0))}px, ${String(Math.round(out[1] ?? 0))}px)`;
    }
  });
}

function placeRing(
  ring: HTMLDivElement | null,
  node: string | null,
  model: SceneModel,
  r: Runtime,
): void {
  if (!ring) return;
  const at = node === null ? undefined : model.index.get(node);
  if (at === undefined || (r.projected[at * 3 + 2] ?? 0) <= 0) {
    ring.hidden = true;
    return;
  }
  const radius = (r.radii[at] ?? 8) + FOCUS_RING_GAP;
  ring.hidden = false;
  ring.style.left = `${String((r.projected[at * 3] ?? 0) - radius)}px`;
  ring.style.top = `${String((r.projected[at * 3 + 1] ?? 0) - radius)}px`;
  ring.style.width = `${String(radius * 2)}px`;
  ring.style.height = `${String(radius * 2)}px`;
}

export function GraphCanvas3D({
  graph,
  view,
  depth,
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
  initialCamera = null,
  onContextLost = NO_OP,
  onContextRestored = NO_OP,
  hidden = false,
  wheelSetting = "auto",
  showMinimap = false,
  revision,
  pinRevision,
  pulse = NO_PULSE,
  palette = DEFAULT_GRAPH_PALETTE,
  theme = "dark",
  ref,
}: GraphCanvas3DProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  const rulerRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);
  const selectionRef = useRef<HTMLDivElement>(null);
  // The three.js camera and the GPU buffers: mutable, outside React's state.
  const stage = useRef<Stage | null>(null);
  stage.current ??= createStage(initialCamera);
  const targetRef = useRef(new Vector3(...(initialCamera?.target ?? [0, 0, 0])));
  const controlsRef = useRef<OrbitControls | null>(null);
  const apiRef = useRef<SceneApi | null>(null);
  const runtime = useRef<Runtime>({
    model: null,
    modelDirty: true,
    positionsDirty: true,
    depthStart: 0,
    depthDuration: 0,
    extent: { cx: 0, cy: 0, radius: 1 },
    autoFrame: initialCamera === null,
    animation: null,
    drag: null,
    frozenSpan: null,
    projected: new Float32Array(0),
    radii: new Float32Array(0),
    depthScratch: new Float32Array(0),
    viewport: { width: 1, height: 1 },
    candidates: [],
    frame: 0,
    keepInView: null,
  });
  const [ready, setReady] = useState(false);
  // Called after each drawn frame (the minimap redraws, throttled).
  const frameListeners = useRef(new Set<() => void>());
  const [ticks, setTicks] = useState<RulerTick[]>([]);
  const [focused, setFocused] = useState<string | null>(null);
  const focusedRef = useRef<string | null>(null);
  const initialFocusRef = useRef(initialFocus);
  const [hint, setHint] = useState("");

  // Latest props for the event handlers and the frame callback.
  const props = {
    graph,
    view,
    depth,
    pulse,
    palette,
    wheelSetting,
    onHoverNode,
    onSelectNode,
    onToggleNode,
    onBoxSelect,
    onNodesMoved,
    onContextMenu,
    onFocusChange,
    onContextLost,
    onContextRestored,
  };
  const propsRef = useRef(props);
  useEffect(() => {
    propsRef.current = props;
  });

  const invalidate = useCallback(() => {
    apiRef.current?.invalidate();
  }, []);

  // --- camera -------------------------------------------------------------------------
  const current = useCallback(
    (): Camera3D => readCamera(stageOf(stage).camera, targetRef.current),
    [],
  );
  const write = useCallback(
    (next: Camera3D) => {
      const { camera } = stageOf(stage);
      const upChanged =
        Math.abs(camera.up.x - next.up[0]) +
          Math.abs(camera.up.y - next.up[1]) +
          Math.abs(camera.up.z - next.up[2]) >
        1e-9;
      camera.position.set(...next.position);
      camera.up.set(...next.up);
      targetRef.current.set(...next.target);
      camera.lookAt(targetRef.current);
      const controls = controlsRef.current;
      if (controls) {
        if (upChanged) syncControlsUp(controls, camera.up);
        controls.target.copy(targetRef.current);
      }
      camera.updateMatrixWorld();
      invalidate();
    },
    [invalidate],
  );
  const animateTo = useCallback(
    (to: Camera3D, durationMs: number) => {
      const r = runtime.current;
      r.autoFrame = false;
      const duration = prefersReducedMotion() ? 0 : durationMs;
      if (duration <= 0) {
        r.animation = null;
        write(to);
        return;
      }
      r.animation = { from: current(), to, start: performance.now(), duration };
      invalidate();
    },
    [current, write, invalidate],
  );
  const framed = useCallback(
    (direction: Vec3 = RESET_DIRECTION, up: Vec3 = [0, 1, 0]): Camera3D | null => {
      const model = runtime.current.model;
      if (!model || model.count === 0) return null;
      const { width, height } = runtime.current.viewport;
      return frameSphere(
        boundingSphere(model.positions, model.count),
        width / Math.max(height, 1),
        direction,
        up,
      );
    },
    [],
  );
  const positionOf = useCallback((node: string): Vec3 | null => {
    const model = runtime.current.model;
    const at = model?.index.get(node);
    if (!model || at === undefined) return null;
    return [
      model.positions[at * 3] ?? 0,
      model.positions[at * 3 + 1] ?? 0,
      model.positions[at * 3 + 2] ?? 0,
    ];
  }, []);
  const flyToNode = useCallback(
    (node: string) => {
      let attempts = 0;
      const tryFly = () => {
        const point = positionOf(node);
        if (!point) {
          attempts += 1;
          // A node that just appeared (an expanded group) is in the model after the next frame.
          if (attempts <= 10) requestAnimationFrame(tryFly);
          invalidate();
          return;
        }
        const radius = runtime.current.extent.radius;
        animateTo(flyTo(current(), point, Math.max(radius * FLY_DISTANCE_RATIO, 40)), FLY_MS);
      };
      tryFly();
    },
    [positionOf, animateTo, current, invalidate],
  );
  /** The viewport's height (the element's until a frame has measured it). */
  const viewportHeight = useCallback((): number => {
    const measured = runtime.current.viewport.height;
    if (measured > 1) return measured;
    const element = containerRef.current?.clientHeight ?? 0;
    return element > 0 ? element : 600;
  }, []);
  const referenceDepth = useCallback(() => distanceOf(current()), [current]);
  const resetCamera = useCallback(() => {
    const next = framed();
    if (next) animateTo(next, RESET_MS);
  }, [framed, animateTo]);
  const zoomCenter = useCallback(
    (direction: 1 | -1) => {
      animateTo(dolly(current(), direction > 0 ? 1 / ZOOM_STEP : ZOOM_STEP), ZOOM_DURATION_MS);
    },
    [animateTo, current],
  );
  const turn = useCallback(
    (steps: number) => {
      animateTo(orbit(current(), steps * TURN_STEP, 0), ZOOM_DURATION_MS);
    },
    [animateTo, current],
  );

  // --- scene ----------------------------------------------------------------------------
  // Graph changes: positions (layout, drags) or structure / looks.
  useEffect(() => {
    const r = runtime.current;
    const moved = () => {
      r.positionsDirty = true;
      invalidate();
    };
    const changed = () => {
      r.modelDirty = true;
      invalidate();
    };
    const onNodeAttributes = (payload: { key?: string }) => {
      if (payload.key === "x" || payload.key === "y" || payload.key === "fixed") moved();
      else changed();
    };
    const onEachNodeAttributes = (payload: { hints?: { attributes?: string[] } }) => {
      const keys = payload.hints?.attributes;
      if (keys?.every((key) => key === "x" || key === "y")) moved();
      else changed();
    };
    graph.on("nodeAttributesUpdated", onNodeAttributes);
    graph.on("eachNodeAttributesUpdated", onEachNodeAttributes);
    const structural = ["nodeAdded", "nodeDropped", "edgeAdded", "edgeDropped", "cleared"] as const;
    for (const event of structural) graph.on(event, changed);
    return () => {
      graph.off("nodeAttributesUpdated", onNodeAttributes);
      graph.off("eachNodeAttributesUpdated", onEachNodeAttributes);
      for (const event of structural) graph.off(event, changed);
    };
  }, [graph, invalidate]);

  // What is drawn, and how, changed.
  useEffect(() => {
    runtime.current.modelDirty = true;
    invalidate();
  }, [view, revision, pinRevision, pulse, palette, invalidate]);

  // A new depth mode: animate from where the knots are now.
  const depthRef = useRef(depth);
  useEffect(() => {
    if (depthRef.current === depth) return;
    depthRef.current = depth;
    const r = runtime.current;
    r.modelDirty = true;
    r.depthStart = performance.now();
    r.depthDuration = depthAnimationMs(prefersReducedMotion());
    invalidate();
  }, [depth, invalidate]);

  const colors = useMemo<SceneColors>(
    () => ({
      background: hexToRgb(palette.background),
      surface: hexToRgb(palette.surface),
      selection: hexToRgb(palette.selection),
      grid: hexToRgb(token(theme, "--grid")),
    }),
    [palette, theme],
  );

  // --- the frame: buffers, camera, labels ------------------------------------------------------
  const labelPool = useRef<HTMLSpanElement[]>([]);
  const rulerPool = useRef<HTMLSpanElement[]>([]);
  const lastTicks = useRef<RulerTick[]>([]);

  const onFrame = useCallback(
    (viewport: Viewport) => {
      const { camera, buffers } = stageOf(stage);
      const r = runtime.current;
      const p = propsRef.current;
      r.viewport = viewport;
      const now = performance.now();

      if (r.modelDirty) {
        r.modelDirty = false;
        const state = p.view;
        const model = buildModel(
          p.graph,
          state,
          computeHighlight(p.graph, state),
          { palette: p.palette, pulse: p.pulse, depth: p.depth },
          r.model,
        );
        r.model = model;
        const entityCount = model.count - model.isGroup.reduce((sum, value) => sum + value, 0);
        const entities = ensureKnotCapacity(buffers.entities, entityCount);
        const groups = ensureKnotCapacity(buffers.groups, model.count - entityCount);
        const threads = ensureThreadCapacity(buffers.threads, model.edgeCount);
        if (
          entities !== buffers.entities ||
          groups !== buffers.groups ||
          threads !== buffers.threads
        ) {
          buffers.entities = entities;
          buffers.groups = groups;
          buffers.threads = threads;
          buffers.allocation += 1;
        }
        writeKnotStyles(model, buffers.entities, false);
        writeKnotStyles(model, buffers.groups, true);
        writeThreadColors(model, buffers.threads);
        buffers.styleVersion += 1;
        if (r.projected.length < model.count * 3) {
          r.projected = new Float32Array(model.count * 3);
          r.radii = new Float32Array(model.count);
          r.depthScratch = new Float32Array(model.count);
        }
        r.positionsDirty = true;
        if (lastTicks.current !== model.ticks) {
          lastTicks.current = model.ticks;
          setTicks(model.ticks);
        }
      }

      const model = r.model;
      if (!model) return;
      let t = 1;
      if (r.depthDuration > 0) {
        t = easeInOut((now - r.depthStart) / r.depthDuration);
        if (t < 1) {
          r.positionsDirty = true;
          invalidate();
        } else {
          r.depthDuration = 0;
        }
      }
      if (r.positionsDirty) {
        r.positionsDirty = false;
        r.extent = updatePositions(model, p.graph, t, r.frozenSpan ?? undefined, r.depthScratch);
        writeKnotPositions(model, buffers.entities);
        writeKnotPositions(model, buffers.groups);
        writeThreadPositions(model, buffers.threads);
        writeWarp(buffers, model, r.extent, p.depth);
        buffers.positionVersion += 1;
        if (r.autoFrame) {
          const next = framed();
          if (next) write(next);
        }
      }

      // A 2D camera seen in 3D: depth brings some knots nearer, so move back
      // (never closer) until everything the 2D view showed is on screen.
      if (r.keepInView) {
        const shown = r.keepInView;
        r.keepInView = null;
        const indices: number[] = [];
        for (let i = 0; i < model.count; i += 1) {
          const x = model.positions[i * 3] ?? 0;
          const y = model.positions[i * 3 + 1] ?? 0;
          if (inView2d(shown, x, y, viewport.width, viewport.height)) indices.push(i);
        }
        const goal = r.animation?.to ?? current();
        const aspect = viewport.width / Math.max(viewport.height, 1);
        const needed = distanceToShow(goal, model.positions, indices, aspect);
        const now = distanceOf(goal);
        if (needed > now && now > 0) {
          const moved = dolly(goal, needed / now);
          if (r.animation) r.animation = { ...r.animation, to: moved };
          else write(moved);
        }
      }

      // Camera animation.
      if (r.animation) {
        const { from, to, start, duration } = r.animation;
        const k = (now - start) / duration;
        if (k >= 1) {
          r.animation = null;
          write(to);
        } else {
          write(interpolateCamera(from, to, easeInOut(k)));
        }
      }

      // Clip planes around the scene, then project every knot.
      const cam = current();
      const distance = distanceOf(cam);
      const sceneRadius = Math.max(r.extent.radius, model.span);
      const reach = Math.hypot(
        cam.position[0] - r.extent.cx,
        cam.position[1] - r.extent.cy,
        cam.position[2],
      );
      const { near, far } = clipPlanes(Math.max(distance, 1), reach + sceneRadius);
      // The camera is ours (not R3F's default), so its aspect is kept here too.
      const aspect = viewport.width / Math.max(viewport.height, 1);
      if (camera.near !== near || camera.far !== far || camera.aspect !== aspect) {
        camera.near = near;
        camera.far = far;
        camera.aspect = aspect;
        camera.updateProjectionMatrix();
      }
      const matrix = viewProjection(cam, aspect, near, far);
      projectAll(matrix, model.positions, model.count, viewport, r.projected);
      for (let i = 0; i < model.count; i += 1) {
        r.radii[i] = knotScreenRadius(model.sizePx[i] ?? 0, r.projected[i * 3 + 2] ?? 0, distance);
      }

      // Labels.
      placeLabels(
        labelsRef.current,
        labelPool.current,
        model,
        r,
        viewport,
        p.graph,
        p.view,
        focusedRef.current,
      );
      placeRuler(
        rulerRef.current,
        rulerPool.current,
        p.depth === "flat" ? [] : model.ticks,
        model,
        r,
        matrix,
        viewport,
      );
      placeRing(ringRef.current, focusedRef.current, model, r);
      r.frame += 1;
      if (r.frame === 1) setReady(true);
      containerRef.current?.setAttribute("data-frame", String(r.frame));
      for (const listener of frameListeners.current) listener();
    },

    [current, framed, invalidate, write],
  );

  // --- picking (from the last frame's projection) ------------------------------------------
  const screenNodes = useCallback(() => {
    const r = runtime.current;
    const model = r.model;
    return {
      ids: model?.ids ?? [],
      projected: r.projected,
      radii: r.radii,
      count: model?.count ?? 0,
    };
  }, []);
  const pick = useCallback((point: Point) => pickNode(screenNodes(), point), [screenNodes]);

  // --- OrbitControls and pointer interactions ------------------------------------------------
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const { camera } = stageOf(stage);
    const controls = new OrbitControls(camera, container);
    controls.enableDamping = false;
    controls.screenSpacePanning = true;
    controls.zoomToCursor = true;
    controls.rotateSpeed = 0.8;
    controls.minDistance = 0.5;
    controls.maxDistance = 2e5;
    controls.target.copy(targetRef.current);
    syncControlsUp(controls, camera.up);
    controls.update();
    controlsRef.current = controls;
    const onChange = () => {
      targetRef.current.copy(controls.target);
      invalidate();
    };
    const onStart = () => {
      runtime.current.autoFrame = false;
      runtime.current.animation = null;
    };
    controls.addEventListener("change", onChange);
    controls.addEventListener("start", onStart);

    const viewportOf = () => runtime.current.viewport;
    const detach = attachInteractions3d(container, {
      handlers: () => propsRef.current,
      pick,
      nodesInRect: (rect: Rect) => nodesInScreenRect(screenNodes(), rect),
      wheelSetting: () => propsRef.current.wheelSetting,
      drawSelection: (rect) => {
        const box = selectionRef.current;
        if (!box) return;
        box.hidden = rect === null;
        if (!rect) return;
        box.style.left = `${String(rect.x)}px`;
        box.style.top = `${String(rect.y)}px`;
        box.style.width = `${String(rect.width)}px`;
        box.style.height = `${String(rect.height)}px`;
      },
      beginDrag: (node, at) => {
        const r = runtime.current;
        const { graph: g, view: state } = propsRef.current;
        const selection = state.selectedNodes;
        const nodes = (selection?.has(node) ? [...selection] : [node]).filter((id) =>
          g.hasNode(id),
        );
        const starts = new Map<string, [number, number]>();
        for (const id of nodes) {
          const { x, y } = g.getNodeAttributes(id);
          starts.set(id, [x, y]);
          g.setNodeAttribute(id, "fixed", true);
        }
        r.frozenSpan = r.model?.span ?? null;
        r.autoFrame = false;
        r.drag = { nodes, starts, anchor: positionOf(node) ?? [...current().target], start: at };
        return nodes;
      },
      dragTo: (at) => {
        const r = runtime.current;
        const drag = r.drag;
        if (!drag) return;
        const delta = dragDelta(current(), viewportOf(), drag.anchor, drag.start, at);
        if (!delta) return;
        const g = propsRef.current.graph;
        for (const [id, [x, y]] of drag.starts) {
          if (g.hasNode(id)) g.mergeNodeAttributes(id, { x: x + delta.dx, y: y + delta.dy });
        }
      },
      endDrag: () => {
        const r = runtime.current;
        const drag = r.drag;
        r.drag = null;
        r.frozenSpan = null;
        if (drag) propsRef.current.onNodesMoved(drag.nodes);
      },
      zoomAt: (at, factor, animate) => {
        const cam = current();
        const anchor = pickedAnchor(at) ?? unprojectAtTarget(cam, at);
        const next = dollyToward(cam, anchor, factor);
        if (animate) animateTo(next, ZOOM_DURATION_MS);
        else {
          runtime.current.animation = null;
          write(next);
        }
      },
      panBy: (dx, dy) => {
        runtime.current.animation = null;
        write(pan(current(), dx, dy, viewportOf().height));
      },
      flyTo: flyToNode,
      onUserCamera: () => {
        runtime.current.autoFrame = false;
        runtime.current.animation = null;
      },
      setOverNode: (over) => {
        container.classList.toggle("graph3d--over-node", over);
      },
    });

    // The world point under the pointer: a knot's own depth if one is there, else the target's plane.
    const pickedAnchor = (at: Point): Vec3 | null => {
      const node = pick(at);
      return node === null ? null : positionOf(node);
    };
    const unprojectAtTarget = (cam: Camera3D, at: Point): Vec3 => {
      const { width, height } = viewportOf();
      const aspect = width / Math.max(height, 1);
      const ndcX = (at.x / Math.max(width, 1)) * 2 - 1;
      const ndcY = 1 - (at.y / Math.max(height, 1)) * 2;
      const tan = Math.tan((FOV_DEGREES * Math.PI) / 360);
      const distance = distanceOf(cam);
      const forward = new Vector3(...cam.target).sub(new Vector3(...cam.position)).normalize();
      const right = new Vector3().crossVectors(forward, new Vector3(...cam.up)).normalize();
      const up = new Vector3().crossVectors(right, forward);
      const point = new Vector3(...cam.target)
        .addScaledVector(right, ndcX * tan * aspect * distance)
        .addScaledVector(up, ndcY * tan * distance);
      return [point.x, point.y, point.z];
    };

    return () => {
      detach();
      controls.removeEventListener("change", onChange);
      controls.removeEventListener("start", onStart);
      controls.dispose();
      controlsRef.current = null;
    };
  }, [invalidate, pick, screenNodes, positionOf, current, write, animateTo, flyToNode]);

  // --- WebGL context ------------------------------------------------------------------------
  const unmounting = useRef(false);
  useEffect(() => {
    unmounting.current = false;
    return () => {
      unmounting.current = true;
    };
  }, []);
  const onCreated = useCallback((api: SceneApi) => {
    apiRef.current = api;
    const lost = (event: Event) => {
      event.preventDefault();
      if (!unmounting.current) propsRef.current.onContextLost();
    };
    const restored = () => {
      propsRef.current.onContextRestored();
      runtime.current.modelDirty = true;
      api.invalidate();
    };
    api.canvas.addEventListener("webglcontextlost", lost);
    api.canvas.addEventListener("webglcontextrestored", restored);
    // Ready once the first frame has measured the viewport (see onFrame).
    api.invalidate();
  }, []);

  const readyRef = useRef(onReadyChange);
  useEffect(() => {
    readyRef.current = onReadyChange;
  });
  useEffect(() => {
    readyRef.current(ready);
  }, [ready]);
  useEffect(
    () => () => {
      readyRef.current(false);
    },
    [],
  );

  // --- focus and announcements ----------------------------------------------------------
  const highlight = computeHighlight(graph, view);
  const summary = focusSummary(graph, view, highlight);
  const summaryId = useId();
  const helpId = useId();
  const focusValid =
    focused !== null && graph.hasNode(focused) && isNodeVisible(graph, focused, view);
  useEffect(() => {
    focusedRef.current = focusValid ? focused : null;
    invalidate();
  }, [focused, focusValid, invalidate, revision]);

  const announcement = useMemo(() => {
    if (!focusValid) return hint;
    const attributes = graph.getNodeAttributes(focused);
    let neighbours = 0;
    graph.forEachNeighbor(focused, (neighbour) => {
      if (isNodeVisible(graph, neighbour, view)) neighbours += 1;
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
      propsRef.current.onHoverNode(node);
      propsRef.current.onFocusChange(node);
      if (node === null) {
        invalidate();
        return;
      }
      // Bring the knot into view when it is near an edge or off screen.
      const r = runtime.current;
      const at = r.model?.index.get(node);
      const { width, height } = r.viewport;
      if (at !== undefined) {
        const x = r.projected[at * 3] ?? 0;
        const y = r.projected[at * 3 + 1] ?? 0;
        const behind = (r.projected[at * 3 + 2] ?? 0) <= 0;
        if (
          behind ||
          x < FOCUS_MARGIN ||
          y < FOCUS_MARGIN ||
          x > width - FOCUS_MARGIN ||
          y > height - FOCUS_MARGIN
        ) {
          const point = positionOf(node);
          if (point) animateTo(moveTarget(current(), point), ZOOM_DURATION_MS);
        }
      }
      invalidate();
    },
    [invalidate, positionOf, animateTo, current],
  );

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const action = keyAction3d(event, focusValid);
    if (!action) return;
    const r = runtime.current;
    switch (action.kind) {
      case "focusNext": {
        const model = r.model;
        if (!model) return;
        const placed = model.ids.map((id, i) => ({
          id,
          point: { x: r.projected[i * 3] ?? 0, y: r.projected[i * 3 + 1] ?? 0 },
          depth: r.projected[i * 3 + 2] ?? 0,
        }));
        const order = focusOrder3d(placed, r.viewport);
        const next = nextFocus(order, focusValid ? focused : null, action.backwards);
        if (next === null) {
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
        if (focusValid) {
          onSelectNode(focused);
          flyToNode(focused);
        }
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
    event.preventDefault();
    r.autoFrame = false;
    switch (action.kind) {
      case "orbit":
        animateTo(orbit(current(), action.azimuth, action.polar), ZOOM_DURATION_MS);
        return;
      case "pan":
        animateTo(pan(current(), action.dx, action.dy, r.viewport.height), ZOOM_DURATION_MS);
        return;
      case "zoom":
        zoomCenter(action.direction);
        return;
      case "rotate":
        turn(action.steps);
        return;
      case "reset":
        resetCamera();
        return;
    }
  };

  useImperativeHandle(
    ref,
    () => ({
      focusNode: flyToNode,
      resetCamera,
      zoomIn: () => {
        zoomCenter(1);
      },
      zoomOut: () => {
        zoomCenter(-1);
      },
      rotate: turn,
      getCamera: () => (ready ? camera3dTo2d(current(), viewportHeight()) : null),
      setCamera: (camera2d, durationMs) => {
        animateTo(camera2dTo3d(camera2d, viewportHeight()), durationMs);
        runtime.current.keepInView = camera2d;
        invalidate();
      },
      getCamera3d: () => (ready ? boundedCamera(current()) : null),
      setCamera3d: (camera3d, durationMs) => {
        animateTo(camera3d, durationMs);
      },
    }),
    [
      flyToNode,
      resetCamera,
      zoomCenter,
      turn,
      ready,
      current,
      animateTo,
      viewportHeight,
      invalidate,
    ],
  );

  const minimapSource = useMemo<Minimap3DSource>(
    () => ({
      dots: (): MinimapDot[] => {
        const model = runtime.current.model;
        if (!model) return [];
        const types = propsRef.current.palette.types;
        return model.ids.map((_id, i) => ({
          x: model.positions[i * 3] ?? 0,
          y: model.positions[i * 3 + 1] ?? 0,
          color: types[model.entities[i]?.type ?? "domain"],
        }));
      },
      camera: current,
      viewport: () => runtime.current.viewport,
      subscribe: (listener) => {
        frameListeners.current.add(listener);
        return () => {
          frameListeners.current.delete(listener);
        };
      },
      lookAt: (point) => {
        runtime.current.autoFrame = false;
        runtime.current.animation = null;
        write(lookAtOnMap(current(), point));
      },
    }),
    [current, write],
  );

  return (
    <>
      {/* role="application": a focusable widget that handles its own keys (./keyboard3d). */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        ref={containerRef}
        className={hidden ? "graph-canvas graph3d graph3d--hidden" : "graph-canvas graph3d"}
        role="application"

        tabIndex={hidden ? -1 : 0}
        aria-label="Investigation graph"
        aria-describedby={`${helpId} ${summaryId}`}
        aria-roledescription="3D graph"
        data-testid="graph-canvas"
        data-mode="3d"
        data-ready={ready ? "true" : "false"}
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
          if (!event.currentTarget.contains(event.relatedTarget) && focusedRef.current !== null) {
            moveFocus(null);
          }
        }}
      >
        <Scene3D
          stage={stage}
          colors={colors}
          referenceDepth={referenceDepth}
          onCreated={onCreated}
          onFrame={onFrame}
        />
      </div>
      <div ref={labelsRef} className="graph3d-labels" aria-hidden="true" hidden={hidden} />
      <div ref={rulerRef} className="graph3d-ruler" aria-hidden="true" hidden={hidden}>
        {ticks.map((tick, n) => (
          <span
            key={`${tick.label}-${String(n)}`}
            className="graph3d-ruler__mark"
            ref={(element) => {
              if (element) rulerPool.current[n] = element;
            }}
            hidden
          >
            {tick.label}
          </span>
        ))}
      </div>
      <div ref={ringRef} className="graph-focus-ring" hidden aria-hidden="true" />
      <div ref={selectionRef} className="graph-selection" hidden aria-hidden="true" />
      {showMinimap && ready && !hidden && <Minimap3D source={minimapSource} palette={palette} />}
      <p id={helpId} className="visually-hidden">
        {GRAPH_HELP_3D}
      </p>
      <p id={summaryId} className="visually-hidden" aria-live="polite">
        {summary}
      </p>
      <p className="visually-hidden" aria-live="polite" data-testid="graph-announcer">
        {announcement}
      </p>
    </>
  );
}

export default GraphCanvas3D;
