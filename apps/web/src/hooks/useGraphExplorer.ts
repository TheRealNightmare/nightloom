import { useCallback, useMemo, useState } from "react";

import type { EntityType } from "../domain/entities";
import type { NightloomGraph } from "../graph/buildGraph";
import { INITIAL_VIEW_STATE, toggleType, type ViewState } from "../graph/view";

export interface GraphExplorer {
  view: ViewState;
  hoverNode: (node: string | null) => void;
  selectNode: (node: string | null) => void;
  toggleEntityType: (type: EntityType) => void;
  showAllTypes: () => void;
  resetView: () => void;
}

/** Interaction state of the graph view (hover, selection, hidden types). */
export function useGraphExplorer(graph: NightloomGraph): GraphExplorer {
  const [view, setView] = useState<ViewState>(INITIAL_VIEW_STATE);

  const hoverNode = useCallback((node: string | null) => {
    setView((current) =>
      current.hoveredNode === node ? current : { ...current, hoveredNode: node },
    );
  }, []);

  const selectNode = useCallback(
    (node: string | null) => {
      setView((current) => {
        if (node !== null && !graph.hasNode(node)) return current;
        // Selecting an entity of a hidden type shows that type again.
        let hiddenTypes = current.hiddenTypes;
        if (node !== null) {
          const type = graph.getNodeAttribute(node, "entityType");
          if (hiddenTypes.has(type)) hiddenTypes = toggleType(hiddenTypes, type);
        }
        return { ...current, selectedNode: node, hiddenTypes };
      });
    },
    [graph],
  );

  const toggleEntityType = useCallback(
    (type: EntityType) => {
      setView((current) => {
        const hiddenTypes = toggleType(current.hiddenTypes, type);
        const isHidden = (node: string | null) =>
          node !== null && hiddenTypes.has(graph.getNodeAttribute(node, "entityType"));
        return {
          hiddenTypes,
          selectedNode: isHidden(current.selectedNode) ? null : current.selectedNode,
          hoveredNode: isHidden(current.hoveredNode) ? null : current.hoveredNode,
        };
      });
    },
    [graph],
  );

  const showAllTypes = useCallback(() => {
    setView((current) => ({ ...current, hiddenTypes: new Set() }));
  }, []);

  const resetView = useCallback(() => {
    setView(INITIAL_VIEW_STATE);
  }, []);

  return useMemo(
    () => ({ view, hoverNode, selectNode, toggleEntityType, showAllTypes, resetView }),
    [view, hoverNode, selectNode, toggleEntityType, showAllTypes, resetView],
  );
}
