/**
 * Stand-in for the 3D view's renderer (src/graph/three/Scene3D.tsx) in jsdom,
 * which has no WebGL: a plain <canvas> that reports itself created and lets
 * tests draw frames, so everything around three.js (buffers, camera,
 * labels, picking, keyboard, announcements) runs for real.
 *
 *   vi.mock("../graph/three/Scene3D", () => import("../test/sceneMock"));
 */
import { act } from "@testing-library/react";
import { useEffect, useRef } from "react";

import type { Scene3DProps } from "../graph/three/Scene3D";

/** The viewport frames are drawn at (CSS pixels). */
export const VIEWPORT_3D = { width: 800, height: 600 };

interface SceneState {
  props: Scene3DProps | null;
  canvas: HTMLCanvasElement | null;
  /** A frame was asked for (invalidate) and not drawn yet. */
  pending: boolean;
  frames: number;
}

export const scene: SceneState = { props: null, canvas: null, pending: false, frames: 0 };

export function resetScene(): void {
  scene.props = null;
  scene.canvas = null;
  scene.pending = false;
  scene.frames = 0;
}

export function Scene3D(props: Scene3DProps) {
  const ref = useRef<HTMLCanvasElement>(null);
  const propsRef = useRef(props);
  useEffect(() => {
    propsRef.current = props;
    scene.props = props;
  });
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    scene.canvas = canvas;
    scene.props = propsRef.current;
    propsRef.current.onCreated({
      invalidate: () => {
        scene.pending = true;
      },
      canvas,
    });
    return () => {
      if (scene.canvas === canvas) scene.canvas = null;
    };
  }, []);
  return <canvas ref={ref} className="graph3d-canvas" data-testid="scene3d" />;
}

export default Scene3D;

/** Draws one frame, as the renderer would. */
export function drawFrame(): void {
  act(() => {
    scene.pending = false;
    scene.frames += 1;
    scene.props?.onFrame(VIEWPORT_3D);
  });
}

/** Draws frames while some are asked for (animations, layout), at most `max`. */
export function drawUntilIdle(max = 40): void {
  drawFrame();
  for (let i = 0; i < max && scene.pending; i += 1) drawFrame();
}
