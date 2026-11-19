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

/** Hidden types kept outside the explorer (e.g. in the URL with the other filters). */
export interface HiddenTypesStore {
  hidden: ReadonlySet<EntityType>;
  setHidden: (next: ReadonlySet<EntityType>) => void;
}

type Pointer = Pick<ViewState, "hoveredNode" | "selectedNode">;

/**
 * Interaction state of the graph view (hover, selection, hidden types).
 * Hidden types live here unless `types` keeps them elsewhere.
 */
export function useGraphExplorer(graph: NightloomGraph, types?: HiddenTypesStore): GraphExplorer {
  const [pointer, setPointer] = useState<Pointer>(INITIAL_VIEW_STATE);
  const [ownHidden, setOwnHidden] = useState<ReadonlySet<EntityType>>(
    INITIAL_VIEW_STATE.hiddenTypes,
  );
  const hiddenTypes = types?.hidden ?? ownHidden;
  const setHidden = types?.setHidden ?? setOwnHidden;

  const hoverNode = useCallback((node: string | null) => {
    setPointer((current) =>
      current.hoveredNode === node ? current : { ...current, hoveredNode: node },
    );
  }, []);

  const selectNode = useCallback(
    (node: string | null) => {
      if (node !== null && !graph.hasNode(node)) return;
      // Selecting an entity of a hidden type shows that type again.
      if (node !== null) {
        const type = graph.getNodeAttribute(node, "entityType");
        if (hiddenTypes.has(type)) setHidden(toggleType(hiddenTypes, type));
      }
      setPointer((current) => ({ ...current, selectedNode: node }));
    },
    [graph, hiddenTypes, setHidden],
  );

  const toggleEntityType = useCallback(
    (type: EntityType) => {
      const next = toggleType(hiddenTypes, type);
      setHidden(next);
      const isHidden = (node: string | null) =>
        node !== null &&
        graph.hasNode(node) &&
        next.has(graph.getNodeAttribute(node, "entityType"));
      setPointer((current) => ({
        selectedNode: isHidden(current.selectedNode) ? null : current.selectedNode,
        hoveredNode: isHidden(current.hoveredNode) ? null : current.hoveredNode,
      }));
    },
    [graph, hiddenTypes, setHidden],
  );

  const showAllTypes = useCallback(() => {
    setHidden(new Set());
  }, [setHidden]);

  const resetView = useCallback(() => {
    setPointer(INITIAL_VIEW_STATE);
    setHidden(new Set());
  }, [setHidden]);

  const view = useMemo<ViewState>(() => ({ ...pointer, hiddenTypes }), [pointer, hiddenTypes]);

  return useMemo(
    () => ({ view, hoverNode, selectNode, toggleEntityType, showAllTypes, resetView }),
    [view, hoverNode, selectNode, toggleEntityType, showAllTypes, resetView],
  );
}
