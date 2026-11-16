import { useCallback, useMemo, useRef, type ReactNode } from "react";

import { useReadiness, type UseReadinessOptions } from "./api/useReadiness";
import { Legend } from "./components/Legend";
import { SearchBox } from "./components/SearchBox";
import { SidePanel } from "./components/SidePanel";
import { TopBar } from "./components/TopBar";
import { sampleGraph } from "./demo/sampleGraph";
import type { InvestigationGraph } from "./domain/entities";
import { buildGraph } from "./graph/buildGraph";
import { GraphCanvas, type GraphCanvasHandle } from "./graph/GraphCanvas";
import { connectionsOf, countByType } from "./graph/view";
import { useGraphExplorer } from "./hooks/useGraphExplorer";

export interface AppProps {
  data?: InvestigationGraph;
  readinessOptions?: UseReadinessOptions;
  /** Shown at the end of the top bar (the signed-in user's menu). */
  account?: ReactNode;
}

export function App({ data = sampleGraph, readinessOptions, account }: AppProps) {
  const graph = useMemo(() => buildGraph(data), [data]);
  const types = useMemo(() => countByType(data.nodes), [data]);
  const explorer = useGraphExplorer(graph);
  const { readiness, recheck } = useReadiness(readinessOptions);
  const canvasRef = useRef<GraphCanvasHandle>(null);

  const { view, selectNode, resetView } = explorer;
  const selected =
    view.selectedNode !== null && graph.hasNode(view.selectedNode)
      ? graph.getNodeAttribute(view.selectedNode, "entity")
      : null;
  const connections = useMemo(
    () => (view.selectedNode === null ? [] : connectionsOf(graph, view.selectedNode)),
    [graph, view.selectedNode],
  );

  const focusNode = useCallback(
    (node: string) => {
      selectNode(node);
      canvasRef.current?.focusNode(node);
    },
    [selectNode],
  );

  const onResetView = useCallback(() => {
    resetView();
    canvasRef.current?.resetCamera();
  }, [resetView]);

  return (
    <div className="app">
      <TopBar readiness={readiness} onRecheck={recheck} account={account} />
      <main className="workspace">
        <section className="stage" aria-label="Graph">
          <GraphCanvas
            ref={canvasRef}
            graph={graph}
            view={view}
            onHoverNode={explorer.hoverNode}
            onSelectNode={selectNode}
          />
          <div className="toolbar">
            <SearchBox nodes={data.nodes} hiddenTypes={view.hiddenTypes} onPick={focusNode} />
            <button type="button" className="button" onClick={onResetView}>
              Reset view
            </button>
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
          summary={{ nodes: graph.order, edges: graph.size, types }}
          onSelect={focusNode}
          onClear={() => {
            selectNode(null);
          }}
        />
      </main>
    </div>
  );
}
