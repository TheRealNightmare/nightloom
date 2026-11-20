import { useCallback, useEffect, useRef, useState } from "react";

import type { NightloomGraph } from "./buildGraph";
import {
  applyPins,
  LayoutSaver,
  PinStore,
  SAVE_BACKOFF_MS,
  type Positions,
  type SaveOptions,
  type SaveStatus,
} from "./pins";

/** Where the user's pins are kept between visits (the case Graph tab saves them per user). */
export interface LayoutPersistence {
  load: (signal: AbortSignal) => Promise<Positions>;
  save: (positions: Positions, options: SaveOptions) => Promise<void>;
  /** Errors that retrying cannot fix (signed out, no longer a member…). */
  isPermanent?: (error: unknown) => boolean;
  retryAfterSeconds?: (error: unknown) => number | null;
}

export interface Pins {
  store: PinStore;
  /** Changes whenever the pins do. */
  version: number;
  saveStatus: SaveStatus;
  isPinned: (id: string) => boolean;
  /** Pins nodes where they are now. */
  pin: (ids: Iterable<string>) => void;
  unpin: (ids: Iterable<string>) => void;
  toggle: (id: string) => void;
  unpinAll: () => void;
}

/**
 * The user's pins for one graph: kept in a {@link PinStore}, mirrored onto
 * the graph's attributes, and (with `persistence`) loaded once and saved
 * after every change (debounced, retried, flushed when the page is hidden).
 * `onReleased` gets the nodes that were unpinned, for the layout to take back.
 */
export function usePins(
  graph: NightloomGraph,
  persistence: LayoutPersistence | undefined,
  onReleased: (nodes: string[]) => void,
): Pins {
  const [store] = useState(() => {
    const created = new PinStore();
    if (!persistence) created.loaded = true;
    return created;
  });
  const [version, setVersion] = useState(0);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const saverRef = useRef<LayoutSaver | null>(null);
  // What the server has (as JSON): a change that brings the pins back to it is not saved.
  const savedRef = useRef<string | null>(null);
  const releasedRef = useRef(onReleased);
  useEffect(() => {
    releasedRef.current = onReleased;
  });

  useEffect(() => {
    if (!persistence) return;
    const saver = new LayoutSaver({
      save: async (positions, options) => {
        await persistence.save(positions, options);
        savedRef.current = JSON.stringify(positions);
      },
      onStatus: setSaveStatus,
      ...(persistence.isPermanent ? { isPermanent: persistence.isPermanent } : {}),
      ...(persistence.retryAfterSeconds
        ? { retryAfterSeconds: persistence.retryAfterSeconds }
        : {}),
    });
    saverRef.current = saver;
    const flush = () => {
      saver.flush({ keepalive: true });
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flush();
    };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibility);

    // Load the saved pins, trying again (with a growing delay) if that fails.
    const controller = new AbortController();
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = () => {
      persistence.load(controller.signal).then(
        (positions) => {
          if (controller.signal.aborted) return;
          savedRef.current = JSON.stringify(positions);
          store.load(positions);
        },
        (error: unknown) => {
          if (controller.signal.aborted || persistence.isPermanent?.(error)) return;
          const delay = SAVE_BACKOFF_MS[Math.min(attempt, SAVE_BACKOFF_MS.length - 1)];
          attempt += 1;
          timer = setTimeout(load, delay);
        },
      );
    };
    load();

    return () => {
      controller.abort();
      if (timer !== null) clearTimeout(timer);
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
      // Leaving the case: what is still waiting is sent now.
      flush();
      saver.dispose();
      saverRef.current = null;
    };
  }, [persistence, store]);

  useEffect(
    () =>
      store.subscribe(() => {
        const released = applyPins(graph, store);
        setVersion((n) => n + 1);
        if (released.length > 0) releasedRef.current(released);
        const saver = saverRef.current;
        if (!saver || !store.loaded) return;
        const positions = store.saved();
        if (JSON.stringify(positions) === savedRef.current) return;
        saver.request(positions);
      }),
    [graph, store],
  );

  const pin = useCallback(
    (ids: Iterable<string>) => {
      const entries: [string, readonly [number, number]][] = [];
      for (const id of ids) {
        if (!graph.hasNode(id)) continue;
        const { x, y } = graph.getNodeAttributes(id);
        entries.push([id, [x, y]]);
      }
      store.pin(entries);
    },
    [graph, store],
  );
  const unpin = useCallback(
    (ids: Iterable<string>) => {
      store.unpin(ids);
    },
    [store],
  );
  const toggle = useCallback(
    (id: string) => {
      if (store.has(id)) store.unpin([id]);
      else pin([id]);
    },
    [store, pin],
  );
  const unpinAll = useCallback(() => {
    store.clear();
  }, [store]);
  const isPinned = useCallback((id: string) => store.has(id), [store]);

  return { store, version, saveStatus, isPinned, pin, unpin, toggle, unpinAll };
}
