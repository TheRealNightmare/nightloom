import { useEffect, useRef, type PointerEvent as ReactPointerEvent } from "react";

import type { GraphPalette } from "../../theme/graphPalette";
import type { Point } from "../geometry";
import {
  boundsOf,
  drawMinimap,
  extendBounds,
  minimapProjection,
  throttle,
  type MinimapContext,
  type MinimapDot,
  type MinimapProjection,
} from "../minimap";
import { MINIMAP_HEIGHT, MINIMAP_WIDTH } from "../Minimap";
import type { Camera3D } from "./camera3d";
import { cameraOnMap, viewFootprint } from "./minimap3d";
import type { Viewport } from "./projection";

export interface Minimap3DSource {
  /** Visible nodes from the top (graph x / y) with their type color. */
  dots: () => MinimapDot[];
  camera: () => Camera3D;
  viewport: () => Viewport;
  /** Look at this graph point (x / y on the target's plane). */
  lookAt: (point: Point) => void;
  /** Calls `listener` after each drawn frame; returns the unsubscribe function. */
  subscribe: (listener: () => void) => () => void;
}

export interface Minimap3DProps {
  source: Minimap3DSource;
  palette: GraphPalette;
}

const CAMERA_DOT = 4;

/**
 * The 3D minimap: the graph from the top, every visible node as a dot in its
 * type color, the camera's view footprint as a brass outline and the camera
 * as a brass dot. Click or drag to move the camera's target there. Redrawn
 * at most 10 times a second, and only while something changes. Hidden from
 * assistive technology (the keyboard moves the camera the same way).
 */
export function Minimap3D({ source, palette }: Minimap3DProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const projectionRef = useRef<MinimapProjection | null>(null);
  const sourceRef = useRef(source);
  const paletteRef = useRef(palette);
  const requestRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    sourceRef.current = source;
    paletteRef.current = palette;
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d") as MinimapContext | null | undefined;
    if (!canvas || !context) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = MINIMAP_WIDTH * ratio;
    canvas.height = MINIMAP_HEIGHT * ratio;
    const size = { width: MINIMAP_WIDTH, height: MINIMAP_HEIGHT };
    const draw = () => {
      const current = sourceRef.current;
      const dots = current.dots();
      dots.sort((a, b) => (a.color < b.color ? -1 : a.color > b.color ? 1 : 0));
      const camera = current.camera();
      const footprint = viewFootprint(camera, current.viewport());
      const eye = cameraOnMap(camera);
      const projection = minimapProjection(extendBounds(boundsOf(dots), [...footprint, eye]), size);
      projectionRef.current = projection;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      const color = paletteRef.current.selection;
      drawMinimap(context, size, dots, footprint, projection, { viewport: color });
      const p = projection.toMinimap(eye);
      context.fillStyle = color;
      context.fillRect(p.x - CAMERA_DOT / 2, p.y - CAMERA_DOT / 2, CAMERA_DOT, CAMERA_DOT);
    };
    const throttled = throttle(draw);
    requestRef.current = throttled.request;
    throttled.request();
    const unsubscribe = sourceRef.current.subscribe(throttled.request);
    return () => {
      unsubscribe();
      throttled.cancel();
      requestRef.current = () => undefined;
    };
  }, []);

  useEffect(() => {
    requestRef.current();
  }, [palette]);

  const moveTo = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const projection = projectionRef.current;
    if (!projection) return;
    const box = event.currentTarget.getBoundingClientRect();
    sourceRef.current.lookAt(
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
