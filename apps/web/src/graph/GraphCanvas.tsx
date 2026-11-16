import { useEffect, useImperativeHandle, useRef, type Ref } from "react";
import Sigma from "sigma";

import type { EdgeAttributes, NightloomGraph, NodeAttributes } from "./buildGraph";
import {
  computeHighlight,
  edgeView,
  LABEL_COLOR,
  nodeView,
  type Highlight,
  type ViewState,
} from "./view";

export interface GraphCanvasHandle {
  /** Animates the camera to a node. */
  focusNode: (node: string) => void;
  /** Animates the camera back to the whole graph. */
  resetCamera: () => void;
}

export interface GraphCanvasProps {
  graph: NightloomGraph;
  view: ViewState;
  onHoverNode: (node: string | null) => void;
  onSelectNode: (node: string | null) => void;
  ref?: Ref<GraphCanvasHandle>;
}

const LABEL_FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif';
const CAMERA_DURATION_MS = 450;

/**
 * Renders the graph with sigma (WebGL). All visual decisions live in the pure
 * functions of ./view; this component only wires sigma's events and reducers.
 */
export function GraphCanvas({ graph, view, onHoverNode, onSelectNode, ref }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma<NodeAttributes, EdgeAttributes> | null>(null);
  // Read by the reducers on every refresh; updated before each refresh.
  const viewRef = useRef<{ state: ViewState; highlight: Highlight }>({
    state: view,
    highlight: computeHighlight(graph, view),
  });
  const callbacksRef = useRef({ onHoverNode, onSelectNode });

  useEffect(() => {
    callbacksRef.current = { onHoverNode, onSelectNode };
  }, [onHoverNode, onSelectNode]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const sigma = new Sigma(graph, container, {
      settings: {
        // Node sizes are pixels (sigma 4 defaults to layout units).
        itemSizesReference: "screen",
        renderEdgeLabels: true,
        labelDensity: 0.6,
        labelRenderedSizeThreshold: 7,
        stagePadding: 64,
        minCameraRatio: 0.08,
        maxCameraRatio: 4,
      },
      styles: {
        nodes: {
          x: { attribute: "x" },
          y: { attribute: "y" },
          size: { attribute: "size", defaultValue: 6 },
          color: { attribute: "color", defaultValue: "#888888" },
          label: { attribute: "label" },
          labelColor: LABEL_COLOR,
          labelFont: LABEL_FONT,
          labelSize: 12,
          cursor: "pointer",
        },
        edges: {
          size: { attribute: "size", defaultValue: 1 },
          color: { attribute: "color", defaultValue: "#3a4568" },
          labelColor: LABEL_COLOR,
          labelFont: LABEL_FONT,
          labelSize: 11,
          labelVisibility: "hidden",
        },
      },
      nodeReducer: (node) =>
        nodeView(graph, node, viewRef.current.state, viewRef.current.highlight),
      edgeReducer: (edge) =>
        edgeView(graph, edge, viewRef.current.state, viewRef.current.highlight),
    });
    sigmaRef.current = sigma;

    sigma.on("enterNode", ({ node }) => {
      callbacksRef.current.onHoverNode(node);
    });
    sigma.on("leaveNode", () => {
      callbacksRef.current.onHoverNode(null);
    });
    sigma.on("clickNode", ({ node }) => {
      callbacksRef.current.onSelectNode(node);
    });
    sigma.on("clickStage", () => {
      callbacksRef.current.onSelectNode(null);
    });

    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
  }, [graph]);

  useEffect(() => {
    viewRef.current = { state: view, highlight: computeHighlight(graph, view) };
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [graph, view]);

  useImperativeHandle(
    ref,
    () => ({
      focusNode(node) {
        const sigma = sigmaRef.current;
        const position = sigma?.getNodeDisplayData(node);
        if (!sigma || !position) return;
        void sigma
          .getCamera()
          .animate({ x: position.x, y: position.y, ratio: 0.7 }, { duration: CAMERA_DURATION_MS });
      },
      resetCamera() {
        void sigmaRef.current?.getCamera().reset({ duration: CAMERA_DURATION_MS });
      },
    }),
    [],
  );

  return (
    <div
      ref={containerRef}
      className="graph-canvas"
      role="img"
      aria-label="Investigation graph. Use the search box to find an entity and the side panel to read its details."
      data-testid="graph-canvas"
    />
  );
}
