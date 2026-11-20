/**
 * Stand-in for sigma in jsdom (which has no WebGL). It records what the
 * component asks of it and lets tests fire sigma events and run the reducers.
 *
 * Coordinates: a 800 × 600 viewport whose camera maps graph (x, y) to the
 * screen like sigma does (y up), with the camera's x / y as the graph point
 * at the center, `ratio` as graph units per pixel and `angle` as rotation.
 */
import { vi } from "vitest";

type Handler = (payload: Record<string, unknown>) => void;
type Reducer = (key: string) => Record<string, unknown>;

interface Options {
  primitives?: unknown;
  styles?: unknown;
  nodeReducer?: Reducer;
  edgeReducer?: Reducer;
  settings?: Record<string, unknown>;
}

interface Point {
  x: number;
  y: number;
}

interface CameraState extends Point {
  ratio: number;
  angle: number;
}

interface FakeGraph {
  nodes: () => string[];
  edges: () => string[];
  hasNode: (node: string) => boolean;
  getNodeAttributes: (node: string) => Record<string, unknown>;
}

export const VIEWPORT = { width: 800, height: 600 };
const INITIAL: CameraState = { x: 0, y: 0, ratio: 1, angle: 0 };

type CameraListener = (state: CameraState) => void;

export class FakeCamera {
  private state: CameraState = { ...INITIAL };
  private readonly listeners = new Set<CameraListener>();
  minRatio = 0.08;
  maxRatio = 4;

  get ratio() {
    return this.state.ratio;
  }
  get angle() {
    return this.state.angle;
  }
  get x() {
    return this.state.x;
  }
  get y() {
    return this.state.y;
  }

  getState(): CameraState {
    return { ...this.state };
  }

  setState = vi.fn((next: Partial<CameraState>) => {
    this.state = { ...this.state, ...next };
    for (const listener of this.listeners) listener(this.getState());
    return this;
  });

  /** Jumps straight to the end state (tests do not wait for animations). */
  animate = vi.fn((next: Partial<CameraState>) => {
    this.setState(next);
    return Promise.resolve();
  });

  reset = vi.fn(() => {
    this.setState({ ...INITIAL });
    return Promise.resolve();
  });

  zoomIn = vi.fn(() => Promise.resolve());
  zoomOut = vi.fn(() => Promise.resolve());

  getBoundedRatio(ratio: number): number {
    return Math.min(Math.max(ratio, this.minRatio), this.maxRatio);
  }

  on(event: string, listener: CameraListener) {
    if (event === "updated") this.listeners.add(listener);
    return this;
  }

  off(event: string, listener: CameraListener) {
    if (event === "updated") this.listeners.delete(listener);
    return this;
  }
}

/** An event payload like sigma's: viewport coordinates, the DOM event, preventSigmaDefault. */
export function sigmaEvent(
  point: Point = { x: 0, y: 0 },
  original: MouseEvent | TouchEvent = new MouseEvent("mousedown", { button: 0 }),
) {
  const event = {
    ...point,
    original,
    sigmaDefaultPrevented: false,
    preventSigmaDefault() {
      event.sigmaDefaultPrevented = true;
    },
  };
  return event;
}

export class FakeSigma {
  static instances: FakeSigma[] = [];

  readonly handlers = new Map<string, Handler[]>();
  readonly camera = new FakeCamera();
  readonly refresh = vi.fn(() => this);
  readonly kill = vi.fn();
  readonly setSetting = vi.fn(() => this);
  readonly setCustomBBox = vi.fn((bbox: unknown) => {
    this.customBBox = bbox;
    return this;
  });
  readonly graph: FakeGraph;
  readonly container: HTMLElement;
  readonly options: Options;
  private customBBox: unknown = null;

  constructor(graph: FakeGraph, container: HTMLElement, options: Options = {}) {
    this.graph = graph;
    this.container = container;
    this.options = options;
    FakeSigma.instances.push(this);
  }

  on(event: string, handler: Handler): this {
    this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler]);
    return this;
  }

  off(event: string, handler: Handler): this {
    this.handlers.set(
      event,
      (this.handlers.get(event) ?? []).filter((existing) => existing !== handler),
    );
    return this;
  }

  /** Fires a sigma event; `event` defaults to a left-button mouse event at (0, 0). */
  emit(name: string, payload: Record<string, unknown> = {}): void {
    const event = (payload["event"] ?? sigmaEvent()) as ReturnType<typeof sigmaEvent>;
    const withPrevent = {
      ...payload,
      event,
      preventSigmaDefault: () => {
        event.preventSigmaDefault();
      },
    };
    for (const handler of this.handlers.get(name) ?? []) handler(withPrevent);
  }

  /** Moves the camera to `ratio`, as a zoom would. */
  zoomTo(ratio: number): void {
    this.camera.setState({ ratio });
  }

  getCamera() {
    return this.camera;
  }

  getGraph() {
    return this.graph;
  }

  getContainer() {
    return this.container;
  }

  getDimensions() {
    return { ...VIEWPORT };
  }

  getSetting(key: string) {
    return this.options.settings?.[key];
  }

  getBBox() {
    return { x: [-100, 100], y: [-100, 100] };
  }

  getCustomBBox() {
    return this.customBBox;
  }

  // Framed graph coordinates are graph coordinates here.
  graphToViewport({ x, y }: Point): Point {
    const { x: cx, y: cy, ratio, angle } = this.camera.getState();
    const dx = (x - cx) / ratio;
    const dy = -(y - cy) / ratio;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    return {
      x: VIEWPORT.width / 2 + dx * cos - dy * sin,
      y: VIEWPORT.height / 2 + dx * sin + dy * cos,
    };
  }

  viewportToGraph({ x, y }: Point): Point {
    const { x: cx, y: cy, ratio, angle } = this.camera.getState();
    const dx = x - VIEWPORT.width / 2;
    const dy = y - VIEWPORT.height / 2;
    const cos = Math.cos(-angle);
    const sin = Math.sin(-angle);
    const rx = dx * cos - dy * sin;
    const ry = dx * sin + dy * cos;
    return { x: cx + rx * ratio, y: cy - ry * ratio };
  }

  viewportToFramedGraph(point: Point): Point {
    return this.viewportToGraph(point);
  }

  getViewportZoomedState(at: Point, ratio: number): CameraState {
    const state = this.camera.getState();
    const before = this.viewportToGraph(at);
    const scale = ratio / state.ratio;
    return {
      ...state,
      ratio,
      x: before.x + (state.x - before.x) * scale,
      y: before.y + (state.y - before.y) * scale,
    };
  }

  getNodeDisplayData(node: string) {
    return this.graph.nodes().includes(node) ? { x: 0.25, y: 0.75, size: 8 } : undefined;
  }

  /** What the node reducer currently returns for `node`. */
  nodeView(node: string): Record<string, unknown> {
    return this.options.nodeReducer?.(node) ?? {};
  }

  edgeView(edge: string): Record<string, unknown> {
    return this.options.edgeReducer?.(edge) ?? {};
  }

  /** Where a node is on the (fake) screen. */
  screenOf(node: string): Point {
    const { x, y } = this.graph.getNodeAttributes(node) as { x: number; y: number };
    return this.graphToViewport({ x, y });
  }

  static latest(): FakeSigma {
    const instance = FakeSigma.instances.at(-1);
    if (!instance) throw new Error("sigma was never instantiated");
    return instance;
  }

  static reset(): void {
    FakeSigma.instances = [];
  }
}

export default FakeSigma;
