import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from "react";

import { FiltersPanel, type GradeOption } from "../components/FiltersPanel";
import { Modal } from "../components/Modal";
import { Legend } from "../components/Legend";
import { SearchBox } from "../components/SearchBox";
import { SidePanel, type GraphSummary } from "../components/SidePanel";
import type { EntityNode, EntityType, InvestigationGraph } from "../domain/entities";
import { useGraphExplorer, type HiddenTypesStore } from "../hooks/useGraphExplorer";
import { useTheme } from "../theme/context";
import { GRAPH_PALETTES } from "../theme/graphPalette";
import { createGraph, recolorGraph, syncGraph } from "./buildGraph";
import {
  applyFilters,
  countActiveFilters,
  EMPTY_FILTERS,
  filterSummary,
  type GraphFilters,
} from "./filters";
import { GraphCanvas, type GraphCanvasHandle } from "./GraphCanvas";
import {
  collapseGroups,
  computeGroups,
  groupContaining,
  groupLabel,
  isGroupId,
  type LeafGroup,
} from "./groups";
import {
  frameSchedule,
  fullLayoutIterations,
  GLIDE_ITERATIONS_PER_FRAME,
  INCREMENTAL_LAYOUT_ITERATIONS,
  startLayout,
  type LayoutRun,
} from "./layout";
import type { Point, Rect } from "./geometry";
import { SHORTCUTS, type ShortcutGroup } from "./keyboard";
import {
  DEFAULT_DEPTH,
  DEPTH_MODES,
  initialMode,
  storeMode,
  webglSupported,
  type Camera3D,
  type DepthMode,
  type ViewMode,
} from "./mode";
import { MINIMAP_DEFAULT_THRESHOLD } from "./minimap";
import { NodeContextMenu } from "./NodeContextMenu";
import { applyPins } from "./pins";
import {
  storedMinimap,
  storedWheelSetting,
  storeMinimap,
  storeWheelSetting,
  type MinimapPreference,
} from "./preferences";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { usePins, type LayoutPersistence } from "./usePins";
import { useNow, type FiltersUpdate } from "./useUrlFilters";
import { connectionsOf, countByType, isNodeVisible } from "./view";
import { SavedViewContext, type SavedViewControls } from "./savedViewContext";
import { ViewMenu } from "./ViewMenu";
import type { CameraSnapshot, ViewSnapshot } from "./viewSnapshot";

export type { SavedViewControls } from "./savedViewContext";
import type { GraphCanvas3DHandle } from "./three/GraphCanvas3D";
import type { WheelSetting } from "./wheel";

// three.js and the 3D view load only once someone switches to 3D.
const GraphCanvas3D = lazy(() => import("./three/GraphCanvas3D"));

export const NO_WEBGL = "3D needs WebGL, which isn’t available in this browser — showing 2D.";
export const WEBGL_LOST = "The 3D view lost its WebGL context — showing 2D until it is restored.";

/** Where a view should look once it is drawn (a saved view's camera animates there). */
interface PendingCamera {
  camera: CameraSnapshot | null;
  camera3d: Camera3D | null;
  animate?: boolean;
}

/** Either canvas: the 3D one also has a 3D camera. */
type CanvasHandle = GraphCanvasHandle &
  Partial<Pick<GraphCanvas3DHandle, "getCamera3d" | "setCamera3d">>;

/** Falls back to 2D when the 3D view cannot start (no WebGL context, a failed shader). */
class Graph3DBoundary extends Component<
  { onError: () => void; children: ReactNode },
  { failed: boolean }
> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override componentDidCatch() {
    this.props.onError();
  }
  override render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** How long the camera takes to reach a saved view (at once with reduced motion). */
export const VIEW_CAMERA_MS = 400;

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

/** Up to this many nodes, every node is labelled. */
const SMALL_GRAPH = 200;

/** Ask the view to select a node and move the camera to it (a new object each time). */
export interface FocusRequest {
  id: string;
}

export interface GraphViewHandle {
  /** Selects `id` and moves the camera to it (expanding its group if it is in one). */
  focusNode: (id: string) => void;
}

