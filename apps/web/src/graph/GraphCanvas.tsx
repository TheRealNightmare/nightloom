import { useEffect, useId, useImperativeHandle, useRef, useState, type Ref } from "react";
import type { Attributes } from "graphology-types";
import Sigma from "sigma";

import type { EdgeAttributes, NightloomGraph, NodeAttributes } from "./buildGraph";
import { DEFAULT_GRAPH_PALETTE, type GraphPalette } from "../theme/graphPalette";
import type { Theme } from "../theme/tokens";
import { CURVATURE, PARALLEL_SPREAD, threadWidthStep } from "./encoding";
import { waitForLabelFont } from "./labelFont";
import { graphPrimitives, type GraphPrimitives } from "./primitives";
import {
  computeHighlight,
  edgeView,
  focusSummary,
  NO_PULSE,
  nodeView,
  type Highlight,
  type RenderContext,
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
   * Colors of the current theme (see ./encoding). Everything sigma draws is
   * computed from it on each refresh, so a theme change repaints in place.
   * The canvas itself is transparent: the page's woven texture shows through.
   */
  palette?: GraphPalette;
  theme?: Theme;
  ref?: Ref<GraphCanvasHandle>;
}

// eslint-disable-next-line @typescript-eslint/no-empty-object-type -- sigma's "no custom state"
type CanvasSigma = Sigma<NodeAttributes, EdgeAttributes, Attributes, {}, {}, {}, GraphPrimitives>;

const CAMERA_DURATION_MS = 450;
const ZOOM_DURATION_MS = 200;
const FOCUS_RETRY_FRAMES = 10;
const FOCUS_RATIO = 0.7;
/** Only knots drawn at least this many pixels wide get a label (all of them in small graphs). */
const LABEL_SIZE_THRESHOLD = 8;
/** Fewer, better-spaced labels than sigma's defaults (1 and 100), so dense graphs stay legible. */
const LABEL_DENSITY = 0.5;
const LABEL_GRID_CELL_SIZE = 140;
/**
 * Renders the graph with sigma (WebGL). All visual decisions live in the pure
 * functions of ./encoding and ./view; this component only wires sigma's
 * events and reducers.
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
  theme = "dark",
  ref,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<CanvasSigma | null>(null);
  // Read by the reducers on every refresh; updated before each refresh.
  const viewRef = useRef<{ state: ViewState; highlight: Highlight; context: RenderContext }>({
    state: view,
    highlight: computeHighlight(graph, view),
    context: { pulse, palette, theme, cameraRatio: 1 },
  });
  const callbacksRef = useRef({ onHoverNode, onSelectNode });
  const showAllLabelsRef = useRef(showAllLabels);
  // Bumped once sigma exists (it may wait for the label font).
  const [mounted, setMounted] = useState(0);

  useEffect(() => {
    callbacksRef.current = { onHoverNode, onSelectNode };
  }, [onHoverNode, onSelectNode]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let sigma: CanvasSigma | null = null;
    let cancelled = false;

    const create = () => {
      if (cancelled) return;
      const created: CanvasSigma = new Sigma(graph, container, {
        primitives: graphPrimitives(),
        settings: {
          // Node sizes are pixels (sigma 4 defaults to layout units).
          itemSizesReference: "screen",
          // A frame scheduled before the page navigates away can run after the
          // container left the DOM (zero width) but before sigma is killed;
          // skip drawing then instead of throwing.
          allowInvalidContainer: true,
          renderEdgeLabels: true,
          labelDensity: LABEL_DENSITY,
          labelGridCellSize: LABEL_GRID_CELL_SIZE,
          labelRenderedSizeThreshold: showAllLabelsRef.current ? 0 : LABEL_SIZE_THRESHOLD,
          stagePadding: 64,
          minCameraRatio: 0.08,
          maxCameraRatio: 4,
        },
        styles: {
          nodes: {
            x: { attribute: "x" },
            y: { attribute: "y" },
            size: { attribute: "size", defaultValue: 8 },
            color: { attribute: "color", defaultValue: DEFAULT_GRAPH_PALETTE.label },
            label: { attribute: "label" },
            // Labels stay horizontal, whatever the camera does.
            labelRotationAlignment: "viewport",
            cursor: "pointer",
          },
          edges: {
            size: { attribute: "size", defaultValue: 1 },
            color: { attribute: "color", defaultValue: DEFAULT_GRAPH_PALETTE.edge },
            label: { attribute: "label" },
            path: "curved",
            curvature: CURVATURE,
            parallelSpread: PARALLEL_SPREAD,
            // Only the focused entity's threads show their label (./view).
            labelVisibility: "hidden",
          },
        },
        nodeReducer: (node) =>
          nodeView(
            graph,
            node,
            viewRef.current.state,
            viewRef.current.highlight,
            viewRef.current.context,
          ),
        edgeReducer: (edge) =>
          edgeView(
            graph,
            edge,
            viewRef.current.state,
            viewRef.current.highlight,
            viewRef.current.context,
          ),
      });
      sigma = created;
      sigmaRef.current = created;

      created.on("enterNode", ({ node }) => {
        callbacksRef.current.onHoverNode(node);
      });
      created.on("leaveNode", () => {
        callbacksRef.current.onHoverNode(null);
      });
      created.on("clickNode", ({ node }) => {
        callbacksRef.current.onSelectNode(node);
      });
      created.on("clickStage", () => {
        callbacksRef.current.onSelectNode(null);
      });
      // Threads get a little wider as the camera zooms in; repaint only when
      // the rounded width changes, not on every frame of a zoom.
      const camera = created.getCamera();
      let step = threadWidthStep(camera.ratio);
      camera.on("updated", ({ ratio }) => {
        const next = threadWidthStep(ratio);
        viewRef.current.context = { ...viewRef.current.context, cameraRatio: ratio };
        if (next === step) return;
        step = next;
        created.refresh({ skipIndexation: true });
      });
      setMounted((count) => count + 1);
    };

    const waiting = waitForLabelFont(typeof document === "undefined" ? undefined : document.fonts);
    if (waiting === null) create();
    else void waiting.then(create);

    return () => {
      cancelled = true;
      sigma?.kill();
      sigmaRef.current = null;
    };
  }, [graph]);

  useEffect(() => {
    showAllLabelsRef.current = showAllLabels;
    sigmaRef.current?.setSetting(
      "labelRenderedSizeThreshold",
      showAllLabels ? 0 : LABEL_SIZE_THRESHOLD,
    );
  }, [graph, showAllLabels, mounted]);

  const highlight = computeHighlight(graph, view);
  useEffect(() => {
    const cameraRatio = viewRef.current.context.cameraRatio;
    viewRef.current = {
      state: view,
      highlight: computeHighlight(graph, view),
      context: { pulse, palette, theme, cameraRatio },
    };
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [graph, view, revision, pulse, palette, theme, mounted]);
  const summary = focusSummary(graph, view, highlight);
  const summaryId = useId();

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
    <>
      <div
        ref={containerRef}
        className="graph-canvas"
        role="img"
        aria-label="Investigation graph. Use the search box to find an entity and the side panel to read its details."
        aria-describedby={summaryId}
        data-testid="graph-canvas"
      />
      {/*
       * The relationships whose labels the canvas shows (the hovered or
       * selected entity's): the canvas's description, announced politely.
       */}
      <p id={summaryId} className="visually-hidden" aria-live="polite">
        {summary}
      </p>
    </>
  );
}
