import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from "react";

import { FiltersPanel, type GradeOption } from "../components/FiltersPanel";
import { Legend } from "../components/Legend";
import { SearchBox } from "../components/SearchBox";
import { SidePanel, type GraphSummary } from "../components/SidePanel";
import type { EntityNode, EntityType, InvestigationGraph } from "../domain/entities";
import { useGraphExplorer, type HiddenTypesStore } from "../hooks/useGraphExplorer";
import { createGraph, syncGraph } from "./buildGraph";
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
  fullLayoutIterations,
  INCREMENTAL_LAYOUT_ITERATIONS,
  startLayout,
  type LayoutRun,
} from "./layout";
import { useNow, type FiltersUpdate } from "./useUrlFilters";
import { connectionsOf, countByType } from "./view";

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
  /** Extra controls next to the search box. */
  toolbar?: ReactNode;
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
  ref?: Ref<GraphViewHandle>;
}

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
  toolbar,
  focus = null,
  onSelectionChange,
  pulse,
  filters: controlledFilters,
  onFiltersChange,
  grades,
  className,
  ref,
}: GraphViewProps) {
  const [graph] = useState(createGraph);
  const layoutRef = useRef<LayoutRun | null>(null);
  const canvasRef = useRef<GraphCanvasHandle>(null);
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

  // The graph is an external, mutable store: bring it in line with what is
  // displayed (cheap, incremental) and keep a snapshot that changes whenever it does.
  const snapshot = useMemo(() => {
    syncGraph(graph, display);
    return { order: graph.order, size: graph.size };
  }, [graph, display]);

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
  const { selectNode, resetView } = explorer;
  // Entities the filters hide (group nodes stand for visible entities).
  const hiddenNodes = useMemo(
    () =>
      new Set(
        display.nodes.filter((node) => !node.group && !visible.has(node.id)).map((n) => n.id),
      ),
    [display, visible],
  );
  const view = useMemo(() => ({ ...explorer.view, hiddenNodes }), [explorer.view, hiddenNodes]);

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
    [graph, collapsed, selectNode, setGroupExpanded],
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
    [graph, collapsed, selectNode, setGroupExpanded],
  );

  useEffect(() => {
    applyFocus(focus, handledFocus);
  }, [applyFocus, focus, snapshot]);
  useEffect(() => {
    applyFocus(pending, handledPending);
  }, [applyFocus, pending, snapshot]);

  const onResetView = useCallback(() => {
    resetView();
    canvasRef.current?.resetCamera();
  }, [resetView]);

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

  return (
    <div className={className ? `workspace ${className}` : "workspace"}>
      <section className="stage" aria-label="Graph">
        <GraphCanvas
          ref={canvasRef}
          graph={graph}
          view={view}
          onHoverNode={explorer.hoverNode}
          onSelectNode={choose}
          showAllLabels={snapshot.order <= SMALL_GRAPH}
          revision={snapshot}
          {...(pulse ? { pulse } : {})}
        />
        <div className="toolbar">
          <SearchBox nodes={searchable} onPick={focusNode} />
          <button type="button" className="button" onClick={onResetView}>
            Reset view
          </button>
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
          {toolbar}
        </div>
        {activeFilters > 0 && !filtersOpen && (
          <p className="filter-status" role="status">
            {summaryText} ·{" "}
            <button type="button" className="link-button" onClick={clearFilters}>
              Clear all
            </button>
          </p>
        )}
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
      </section>
      <SidePanel
        selected={selected}
        connections={connections}
        overview={overview(summary)}
        {...(renderEntity ? { renderEntity } : {})}
        extra={groupActions}
        onSelect={focusNode}
        onClear={() => {
          selectNode(null);
        }}
      />
    </div>
  );
}

export default GraphView;
