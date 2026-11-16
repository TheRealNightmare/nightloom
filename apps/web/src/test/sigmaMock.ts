/**
 * Stand-in for sigma in jsdom (which has no WebGL). It records what the
 * component asks of it and lets tests fire sigma events and run the reducers.
 */
import { vi } from "vitest";

type Handler = (payload: { node?: string }) => void;
type Reducer = (key: string) => Record<string, unknown>;

interface Options {
  nodeReducer?: Reducer;
  edgeReducer?: Reducer;
  settings?: Record<string, unknown>;
}

export class FakeSigma {
  static instances: FakeSigma[] = [];

  readonly handlers = new Map<string, Handler[]>();
  readonly camera = {
    animate: vi.fn(() => Promise.resolve()),
    reset: vi.fn(() => Promise.resolve()),
  };
  readonly refresh = vi.fn(() => this);
  readonly kill = vi.fn();
  readonly graph: { nodes: () => string[]; edges: () => string[] };
  readonly options: Options;

  constructor(
    graph: { nodes: () => string[]; edges: () => string[] },
    _container: HTMLElement,
    options: Options = {},
  ) {
    this.graph = graph;
    this.options = options;
    FakeSigma.instances.push(this);
  }

  on(event: string, handler: Handler): this {
    this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler]);
    return this;
  }

  emit(event: string, payload: { node?: string } = {}): void {
    for (const handler of this.handlers.get(event) ?? []) handler(payload);
  }

  getCamera() {
    return this.camera;
  }

  getNodeDisplayData(node: string) {
    return this.graph.nodes().includes(node) ? { x: 0.25, y: 0.75 } : undefined;
  }

  /** What the node reducer currently returns for `node`. */
  nodeView(node: string): Record<string, unknown> {
    return this.options.nodeReducer?.(node) ?? {};
  }

  edgeView(edge: string): Record<string, unknown> {
    return this.options.edgeReducer?.(edge) ?? {};
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
