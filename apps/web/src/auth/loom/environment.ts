/**
 * What the sign-in loom checks before it loads (./LoomBackdrop): reduced
 * motion, Save-Data, WebGL, an idle moment, and the colors of the theme.
 */
import { typeToken } from "../../domain/entities";
import { hexToUnitRgb } from "../../theme/contrast";
import { token, type Theme } from "../../theme/tokens";
import { KNOT_TYPES } from "./geometry";
import type { LoomColors } from "./loomScene";

export type StillReason = "reduced-motion" | "save-data" | "no-webgl" | "failed";

/** "still": the drawing only; "loading": the scene is starting behind it; "scene": the scene shows. */
export type LoomState =
  { mode: "still"; reason: StillReason | null } | { mode: "loading" | "scene" };

export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

interface NetworkInformationLike {
  saveData?: boolean;
}

export function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

export function wantsSaveData(): boolean {
  const connection = (navigator as Navigator & { connection?: NetworkInformationLike }).connection;
  return connection?.saveData === true;
}

/** Whether a WebGL context can be created (a throwaway canvas, released at once). */
export function hasWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    if (!gl) return false;
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

/** Why the still drawing must stay, or null when the scene may load. */
export function stillReason(): StillReason | null {
  if (prefersReducedMotion()) return "reduced-motion";
  if (wantsSaveData()) return "save-data";
  if (!hasWebGL()) return "no-webgl";
  return null;
}

/** The scene's colors from the current theme's tokens. */
export function loomColors(theme: Theme): LoomColors {
  return {
    background: hexToUnitRgb(token(theme, "--bg")),
    warp: hexToUnitRgb(token(theme, "--hair")),
    weft: hexToUnitRgb(token(theme, "--brass")),
    knots: KNOT_TYPES.map((type) => hexToUnitRgb(token(theme, typeToken(type)))),
  };
}

/**
 * Calls `callback` once the page has loaded and the browser is idle. Returns
 * a function that cancels it.
 */
export function whenIdle(callback: () => void): () => void {
  let cancelled = false;
  let idleHandle: number | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const idle = () => {
    if (cancelled) return;
    if (typeof window.requestIdleCallback === "function") {
      idleHandle = window.requestIdleCallback(() => {
        if (!cancelled) callback();
      });
    } else {
      // No requestIdleCallback (Safari before 18): a moment after load.
      timer = setTimeout(() => {
        if (!cancelled) callback();
      }, 500);
    }
  };
  const onLoad = () => {
    idle();
  };
  if (document.readyState === "complete") idle();
  else window.addEventListener("load", onLoad, { once: true });
  return () => {
    cancelled = true;
    window.removeEventListener("load", onLoad);
    if (idleHandle !== null && typeof window.cancelIdleCallback === "function") {
      window.cancelIdleCallback(idleHandle);
    }
    if (timer !== null) clearTimeout(timer);
  };
}
