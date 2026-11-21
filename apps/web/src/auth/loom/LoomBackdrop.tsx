/**
 * The loom beside (or, on narrow screens, behind) the sign-in form. It must
 * never get in the way of signing in:
 *
 * - The still drawing (LoomArt) shows first. The 3D scene is fetched only
 *   after the page has loaded and the browser is idle, as a chunk of its own
 *   that shares three-vendor with the 3D graph view; the sign-in page's
 *   initial JavaScript has no three.js (scripts/check-bundle-size.mjs).
 * - The still drawing stays, and nothing is fetched, with reduced motion,
 *   without WebGL or with Save-Data; it comes back when the scene fails.
 * - Hidden from assistive technology, nothing focusable, no pointer events.
 * - The scene is disposed when the sign-in page goes away, and repainted
 *   with the new tokens when the theme changes.
 */
import { useEffect, useRef, useState } from "react";

import { LoomArt } from "../../components/LoomArt";
import { useTheme } from "../../theme/context";
import {
  REDUCED_MOTION_QUERY,
  loomColors,
  stillReason,
  whenIdle,
  type LoomState,
} from "./environment";
import type { LoomScene } from "./loomScene";

export interface LoomBackdropProps {
  /** For tests: how the scene module is loaded. */
  loadScene?: () => Promise<typeof import("./loomScene")>;
}

const defaultLoad = () => import("./loomScene");

export function LoomBackdrop({ loadScene = defaultLoad }: LoomBackdropProps) {
  const { theme } = useTheme();
  // Decided once, on the first render: a still drawing for good, or the scene later.
  const [state, setState] = useState<LoomState>(() => ({ mode: "still", reason: stillReason() }));
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<LoomScene | null>(null);
  const themeRef = useRef(theme);

  // The scene may load: wait for the page to be loaded and the browser idle.
  const mayLoad = state.mode === "still" && state.reason === null;
  useEffect(() => {
    if (!mayLoad) return;
    return whenIdle(() => {
      setState({ mode: "loading" });
    });
  }, [mayLoad]);

  // Load and start the scene on the canvas that "loading" put in place.
  const loading = state.mode === "loading";
  useEffect(() => {
    if (!loading) return;
    let cancelled = false;
    const fail = () => {
      if (cancelled) return;
      sceneRef.current?.dispose();
      sceneRef.current = null;
      setState({ mode: "still", reason: "failed" });
    };
    loadScene().then(
      (module) => {
        const canvas = canvasRef.current;
        if (cancelled || !canvas) return;
        try {
          sceneRef.current = module.startLoomScene(canvas, loomColors(themeRef.current), {
            onReady: () => {
              if (!cancelled) setState({ mode: "scene" });
            },
            onError: fail,
          });
        } catch {
          fail();
        }
      },
      () => {
        fail();
      },
    );
    return () => {
      cancelled = true;
    };
  }, [loading, loadScene]);

  // Dispose when the sign-in page goes away.
  useEffect(
    () => () => {
      sceneRef.current?.dispose();
      sceneRef.current = null;
    },
    [],
  );

  // Theme: re-read the tokens.
  useEffect(() => {
    if (themeRef.current === theme) return;
    themeRef.current = theme;
    sceneRef.current?.setColors(loomColors(theme));
  }, [theme]);

  // Reduced motion switched on while the scene runs: back to the still drawing.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(REDUCED_MOTION_QUERY);
    const onChange = () => {
      if (!query.matches) return;
      sceneRef.current?.dispose();
      sceneRef.current = null;
      setState({ mode: "still", reason: "reduced-motion" });
    };
    query.addEventListener("change", onChange);
    return () => {
      query.removeEventListener("change", onChange);
    };
  }, []);

  const reason = state.mode === "still" ? (state.reason ?? "pending") : undefined;
  return (
    <div className="loom" aria-hidden="true" data-loom={state.mode} data-loom-reason={reason}>
      {state.mode !== "scene" && <LoomArt className="loom__still" />}
      {state.mode !== "still" && (
        <canvas ref={canvasRef} className="loom__canvas" data-testid="loom-canvas" />
      )}
    </div>
  );
}