export interface GraphViewProps {
  data: InvestigationGraph;
  /** The side panel's content when nothing is selected. */
  overview: (summary: GraphSummary) => ReactNode;
  /** The selected entity's details in the side panel (default: id, properties, provenance). */
  renderEntity?: (entity: EntityNode) => ReactNode;
  /** More side panel content while several entities are selected (e.g. running a profile). */
  renderSelection?: (entities: readonly EntityNode[]) => ReactNode;
  /** Extra content at the end of the toolbar (e.g. a summary and the case's actions). */
  toolbar?: ReactNode;
  /** Shown over the canvas's top-right corner (e.g. the running job). */
  overlay?: ReactNode;
  /** Applied once the node exists in the graph. */
  focus?: FocusRequest | null;
  onSelectionChange?: (id: string | null) => void;
  /** Nodes to emphasize for a moment (e.g. what a Shuttle job just found). */
  pulse?: ReadonlySet<string>;
  /**
   * Filters kept by the caller (the case Graph tab keeps them in the URL).
   * Without them, the view keeps its own.
   */
  filters?: GraphFilters;
  onFiltersChange?: FiltersUpdate;
  /** Admiralty grades and their meanings, for the filter choices. */
  grades?: { reliability: readonly GradeOption[]; credibility: readonly GradeOption[] };
  /** Extra class names for the outer element (e.g. to embed it in a page). */
  className?: string;
  /**
   * Where the user's pins are saved (the case Graph tab: per user and case).
   * Without it, pins last as long as the page.
   */
  layout?: LayoutPersistence;
  /**
   * A saved-views control for the toolbar (the case Graph tab's Views menu);
   * it reads the view's controls with `useSavedViewControls` (./savedViewContext).
   */
  savedViews?: ReactNode;
  /**
   * 2D or 3D, kept by the caller (the case Graph tab keeps it in the URL).
   * Without it, the view starts from `?mode=` or the remembered choice.
   */
  mode?: ViewMode;
  onModeChange?: (mode: ViewMode) => void;
  ref?: Ref<GraphViewHandle>;
}

/** A node's menu, open at a point in the canvas. */
interface MenuState {
  node: string;
  at: Point;
  bounds: { width: number; height: number };
}

const NO_SELECTION: ReadonlySet<string> = new Set();

/**
 * The interactive graph: canvas, search, legend, filters and side panel,
 * shared by the demo and the case Graph tab. It keeps one graphology graph
 * for its whole life and syncs into it what the filters leave, with large
 * groups of leaves collapsed into one node each, so adding an entity places
 * one node next to its neighbours and runs a short layout instead of
 * rebuilding everything.
 */
