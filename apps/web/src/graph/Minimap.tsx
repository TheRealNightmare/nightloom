import { useEffect, useRef, type PointerEvent as ReactPointerEvent } from "react";

import type { GraphPalette } from "../theme/graphPalette";
import type { NightloomGraph } from "./buildGraph";
import type { AnySigma } from "./interactions";
import {
  boundsOf,
  centerOn,
  drawMinimap,
  extendBounds,
  minimapProjection,
  throttle,
  viewportCorners,
  type MinimapContext,
  type MinimapDot,
  type MinimapProjection,
} from "./minimap";
import { isNodeVisible, type ViewState } from "./view";

export const MINIMAP_WIDTH = 168;
export const MINIMAP_HEIGHT = 112;

export interface MinimapProps {
  sigma: AnySigma;
  graph: NightloomGraph;
  view: ViewState;
  palette: GraphPalette;
}

/**
 * A small overview in the bottom-right corner: every visible node as a dot
 * in its type color and the viewport as a brass outline. Click or drag in it
 * to move the camera there. Redrawn at most 10 times a second while the
 * camera or the nodes move, and not at all otherwise. Hidden from assistive
 * technology: the keyboard pans and zooms the same way.
 */
export function Minimap({ sigma, graph, view, palette }: MinimapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const projectionRef = useRef<MinimapProjection | null>(null);
  const viewRef = useRef(view);
  const paletteRef = useRef(palette);
  const scheduleRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    viewRef.current = view;
    paletteRef.current = palette;
    scheduleRef.current();
  }, [view, palette]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d") as MinimapContext | null | undefined;
    if (!canvas || !context) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = MINIMAP_WIDTH * ratio;
    canvas.height = MINIMAP_HEIGHT * ratio;
    const size = { width: MINIMAP_WIDTH, height: MINIMAP_HEIGHT };

    const draw = () => {
      const dots: MinimapDot[] = [];
      graph.forEachNode((node, attributes) => {
        if (!isNodeVisible(graph, node, viewRef.current)) return;
        dots.push({
          x: attributes.x,
          y: attributes.y,
          color: paletteRef.current.types[attributes.entityType],
        });
      });
      // Grouped by color: one fillStyle change per type.
      dots.sort((a, b) => (a.color < b.color ? -1 : a.color > b.color ? 1 : 0));
      const corners = viewportCorners(sigma);
      const projection = minimapProjection(extendBounds(boundsOf(dots), corners), size);
      projectionRef.current = projection;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      drawMinimap(context, size, dots, corners, projection, {
        viewport: paletteRef.current.selection,
      });
    };
    const throttled = throttle(draw);
    scheduleRef.current = throttled.request;
    const camera = sigma.getCamera();
    camera.on("updated", throttled.request);
    sigma.on("afterRender", throttled.request);
    throttled.request();
    return () => {
      throttled.cancel();
      scheduleRef.current = () => undefined;
      camera.off("updated", throttled.request);
      sigma.off("afterRender", throttled.request);
    };
  }, [sigma, graph]);

  const moveTo = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const projection = projectionRef.current;
    if (!projection) return;
    const box = event.currentTarget.getBoundingClientRect();
    centerOn(
      sigma,
      projection.toGraph({ x: event.clientX - box.left, y: event.clientY - box.top }),
    );
  };

  return (
    <canvas
      ref={canvasRef}
      className="graph-minimap"
      data-testid="graph-minimap"
      aria-hidden="true"
      style={{ width: MINIMAP_WIDTH, height: MINIMAP_HEIGHT }}
      onPointerDown={(event) => {
        // Keep receiving moves while dragging outside the minimap.
        if ("setPointerCapture" in event.currentTarget) {
          event.currentTarget.setPointerCapture(event.pointerId);
        }
        moveTo(event);
      }}
      onPointerMove={(event) => {
        if (event.buttons & 1) moveTo(event);
      }}
    />
  );
}
