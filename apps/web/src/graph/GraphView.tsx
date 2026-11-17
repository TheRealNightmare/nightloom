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

import { Legend } from "../components/Legend";
import { SearchBox } from "../components/SearchBox";
import { SidePanel, type GraphSummary } from "../components/SidePanel";
import type { EntityNode, InvestigationGraph } from "../domain/entities";
import { useGraphExplorer } from "../hooks/useGraphExplorer";
import { createGraph, syncGraph } from "./buildGraph";
import { GraphCanvas, type GraphCanvasHandle } from "./GraphCanvas";
import {
  fullLayoutIterations,
  INCREMENTAL_LAYOUT_ITERATIONS,
  startLayout,
  type LayoutRun,
} from "./layout";
import { connectionsOf, countByType } from "./view";

/** Up to this many nodes, every node is labelled. */
const SMALL_GRAPH = 200;

/** Ask the view to select a node and move the camera to it (a new object each time). */
export interface FocusRequest {
  id: string;
}

export interface GraphViewHandle {
  /** Selects `id` and moves the camera to it. */
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
  /** Extra class names for the outer element (e.g. to embed it in a page). */
  className?: string;
  ref?: Ref<GraphViewHandle>;
}

/**
 * The interactive graph: canvas, search, legend and side panel, shared by the
 * demo and the case Graph tab. It keeps one graphology graph for its whole
 * life and syncs `data` into it, so adding an entity places one node next to
 * its neighbours and runs a short layout instead of rebuilding everything.
 */
export function GraphView({
  data,
  overview,
  renderEntity,
  toolbar,
  focus = null,
  onSelectionChange,
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

  // The graph is an external, mutable store: bring it in line with `data`
  // (cheap, incremental) and keep a snapshot that changes whenever it does.
  const snapshot = useMemo(() => {
    syncGraph(graph, data);
    return { order: graph.order, size: graph.size };
  }, [graph, data]);

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

  const explorer = useGraphExplorer(graph);
  const { view, selectNode, resetView } = explorer;
  const selectedId =
    view.selectedNode !== null && graph.hasNode(view.selectedNode) ? view.selectedNode : null;
  const selected = selectedId === null ? null : graph.getNodeAttribute(selectedId, "entity");
  const connections = useMemo(
    () => (selectedId === null || snapshot.size === 0 ? [] : connectionsOf(graph, selectedId)),
    [graph, selectedId, snapshot],
  );
  const types = useMemo(() => countByType(data.nodes), [data]);
  const summary = useMemo<GraphSummary>(
    () => ({ nodes: snapshot.order, edges: snapshot.size, types }),
    [snapshot, types],
  );

  useEffect(() => {
    onSelectionChange?.(selectedId);
  }, [onSelectionChange, selectedId]);

  const focusNode = useCallback(
    (node: string) => {
      selectNode(node);
      canvasRef.current?.focusNode(node);
    },
    [selectNode],
  );

  useImperativeHandle(ref, () => ({ focusNode }), [focusNode]);

  const handled = useRef<FocusRequest | null>(null);
  useEffect(() => {
    if (focus === null || handled.current === focus || !graph.hasNode(focus.id)) return;
    handled.current = focus;
    selectNode(focus.id);
    // Move the camera once the node has settled. sigma keeps the camera where
    // it is when a layout changes the graph's extent, so in a small graph show
    // all of it (the new node is selected and highlighted); in a large one,
    // whose extent barely changes, go to the node.
    const run = layoutRef.current;
    void (run && !run.finished ? run.done : Promise.resolve()).then(() => {
      if (!graph.hasNode(focus.id)) return;
      if (graph.order <= SMALL_GRAPH) canvasRef.current?.resetCamera();
      else canvasRef.current?.focusNode(focus.id);
    });
  }, [focus, selectNode, graph, snapshot]);

  const onResetView = useCallback(() => {
    resetView();
    canvasRef.current?.resetCamera();
  }, [resetView]);

  return (
    <div className={className ? `workspace ${className}` : "workspace"}>
      <section className="stage" aria-label="Graph">
        <GraphCanvas
          ref={canvasRef}
          graph={graph}
          view={view}
          onHoverNode={explorer.hoverNode}
          onSelectNode={selectNode}
          showAllLabels={snapshot.order <= SMALL_GRAPH}
          revision={snapshot}
        />
        <div className="toolbar">
          <SearchBox nodes={data.nodes} hiddenTypes={view.hiddenTypes} onPick={focusNode} />
          <button type="button" className="button" onClick={onResetView}>
            Reset view
          </button>
          {toolbar}
        </div>
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
        onSelect={focusNode}
        onClear={() => {
          selectNode(null);
        }}
      />
    </div>
  );
}

export default GraphView;
