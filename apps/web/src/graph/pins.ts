/**
 * Pinned nodes: where the user put them, kept in place by the layout, and
 * saved as the user's personal layout of the case.
 *
 * {@link PinStore} is the source of truth (entity id → [x, y]); the graph's
 * `pinned` and `fixed` attributes mirror it for the nodes that are drawn
 * (see {@link applyPins}). Pins of entities that are not drawn right now
 * (filtered out, or inside a collapsed group) stay in the store and are
 * saved with the rest. Pins of group nodes are kept for the session only:
 * a group is not an entity, so it has no key to save it under.
 *
 * {@link LayoutSaver} sends the latest pins after a short pause (debounced),
 * right away when the page is hidden, and again with a growing delay after a
 * failure. A retry always sends the newest pins, never an older snapshot.
 */
import type { NightloomGraph } from "./buildGraph";
import { isGroupId } from "./groups";

export type Position = readonly [number, number];
/** What is saved: entity key → [x, y] in graph coordinates. */
export type Positions = Record<string, [number, number]>;

/** Whether a pin of `id` is saved (entities only, not group nodes). */
export function isSavedPin(id: string): boolean {
  return !isGroupId(id);
}

/**
 * The pins after the saved layout arrives: the saved ones, except where the
 * user already pinned or unpinned something in this session before it
 * arrived (`touched`), or unpinned everything (`cleared`).
 */
export function mergeLoadedPins(
  loaded: Readonly<Positions>,
  local: ReadonlyMap<string, Position>,
  touched: ReadonlySet<string>,
  cleared: boolean,
): Map<string, Position> {
  const result = new Map<string, Position>();
  if (!cleared) {
    for (const [key, xy] of Object.entries(loaded)) {
      if (!touched.has(key) && isFinitePosition(xy)) result.set(key, [xy[0], xy[1]]);
    }
  }
  for (const [key, xy] of local) result.set(key, xy);
  return result;
}

function isFinitePosition(value: unknown): value is Position {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    value.every((n) => typeof n === "number" && Number.isFinite(n))
  );
}

export class PinStore {
  private pins = new Map<string, Position>();
  private touched = new Set<string>();
  private cleared = false;
  private listeners = new Set<() => void>();
  /** Whether the saved layout has arrived (until then, changes are merged with it). */
  loaded = false;

  /** Replaces nothing the user changed meanwhile (see {@link mergeLoadedPins}). */
  load(saved: Readonly<Positions>): void {
    this.pins = mergeLoadedPins(saved, this.pins, this.touched, this.cleared);
    this.loaded = true;
    this.touched.clear();
    this.cleared = false;
    this.emit();
  }

  has(id: string): boolean {
    return this.pins.has(id);
  }

  get(id: string): Position | undefined {
    return this.pins.get(id);
  }

  get size(): number {
    return this.pins.size;
  }

  ids(): string[] {
    return [...this.pins.keys()];
  }

  pin(entries: Iterable<readonly [string, Position]>): void {
    let changed = false;
    for (const [id, xy] of entries) {
      const current = this.pins.get(id);
      if (current?.[0] === xy[0] && current[1] === xy[1]) continue;
      this.pins.set(id, [xy[0], xy[1]]);
      if (!this.loaded) this.touched.add(id);
      changed = true;
    }
    if (changed) this.emit();
  }

  unpin(ids: Iterable<string>): void {
    let changed = false;
    for (const id of ids) {
      if (!this.loaded) this.touched.add(id);
      if (this.pins.delete(id)) changed = true;
    }
    if (changed || !this.loaded) this.emit();
  }

  clear(): void {
    if (!this.loaded) this.cleared = true;
    this.pins.clear();
    this.emit();
  }

  /**
   * Replaces every pin with `positions` (applying a saved view). Before the
   * saved layout arrives, it is discarded when it does, as after {@link clear}.
   */
  replace(positions: Readonly<Positions>): void {
    if (!this.loaded) {
      this.cleared = true;
      this.touched.clear();
    }
    this.pins = new Map();
    for (const [id, xy] of Object.entries(positions)) {
      if (isFinitePosition(xy)) this.pins.set(id, [xy[0], xy[1]]);
    }
    this.emit();
  }

  /** The pins to save (entities only). */
  saved(): Positions {
    const result: Positions = {};
    for (const [id, [x, y]] of this.pins) if (isSavedPin(id)) result[id] = [x, y];
    return result;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }
}

/**
 * Brings the graph's `pinned` / `fixed` attributes and the pinned nodes'
 * positions in line with `store`. Returns the nodes that were pinned and no
 * longer are (to let the layout take them back).
 */
