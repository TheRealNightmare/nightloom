import { useEffect, useImperativeHandle, useRef, type Ref } from "react";
import type { Attributes } from "graphology-types";
import Sigma from "sigma";

import {
  NO_DASH,
  type EdgeAttributes,
  type NightloomGraph,
  type NodeAttributes,
} from "./buildGraph";
import { DEFAULT_GRAPH_PALETTE, type GraphPalette } from "../theme/graphPalette";
import { nodePrimitives, type NodePrimitives } from "./nodePrimitives";
import {
  computeHighlight,
  edgeView,
  NO_PULSE,
  nodeView,
  type Highlight,
  type ViewState,
} from "./view";

export interface GraphCanvasHandle {
  /** Animates the camera to a node. */
  focusNode: (node: string) => void;
  /** Animates the camera back to the whole graph. */
  resetCamera: () => void;
  /** Animates the camera one step closer. */
  zoomIn: () => void;
  /** Animates the camera one step further away. */
  zoomOut: () => void;
}

export interface GraphCanvasProps {
  graph: NightloomGraph;
  view: ViewState;
  onHoverNode: (node: string | null) => void;
  onSelectNode: (node: string | null) => void;
  /** Label every node, not only the larger ones (for small graphs). */
  showAllLabels?: boolean;
  /** Changes whenever the graph's nodes or edges do, so highlights are recomputed. */
  revision?: unknown;
  /** Nodes to emphasize for a moment (e.g. a job's results). */
  pulse?: ReadonlySet<string>;
  /**
   * Colors of the current theme. Node and edge colors are graph attributes
   * (see recolorGraph); labels, dimming and the selection are read from here
   * on every refresh. The canvas itself is transparent.
   */
  palette?: GraphPalette;
  ref?: Ref<GraphCanvasHandle>;
}

// eslint-disable-next-line @typescript-eslint/no-empty-object-type -- sigma's "no custom state"
type CanvasSigma = Sigma<NodeAttributes, EdgeAttributes, Attributes, {}, {}, {}, NodePrimitives>;

const LABEL_FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif';
const CAMERA_DURATION_MS = 450;
const ZOOM_DURATION_MS = 200;
const FOCUS_RETRY_FRAMES = 10;
const FOCUS_RATIO = 0.7;
/** sigma's default: only nodes drawn at least this many pixels wide get a label. */
const LABEL_SIZE_THRESHOLD = 7;

/**
 * Renders the graph with sigma (WebGL). All visual decisions live in the pure
 * functions of ./view; this component only wires sigma's events and reducers.
 */
export function GraphCanvas({
  graph,
  view,
  onHoverNode,
  onSelectNode,
  showAllLabels = false,
  revision,
  pulse = NO_PULSE,
  palette = DEFAULT_GRAPH_PALETTE,
  ref,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<CanvasSigma | null>(null);
  // Read by the reducers on every refresh; updated before each refresh.
  const viewRef = useRef<{
    state: ViewState;
    highlight: Highlight;
    pulse: ReadonlySet<string>;
    palette: GraphPalette;
  }>({
    state: view,
    highlight: computeHighlight(graph, view),
    pulse,
    palette,
  });
  const callbacksRef = useRef({ onHoverNode, onSelectNode });

  useEffect(() => {
    callbacksRef.current = { onHoverNode, onSelectNode };
  }, [onHoverNode, onSelectNode]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Without WebGL (tests) sigma's defaults apply: no dashed group outlines.
    const primitives = typeof WebGL2RenderingContext === "undefined" ? undefined : nodePrimitives();
    const sigma: CanvasSigma = new Sigma(graph, container, {
      ...(primitives ? { primitives } : {}),
      settings: {
        // Node sizes are pixels (sigma 4 defaults to layout units).
        itemSizesReference: "screen",
        // A frame scheduled before the page navigates away can run after the
        // container left the DOM (zero width) but before sigma is killed;
        // skip drawing then instead of throwing.
        allowInvalidContainer: true,
        renderEdgeLabels: true,
        labelDensity: 0.6,
        labelRenderedSizeThreshold: LABEL_SIZE_THRESHOLD,
        stagePadding: 64,
        minCameraRatio: 0.08,
        maxCameraRatio: 4,
      },
      styles: {
        nodes: {
          x: { attribute: "x" },
          y: { attribute: "y" },
          size: { attribute: "size", defaultValue: 6 },
          color: { attribute: "color", defaultValue: DEFAULT_GRAPH_PALETTE.label },
          label: { attribute: "label" },
          dashColor: { attribute: "dashColor", defaultValue: NO_DASH },
          labelColor: () => viewRef.current.palette.label,
          labelFont: LABEL_FONT,
          labelSize: 12,
          cursor: "pointer",
        },
        edges: {
          size: { attribute: "size", defaultValue: 1 },
          color: { attribute: "color", defaultValue: DEFAULT_GRAPH_PALETTE.edge },
          labelColor: () => viewRef.current.palette.label,
          labelFont: LABEL_FONT,
          labelSize: 11,
          labelVisibility: "hidden",
        },
      },
      nodeReducer: (node) =>
        nodeView(
          graph,
          node,
          viewRef.current.state,
          viewRef.current.highlight,
          viewRef.current.pulse,
          viewRef.current.palette,
        ),
      edgeReducer: (edge) =>
        edgeView(
          graph,
          edge,
          viewRef.current.state,
          viewRef.current.highlight,
          viewRef.current.palette,
        ),
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
    sigmaRef.current?.setSetting(
      "labelRenderedSizeThreshold",
      showAllLabels ? 0 : LABEL_SIZE_THRESHOLD,
    );
  }, [graph, showAllLabels]);

  useEffect(() => {
    viewRef.current = { state: view, highlight: computeHighlight(graph, view), pulse, palette };
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [graph, view, revision, pulse, palette]);

  useImperativeHandle(
    ref,
    () => ({
      focusNode(node) {
        // A node added a moment ago has no display data until sigma's next
        // render, so try again for a few frames.
        let attempts = 0;
        const tryFocus = () => {
          const sigma = sigmaRef.current;
          if (!sigma) return;
          const position = sigma.getNodeDisplayData(node);
          if (!position) {
            attempts += 1;
            if (attempts <= FOCUS_RETRY_FRAMES) requestAnimationFrame(tryFocus);
            return;
          }
          void sigma
            .getCamera()
            .animate(
              { x: position.x, y: position.y, ratio: FOCUS_RATIO },
              { duration: CAMERA_DURATION_MS },
            );
        };
        tryFocus();
      },
      resetCamera() {
        void sigmaRef.current?.getCamera().reset({ duration: CAMERA_DURATION_MS });
      },
      zoomIn() {
        void sigmaRef.current?.getCamera().zoomIn({ duration: ZOOM_DURATION_MS });
      },
      zoomOut() {
        void sigmaRef.current?.getCamera().zoomOut({ duration: ZOOM_DURATION_MS });
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