export function GraphView({
  data,
  overview,
  renderEntity,
  renderSelection,
  toolbar,
  overlay,
  focus = null,
  onSelectionChange,
  pulse,
  filters: controlledFilters,
  onFiltersChange,
  grades,
  className,
  layout,
  savedViews,
  mode: controlledMode,
  onModeChange,
  ref,
}: GraphViewProps) {
  const [graph] = useState(createGraph);
  const { theme } = useTheme();
  const palette = GRAPH_PALETTES[theme];
  const layoutRef = useRef<LayoutRun | null>(null);
  const canvas2dRef = useRef<GraphCanvasHandle>(null);
  const canvas3dRef = useRef<GraphCanvas3DHandle>(null);

  // --- 2D / 3D --------------------------------------------------------------------------
  const [ownMode, setOwnMode] = useState<ViewMode>(() => initialMode(window.location.search));
  const mode = controlledMode ?? ownMode;
  const [depth, setDepth] = useState<DepthMode>(DEFAULT_DEPTH);
  const [webgl, setWebgl] = useState<"ok" | "unavailable" | "lost">("ok");
  const supported = useMemo(() => mode === "2d" || webglSupported(), [mode]);
  const show3d = mode === "3d" && supported && webgl !== "unavailable";
  // While the context is lost, 2D is shown and the 3D view waits (mounted) for it to come back.
  const active3d = show3d && webgl === "ok";
  const active3dRef = useRef(active3d);
  useLayoutEffect(() => {
    active3dRef.current = active3d;
  }, [active3d]);
  const canvasRef = useMemo(
    () => ({
      get current(): CanvasHandle | null {
        return active3dRef.current ? canvas3dRef.current : canvas2dRef.current;
      },
    }),
    [],
  );
  // The node with the keyboard focus ring, kept across a switch of mode.
  const [lastFocus, setLastFocus] = useState<string | null>(null);
  const [shortcuts3d, setShortcuts3d] = useState<readonly ShortcutGroup[] | null>(null);
  useEffect(() => {
    if (!show3d || shortcuts3d) return;
    void import("./three/keyboard3d").then((module) => {
      setShortcuts3d(module.SHORTCUTS_3D);
    });
  }, [show3d, shortcuts3d]);
  // What the last layout decision saw; anything else in the graph is new.
  const seenRef = useRef<{ nodes: Set<string>; edges: Set<string> }>({
    nodes: new Set(),
    edges: new Set(),
  });

  // --- filters and groups -----------------------------------------------------------
  const [ownFilters, setOwnFilters] = useState<GraphFilters>(EMPTY_FILTERS);
  const filters = controlledFilters ?? ownFilters;
  const updateFilters = useCallback<FiltersUpdate>(
    (change) => {
      if (onFiltersChange) onFiltersChange(change);
      else setOwnFilters(change);
    },
    [onFiltersChange],
  );
  const [filtersOpen, setFiltersOpen] = useState(false);
  const now = useNow(filters.seen.kind === "preset");
  const visible = useMemo(() => applyFilters(data, filters, { now }), [data, filters, now]);
  const groups = useMemo(() => computeGroups(data, visible), [data, visible]);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const collapsed = useMemo(
    () => groups.filter((group) => !expanded.has(group.id)),
    [groups, expanded],
  );
  const display = useMemo(() => collapseGroups(data, collapsed), [data, collapsed]);
  const activeFilters = countActiveFilters(filters);
  const summaryText = filterSummary(visible.size, data.nodes.length, activeFilters);

  const setGroupExpanded = useCallback((id: string, open: boolean) => {
    setExpanded((current) => {
      if (current.has(id) === open) return current;
      const next = new Set(current);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  // --- pins ---------------------------------------------------------------------------
  // Unpinned nodes rejoin the layout: a short run moves only them, a couple
  // of iterations per frame, so they glide back instead of jumping.
  const rejoin = useCallback(
    (released: string[]) => {
      const running = layoutRef.current;
      // A layout still running takes them along (they are no longer fixed).
      if (running && !running.finished) return;
      const moving = new Set(released.filter((node) => graph.hasNode(node)));
      if (moving.size === 0) return;
      layoutRef.current = startLayout(graph, {
        iterations: INCREMENTAL_LAYOUT_ITERATIONS,
        fixed: new Set(graph.filterNodes((node) => !moving.has(node))),
        maxPerSlice: GLIDE_ITERATIONS_PER_FRAME,
        schedule: frameSchedule,
      });
    },
    [graph],
  );
  const pins = usePins(graph, layout, rejoin);
  const pinStore = pins.store;

  // The graph is an external, mutable store: bring it in line with what is
  // displayed (cheap, incremental) and keep a snapshot that changes whenever it does.
  const snapshot = useMemo(() => {
    syncGraph(graph, display, palette);
    // Entities that just appeared (an expanded group, new data) go where they were pinned.
    applyPins(graph, pinStore);
    return { order: graph.order, size: graph.size };
    // A theme change only repaints (below); it does not resync.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, display]);
  // The theme changed: repaint what is there (colors only), then let the canvas refresh.
  const painted = useMemo(() => {
    recolorGraph(graph, palette);
    return palette;
  }, [graph, palette]);

  useEffect(() => {
    if (snapshot.order === 0) return;
    const seen = seenRef.current;
    const newNodes = graph.filterNodes((node) => !seen.nodes.has(node));
    const newEdges = graph.filterEdges((edge) => !seen.edges.has(edge));
    seenRef.current = { nodes: new Set(graph.nodes()), edges: new Set(graph.edges()) };
    if (newNodes.length === 0 && newEdges.length === 0) return;

    const running = layoutRef.current;
    if (seen.nodes.size === 0) {
      running?.cancel();
      layoutRef.current = startLayout(graph, { iterations: fullLayoutIterations(graph.order) });
      return;
    }
    // A layout still settling the graph takes the new items along with it.
    if (running && !running.finished) return;
    // New nodes settle next to their neighbours; one without any keeps its
    // place just outside the graph (repulsion alone would push it far away).
    const moving = new Set(newNodes.filter((node) => graph.degree(node) > 0));
    for (const edge of newEdges) {
      moving.add(graph.source(edge));
      moving.add(graph.target(edge));
    }
    if (moving.size === 0) return;
    layoutRef.current = startLayout(graph, {
      iterations: INCREMENTAL_LAYOUT_ITERATIONS,
      fixed: new Set(graph.filterNodes((node) => !moving.has(node))),
    });
  }, [graph, snapshot]);

  useEffect(
    () => () => {
      layoutRef.current?.cancel();
      layoutRef.current = null;
      // A remount (StrictMode) lays the graph out again from scratch.
      seenRef.current = { nodes: new Set(), edges: new Set() };
    },
    [],
  );

  // --- interaction --------------------------------------------------------------------
  const typesStore = useMemo<HiddenTypesStore>(
    () => ({
      hidden: filters.hiddenTypes,
      setHidden: (hiddenTypes: ReadonlySet<EntityType>) => {
        updateFilters((current) => ({ ...current, hiddenTypes }));
      },
    }),
    [filters.hiddenTypes, updateFilters],
  );
  const explorer = useGraphExplorer(graph, typesStore);
  const { selectNode: selectOne, resetView } = explorer;
  // A multi-selection (two or more nodes); a single selection is the explorer's.
  const [multi, setMulti] = useState<ReadonlySet<string>>(NO_SELECTION);
  const selectNode = useCallback(
    (node: string | null) => {
      setMulti(NO_SELECTION);
      selectOne(node);
    },
    [selectOne],
  );
  // Entities the filters hide (group nodes stand for visible entities).
  const hiddenNodes = useMemo(
    () =>
      new Set(
        display.nodes.filter((node) => !node.group && !visible.has(node.id)).map((n) => n.id),
      ),
    [display, visible],
  );
  const view = useMemo(
    () => ({ ...explorer.view, hiddenNodes, selectedNodes: multi }),
    [explorer.view, hiddenNodes, multi],
  );
  // The multi-selection, without what is no longer drawn.
  const selection = useMemo(
    () => [...multi].filter((node) => graph.hasNode(node) && isNodeVisible(graph, node, view)),
    // snapshot: the graph's nodes changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph, multi, view, snapshot],
  );

  const selectedSet = useMemo(() => new Set(selection), [selection]);

  /** Selects `nodes`: none, one (the explorer's selection) or several. */
  const selectMany = useCallback(
    (nodes: Iterable<string>) => {
      const next = new Set(nodes);
      if (next.size >= 2) {
        selectOne(null);
        setMulti(next);
        return;
      }
      setMulti(NO_SELECTION);
      const [only] = next;
      selectOne(only ?? null);
    },
    [selectOne],
  );
  const currentSelection = useCallback((): Set<string> => {
    if (multi.size > 0) return new Set(multi);
    const single = explorer.view.selectedNode;
    return new Set(single === null ? [] : [single]);
  }, [multi, explorer.view.selectedNode]);
  const toggleInSelection = useCallback(
    (node: string) => {
      const next = currentSelection();
      if (next.has(node)) next.delete(node);
      else next.add(node);
      selectMany(next);
    },
    [currentSelection, selectMany],
  );
  const boxSelect = useCallback(
    (nodes: string[], additive: boolean) => {
      const next = additive ? currentSelection() : new Set<string>();
      for (const node of nodes) next.add(node);
      selectMany(next);
    },
    [currentSelection, selectMany],
  );
  const selectNeighbours = useCallback(
    (node: string) => {
      if (!graph.hasNode(node)) return;
      const next = new Set([node]);
      graph.forEachNeighbor(node, (neighbour) => {
        if (isNodeVisible(graph, neighbour, view)) next.add(neighbour);
      });
      selectMany(next);
    },
    [graph, view, selectMany],
  );

  // --- view preferences, menus and dialogs -------------------------------------------
  const [wheel, setWheel] = useState<WheelSetting>(storedWheelSetting);
  const [minimapPreference, setMinimapPreference] = useState<MinimapPreference>(storedMinimap);
  const visibleCount = visible.size;
  const minimapShown =
    minimapPreference === "auto"
      ? visibleCount > MINIMAP_DEFAULT_THRESHOLD
      : minimapPreference === "on";
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [confirmUnpinAll, setConfirmUnpinAll] = useState(false);
  const stageRef = useRef<HTMLDivElement>(null);
  const frameCover = useOverlayCover(stageRef);
  const openMenu = useCallback(
    (node: string, at: Point) => {
      if (!graph.hasNode(node)) return;
      const stage = stageRef.current;
      setMenu({
        node,
        at,
        bounds: { width: stage?.clientWidth ?? 0, height: stage?.clientHeight ?? 0 },
      });
    },
    [graph],
  );

  const selectedId =
    view.selectedNode !== null && graph.hasNode(view.selectedNode) ? view.selectedNode : null;
  const selected = selectedId === null ? null : graph.getNodeAttribute(selectedId, "entity");
  const connections = useMemo(
    () => (selectedId === null || snapshot.size === 0 ? [] : connectionsOf(graph, selectedId)),
    [graph, selectedId, snapshot],
  );
  const types = useMemo(() => countByType(data.nodes), [data]);
  const summary = useMemo<GraphSummary>(
    () => ({ nodes: data.nodes.length, edges: data.edges.length, types }),
    [data, types],
  );
  const searchable = useMemo(
    () => data.nodes.filter((node) => visible.has(node.id)),
    [data, visible],
  );

  useEffect(() => {
    onSelectionChange?.(selectedId);
  }, [onSelectionChange, selectedId]);

  // Focus requests wait until their node is in the graph (e.g. once its group is expanded).
  const [pending, setPending] = useState<FocusRequest | null>(null);

  /** Selects a node; a group node is expanded instead (its parent stays selected). */
  const choose = useCallback(
    (node: string | null) => {
      if (node !== null && isGroupId(node)) {
        setGroupExpanded(node, true);
        return;
      }
      selectNode(node);
    },
    [selectNode, setGroupExpanded],
  );

  const focusNode = useCallback(
    (node: string) => {
      if (isGroupId(node)) {
        setGroupExpanded(node, true);
        return;
      }
      const group = graph.hasNode(node) ? undefined : groupContaining(collapsed, node);
      if (group) {
        setGroupExpanded(group.id, true);
        setPending({ id: node });
        return;
      }
      selectNode(node);
      canvasRef.current?.focusNode(node);
    },
    [graph, canvasRef, collapsed, selectNode, setGroupExpanded],
  );

  useImperativeHandle(ref, () => ({ focusNode }), [focusNode]);

  // The last request of each kind that was applied (each is applied once).
  const handledFocus = useRef<FocusRequest | null>(null);
  const handledPending = useRef<FocusRequest | null>(null);
  const applyFocus = useCallback(
    (request: FocusRequest | null, handled: { current: FocusRequest | null }) => {
      if (request === null || handled.current === request) return;
      if (!graph.hasNode(request.id)) {
        const group = groupContaining(collapsed, request.id);
        if (group) setGroupExpanded(group.id, true);
        return;
      }
      handled.current = request;
      selectNode(request.id);
      // Move the camera once the node has settled. sigma keeps the camera where
      // it is when a layout changes the graph's extent, so in a small graph show
      // all of it (the new node is selected and highlighted); in a large one,
      // whose extent barely changes, go to the node.
      const run = layoutRef.current;
      void (run && !run.finished ? run.done : Promise.resolve()).then(() => {
        if (!graph.hasNode(request.id)) return;
        if (graph.order <= SMALL_GRAPH) canvasRef.current?.resetCamera();
        else canvasRef.current?.focusNode(request.id);
      });
    },
    [graph, canvasRef, collapsed, selectNode, setGroupExpanded],
  );

  useEffect(() => {
    applyFocus(focus, handledFocus);
  }, [applyFocus, focus, snapshot]);
  useEffect(() => {
    applyFocus(pending, handledPending);
  }, [applyFocus, pending, snapshot]);

  const onResetView = useCallback(() => {
    setMulti(NO_SELECTION);
    resetView();
    canvasRef.current?.resetCamera();
  }, [canvasRef, resetView]);

  // --- saved views ----------------------------------------------------------------------
  const [ready, setReady] = useState(false);
  const [cameraRequest, setCameraRequest] = useState(0);
  const [viewsRequest, setViewsRequest] = useState(0);
  // A camera to move to once what a view changed is drawn (each request applied once).
  const pendingCameraRef = useRef<PendingCamera | null>(null);
  const capture = useCallback((): ViewSnapshot => {
    const canvas = canvasRef.current;
    return {
      mode,
      depth,
      camera: canvas?.getCamera() ?? null,
      camera3d: canvas?.getCamera3d?.() ?? null,
      filters,
      expanded: [...expanded],
      pins: pinStore.saved(),
    };
  }, [canvasRef, mode, depth, filters, expanded, pinStore]);

  /** Switches to `next`; `camera`: where it looks (else: where the current view does, at once). */
  const switchMode = useCallback(
    (next: ViewMode, camera?: PendingCamera) => {
      if (next !== mode) {
        setReady(false);
        setWebgl("ok");
        storeMode(next);
        if (onModeChange) onModeChange(next);
        else setOwnMode(next);
      }
      pendingCameraRef.current = camera ?? {
        camera: canvasRef.current?.getCamera() ?? null,
        camera3d: null,
      };
      setCameraRequest((n) => n + 1);
    },
    [canvasRef, mode, onModeChange],
  );
  const applySnapshot = useCallback(
    (next: ViewSnapshot) => {
      setMenu(null);
      setMulti(NO_SELECTION);
      updateFilters(() => next.filters);
      setExpanded(new Set(next.expanded));
      pinStore.replace(next.pins);
      setDepth(next.depth);
      switchMode(next.mode, { camera: next.camera, camera3d: next.camera3d, animate: true });
    },
    [updateFilters, pinStore, switchMode],
  );
  useEffect(() => {
    const pending = pendingCameraRef.current;
    const canvas = canvasRef.current;
    if (pending === null || !ready || !canvas) return;
    pendingCameraRef.current = null;
    const ms = pending.animate && !prefersReducedMotion() ? VIEW_CAMERA_MS : 0;
    if (pending.camera3d && canvas.setCamera3d) canvas.setCamera3d(pending.camera3d, ms);
    else if (pending.camera) canvas.setCamera(pending.camera, ms);
  }, [canvasRef, cameraRequest, ready, snapshot]);
  const savedViewControls = useMemo<SavedViewControls>(
    () => ({ ready, capture, apply: applySnapshot, openRequest: viewsRequest }),
    [ready, capture, applySnapshot, viewsRequest],
  );

  const clearFilters = useCallback(() => {
    updateFilters(() => EMPTY_FILTERS);
  }, [updateFilters]);

  // Expanded groups around the selection, which the side panel can collapse again.
  const collapsible = useMemo<LeafGroup[]>(
    () =>
      selectedId === null
        ? []
        : groups.filter(
            (group) =>
              expanded.has(group.id) &&
              (group.parent === selectedId || group.members.includes(selectedId)),
          ),
    [groups, expanded, selectedId],
  );
  const selectedPinned = selectedId !== null && pins.isPinned(selectedId);
  const pinActions =
    selectedId === null ? null : (
      <section aria-label="Layout">
        <h3 className="panel__section">Layout</h3>
        <p className="panel__hint">
          {selectedPinned
            ? "Pinned: the layout leaves it where you put it."
            : "Placed by the layout. Drag it to pin it in place."}
        </p>
        <button
          type="button"
          className="button button--small"
          onClick={() => {
            pins.toggle(selectedId);
          }}
        >
          {selectedPinned ? "Unpin" : "Pin"}
        </button>
      </section>
    );
  const groupActions =
    collapsible.length === 0 ? null : (
      <section aria-label="Groups">
        <h3 className="panel__section">Groups</h3>
        <ul className="group-actions">
          {collapsible.map((group) => (
            <li key={group.id}>
              <span>{groupLabel(group.type, group.members.length)}</span>
              <button
                type="button"
                className="button button--small"
                onClick={() => {
                  if (selectedId !== null && group.members.includes(selectedId)) {
                    selectNode(group.parent);
                  }
                  setGroupExpanded(group.id, false);
                }}
              >
                Collapse
              </button>
            </li>
          ))}
        </ul>
      </section>
    );

  // What both canvases take.
  const canvasProps = {
    graph,
    view,
    onHoverNode: explorer.hoverNode,
    onSelectNode: choose,
    onToggleNode: toggleInSelection,
    onBoxSelect: boxSelect,
    onNodesMoved: pins.pin,
    onTogglePin: pins.toggle,
    onContextMenu: openMenu,
    onClearSelection: () => {
      selectNode(null);
    },
    onShowShortcuts: () => {
      setShortcutsOpen(true);
    },
    onOpenViews: () => {
      setViewsRequest((n) => n + 1);
    },
    onReadyChange: setReady,
    onFocusChange: (node: string | null) => {
      if (node !== null) setLastFocus(node);
    },
    wheelSetting: wheel,
    showMinimap: minimapShown,
    revision: snapshot,
    pinRevision: pins.version,
    palette: painted,
    theme,
    frameCover,
    ...(pulse ? { pulse } : {}),
  };

  return (
    <div className={className ? `workspace ${className}` : "workspace"}>
      <section className="stage" aria-label="Graph">
        <div className="graph-toolbar">
          <SearchBox nodes={searchable} onPick={focusNode} />
          <button
            type="button"
            className="button"
            aria-expanded={filtersOpen}
            onClick={() => {
              setFiltersOpen((open) => !open);
            }}
          >
            Filters
            {activeFilters > 0 && (
              <span className="count-badge" aria-label={`${String(activeFilters)} active`}>
                {activeFilters}
              </span>
            )}
          </button>
          <ViewModeSwitch mode={mode} onChange={switchMode} />
          {show3d && <DepthSwitch depth={depth} onChange={setDepth} />}
          <ViewMenu
            minimap={minimapShown}
            onMinimapChange={(shown) => {
              const next = shown ? "on" : "off";
              setMinimapPreference(next);
              storeMinimap(next);
            }}
            wheel={wheel}
            onWheelChange={(setting) => {
              setWheel(setting);
              storeWheelSetting(setting);
            }}
            pinnedCount={pinStore.size}
            onUnpinAll={() => {
              setConfirmUnpinAll(true);
            }}
          />
          {savedViews !== undefined && (
            <SavedViewContext value={savedViewControls}>{savedViews}</SavedViewContext>
          )}
          {toolbar}
        </div>
        <div className="stage__canvas" ref={stageRef}>
          {(!show3d || webgl === "lost") && (
            <GraphCanvas
              ref={canvas2dRef}
              {...canvasProps}
              showAllLabels={snapshot.order <= SMALL_GRAPH}
              initialFocus={lastFocus}
            />
          )}
          {show3d && (
            <Graph3DBoundary
              onError={() => {
                setWebgl("unavailable");
              }}
            >
              <Suspense fallback={null}>
                <GraphCanvas3D
                  ref={canvas3dRef}
                  {...canvasProps}
                  depth={depth}
                  initialFocus={lastFocus}
                  hidden={webgl === "lost"}
                  onContextLost={() => {
                    setWebgl("lost");
                  }}
                  onContextRestored={() => {
                    setWebgl("ok");
                  }}
                />
              </Suspense>
            </Graph3DBoundary>
          )}
          {mode === "3d" && !active3d && (
            <p className="notice graph-notice" role="status">
              {webgl === "lost" ? WEBGL_LOST : NO_WEBGL}
            </p>
          )}
          {(pins.saveStatus === "retrying" || pins.saveStatus === "failed") && (
            <p className="layout-status" role="status">
              {pins.saveStatus === "retrying"
                ? "Couldn’t save layout — retrying"
                : "Couldn’t save layout. Reload the page to try again."}
            </p>
          )}
          {menu !== null && graph.hasNode(menu.node) && (
            <NodeContextMenu
              at={menu.at}
              bounds={menu.bounds}
              label={graph.getNodeAttribute(menu.node, "entity").label}
              type={graph.getNodeAttribute(menu.node, "entityType")}
              pinned={pins.isPinned(menu.node)}
              onTogglePin={() => {
                pins.toggle(menu.node);
              }}
              onSelectNeighbours={() => {
                selectNeighbours(menu.node);
              }}
              onHideType={() => {
                explorer.toggleEntityType(graph.getNodeAttribute(menu.node, "entityType"));
              }}
              onClose={() => {
                setMenu(null);
              }}
            />
          )}
          {activeFilters > 0 && !filtersOpen && (
            <p className="filter-status" role="status">
              {summaryText} ·{" "}
              <button type="button" className="link-button" onClick={clearFilters}>
                Clear all
              </button>
            </p>
          )}
          {overlay !== undefined && <div className="stage__overlay">{overlay}</div>}
          {filtersOpen && (
            <FiltersPanel
              data={data}
              filters={filters}
              onChange={updateFilters}
              summary={summaryText}
              activeCount={activeFilters}
              onClearAll={clearFilters}
              onClose={() => {
                setFiltersOpen(false);
              }}
              selected={selected?.group ? null : selected}
              {...(grades ? { grades } : {})}
            />
          )}
          <Legend
            types={types}
            hiddenTypes={view.hiddenTypes}
            onToggle={explorer.toggleEntityType}
            onShowAll={explorer.showAllTypes}
          />
          <div className="zoom-controls" role="group" aria-label="Zoom and rotation">
            <button
              type="button"
              className="icon-button icon-button--square"
              aria-label="Zoom in"
              title="Zoom in"
              onClick={() => canvasRef.current?.zoomIn()}
            >
              <ZoomIcon plus />
            </button>
            <button
              type="button"
              className="icon-button icon-button--square"
              aria-label="Zoom out"
              title="Zoom out"
              onClick={() => canvasRef.current?.zoomOut()}
            >
              <ZoomIcon />
            </button>
            <button
              type="button"
              className="icon-button icon-button--square"
              aria-label="Rotate left"
              title="Rotate left (Q)"
              onClick={() => canvasRef.current?.rotate(-1)}
            >
              <RotateIcon />
            </button>
            <button
              type="button"
              className="icon-button icon-button--square"
              aria-label="Rotate right"
              title="Rotate right (E)"
              onClick={() => canvasRef.current?.rotate(1)}
            >
              <RotateIcon clockwise />
            </button>
            <button
              type="button"
              className="icon-button icon-button--square"
              aria-label="Reset view"
              title="Reset view (0)"
              onClick={onResetView}
            >
              <FitIcon />
            </button>
            <button
              type="button"
              className="icon-button icon-button--square"
              aria-label="Keyboard and mouse shortcuts"
              title="Shortcuts (?)"
              onClick={() => {
                setShortcutsOpen(true);
              }}
            >
              <span aria-hidden="true" className="zoom-controls__help">
                ?
              </span>
            </button>
          </div>
        </div>
      </section>
      <SidePanel
        selected={selected}
        connections={connections}
        overview={
          selection.length >= 2 ? (
            <SelectionSummary
              {...(renderSelection
                ? {
                    extra: renderSelection(data.nodes.filter((node) => selectedSet.has(node.id))),
                  }
                : {})}
              count={selection.length}
              pinned={selection.filter((node) => pins.isPinned(node)).length}
              onPinAll={() => {
                pins.pin(selection);
              }}
              onUnpinAll={() => {
                pins.unpin(selection);
              }}
              onClear={() => {
                selectNode(null);
              }}
            />
          ) : (
            overview(summary)
          )
        }
        {...(renderEntity ? { renderEntity } : {})}
        extra={
          <>
            {pinActions}
            {groupActions}
          </>
        }
        onSelect={focusNode}
        onClear={() => {
          selectNode(null);
        }}
      />
      {shortcutsOpen && (
        <ShortcutsDialog
          groups={active3d && shortcuts3d ? shortcuts3d : SHORTCUTS}
          onClose={() => {
            setShortcutsOpen(false);
          }}
        />
      )}
      {confirmUnpinAll && (
        <Modal
          title="Unpin all entities?"
          description={`${pinStore.size.toLocaleString("en")} pinned ${pinStore.size === 1 ? "entity goes" : "entities go"} back to the automatic layout. This changes only your own view.`}
          onClose={() => {
            setConfirmUnpinAll(false);
          }}
        >
          <div className="button-row dialog__actions">
            <button
              type="button"
              className="button"
              onClick={() => {
                setConfirmUnpinAll(false);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                setConfirmUnpinAll(false);
                pins.unpinAll();
              }}
            >
              Unpin all
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** Room kept between the framed graph and the overlays over it (pixels). */
const OVERLAY_CLEARANCE = 8;
/** The overlays over the graph's stage that the framing keeps clear of. */
const OVERLAYS = ":scope > .legend, :scope > .zoom-controls, :scope > .graph-minimap";

const NO_COVER: readonly Rect[] = [];

function sameCover(a: readonly Rect[], b: readonly Rect[]): boolean {
  return (
    a.length === b.length &&
    a.every((r, i) => {
      const o = b[i];
      return r.x === o?.x && r.y === o.y && r.width === o.width && r.height === o.height;
    })
  );
}

/**
 * What the legend, the zoom controls and the minimap cover of the stage
 * (pixels from its top left corner, with some clearance), kept up to date
 * as they come and go and as they and the stage change size. Both views
 * frame the graph in the largest part left free (see ./framing).
 */
function useOverlayCover(stageRef: { current: HTMLDivElement | null }): readonly Rect[] {
  const [cover, setCover] = useState<readonly Rect[]>(NO_COVER);
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || typeof ResizeObserver === "undefined") return;
    const overlays = () => Array.from(stage.querySelectorAll<HTMLElement>(OVERLAYS));
    const measure = () => {
      const box = stage.getBoundingClientRect();
      const next = overlays()
        .map((element) => element.getBoundingClientRect())
        .filter((r) => r.width > 0 && r.height > 0)
        .map((r) => ({
          x: Math.round(r.left - box.left - OVERLAY_CLEARANCE),
          y: Math.round(r.top - box.top - OVERLAY_CLEARANCE),
          width: Math.round(r.width + 2 * OVERLAY_CLEARANCE),
          height: Math.round(r.height + 2 * OVERLAY_CLEARANCE),
        }));
      setCover((previous) => (sameCover(previous, next) ? previous : next));
    };
    const resizes = new ResizeObserver(measure);
    const observe = () => {
      resizes.disconnect();
      resizes.observe(stage);
      for (const element of overlays()) resizes.observe(element);
    };
    // The minimap comes and goes.
    const children =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver(() => {
            observe();
            measure();
          });
    children?.observe(stage, { childList: true });
    observe();
    measure();
    return () => {
      resizes.disconnect();
      children?.disconnect();
    };
  }, [stageRef]);
  return cover;
}

/** The side panel while several entities are selected. */
function SelectionSummary({
  count,
  pinned,
  onPinAll,
  onUnpinAll,
  onClear,
  extra,
}: {
  count: number;
  pinned: number;
  onPinAll: () => void;
  onUnpinAll: () => void;
  onClear: () => void;
  extra?: ReactNode;
}) {
  return (
    <section aria-labelledby="selection-title">
      <h2 id="selection-title" className="panel__title">
        {count.toLocaleString("en")} entities selected
      </h2>
      <p className="panel__hint">
        {pinned.toLocaleString("en")} pinned. Drag any of them to move them together.
      </p>
      <div className="button-row">
        <button type="button" className="button button--small" onClick={onPinAll}>
          Pin all
        </button>
        <button
          type="button"
          className="button button--small"
          disabled={pinned === 0}
          onClick={onUnpinAll}
        >
          Unpin all
        </button>
        <button type="button" className="button button--small" onClick={onClear}>
          Clear
        </button>
      </div>
      {extra}
    </section>
  );
}

/** The 2D / 3D switch (the choice is remembered; see ./mode). */
function ViewModeSwitch({
  mode,
  onChange,
}: {
  mode: ViewMode;
  onChange: (mode: ViewMode) => void;
}) {
  return (
    <div className="segmented view-mode" role="group" aria-label="View">
      {(["2d", "3d"] as const).map((option) => (
        <button
          key={option}
          type="button"
          className="segmented__option"
          aria-pressed={mode === option}
          onClick={() => {
            onChange(option);
          }}
        >
          {option.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

/** What the third axis shows in 3D: first seen, entity type, or nothing. */
function DepthSwitch({
  depth,
  onChange,
}: {
  depth: DepthMode;
  onChange: (depth: DepthMode) => void;
}) {
  return (
    <div className="segmented view-mode" role="group" aria-label="Depth">
      {DEPTH_MODES.map((option) => (
        <button
          key={option}
          type="button"
          className="segmented__option"
          aria-pressed={depth === option}
          onClick={() => {
            onChange(option);
          }}
        >
          {option[0]?.toUpperCase()}
          {option.slice(1)}
        </button>
      ))}
    </div>
  );
}

function ZoomIcon({ plus = false }: { plus?: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d={plus ? "M3 8h10M8 3v10" : "M3 8h10"}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** A circular arrow: rotate the view (counter-clockwise unless `clockwise`). */
function RotateIcon({ clockwise = false }: { clockwise?: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      focusable="false"
      style={clockwise ? { transform: "scaleX(-1)" } : undefined}
    >
      <path
        d="M4.2 4.5A5 5 0 1 1 3 8.5M4.2 4.5V1.8M4.2 4.5H7"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Four corners: fit the whole graph. */
function FitIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default GraphView;