export function applyPins(graph: NightloomGraph, store: PinStore): string[] {
  const released: string[] = [];
  graph.forEachNode((node, attributes) => {
    const xy = store.get(node);
    if (xy) {
      if (
        attributes.pinned === true &&
        attributes.fixed === true &&
        attributes.x === xy[0] &&
        attributes.y === xy[1]
      ) {
        return;
      }
      graph.mergeNodeAttributes(node, { x: xy[0], y: xy[1], pinned: true, fixed: true });
    } else if (attributes.pinned === true) {
      graph.removeNodeAttribute(node, "pinned");
      graph.removeNodeAttribute(node, "fixed");
      released.push(node);
    }
  });
  return released;
}

// --- saving -----------------------------------------------------------------------

export type SaveStatus = "idle" | "pending" | "saving" | "retrying" | "failed";

export interface SaveOptions {
  /** The page is going away: the request must outlive it. */
  keepalive: boolean;
}

export interface LayoutSaverOptions {
  save: (positions: Positions, options: SaveOptions) => Promise<void>;
  onStatus?: (status: SaveStatus) => void;
  /** Errors that retrying cannot fix (signed out, no longer a member…). */
  isPermanent?: (error: unknown) => boolean;
  /** Seconds the server asked to wait before trying again (a 429), if any. */
  retryAfterSeconds?: (error: unknown) => number | null;
  debounceMs?: number;
  /** Delays before each retry; the last one repeats. */
  backoffMs?: readonly number[];
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export const SAVE_DEBOUNCE_MS = 800;
export const SAVE_BACKOFF_MS = [1_000, 2_000, 5_000, 10_000, 30_000] as const;

export class LayoutSaver {
  private latest: Positions | null = null;
  private inFlight = false;
  private timer: unknown = null;
  private failures = 0;
  private disposed = false;
  private current: SaveStatus = "idle";
  private readonly options: Required<LayoutSaverOptions>;

  constructor(options: LayoutSaverOptions) {
    this.options = {
      onStatus: () => undefined,
      isPermanent: () => false,
      retryAfterSeconds: () => null,
      debounceMs: SAVE_DEBOUNCE_MS,
      backoffMs: SAVE_BACKOFF_MS,
      setTimer: (callback, ms) => setTimeout(callback, ms),
      clearTimer: (handle) => {
        clearTimeout(handle as ReturnType<typeof setTimeout>);
      },
      ...options,
    };
  }

  get status(): SaveStatus {
    return this.current;
  }

  /** Saves `positions` after the debounce delay (a newer request replaces it). */
  request(positions: Positions): void {
    if (this.disposed) return;
    this.latest = positions;
    // While a retry is scheduled, the newer pins go with it.
    if (this.current === "retrying") return;
    this.setStatus("pending");
    this.schedule(this.options.debounceMs);
  }

  /** Sends what is waiting right away (the page is being hidden). */
  flush(options: SaveOptions = { keepalive: true }): void {
    if (this.disposed || this.latest === null) return;
    this.cancelTimer();
    void this.send(options);
  }

  dispose(): void {
    this.cancelTimer();
    this.disposed = true;
  }

  // Methods, not fields, after an await: what another call changed meanwhile.
  private isDisposed(): boolean {
    return this.disposed;
  }

  private hasWaiting(): boolean {
    return this.latest !== null;
  }

  private setStatus(status: SaveStatus): void {
    if (this.current === status) return;
    this.current = status;
    this.options.onStatus(status);
  }

  private cancelTimer(): void {
    if (this.timer !== null) this.options.clearTimer(this.timer);
    this.timer = null;
  }

  private schedule(ms: number): void {
    this.cancelTimer();
    this.timer = this.options.setTimer(() => {
      this.timer = null;
      void this.send({ keepalive: false });
    }, ms);
  }

  private async send(options: SaveOptions): Promise<void> {
    const positions = this.latest;
    if (positions === null || this.disposed) return;
    // One request at a time; whatever arrives meanwhile goes next. A page
    // hide does not wait: its keepalive request must start now.
    if (this.inFlight && !options.keepalive) return;
    this.latest = null;
    this.inFlight = true;
    if (this.current !== "retrying") this.setStatus("saving");
    try {
      await this.options.save(positions, options);
      this.inFlight = false;
      this.failures = 0;
      if (this.isDisposed()) return;
      if (this.hasWaiting()) {
        this.setStatus("pending");
        this.schedule(this.options.debounceMs);
      } else {
        this.setStatus("idle");
      }
    } catch (error) {
      this.inFlight = false;
      if (this.isDisposed()) return;
      // Retry with the newest pins: these, unless newer ones came meanwhile.
      this.latest ??= positions;
      if (this.options.isPermanent(error)) {
        this.setStatus("failed");
        return;
      }
      const { backoffMs } = this.options;
      const delay = backoffMs[Math.min(this.failures, backoffMs.length - 1)] ?? 1000;
      this.failures += 1;
      const hint = this.options.retryAfterSeconds(error);
      this.setStatus("retrying");
      this.schedule(typeof hint === "number" && hint > 0 ? Math.max(delay, hint * 1000) : delay);
    }
  }
}
