/**
 * The 2D / 3D view mode and the 3D depth mode. Small and free of three.js
 * on purpose: it is part of the 2D graph's bundle; the 3D view itself is a
 * separate, lazily loaded chunk (./three).
 */
export type ViewMode = "2d" | "3d";
/** What the third axis shows: first seen (oldest at the back), entity type, or nothing. */
export type DepthMode = "time" | "type" | "flat";
export type Vec3 = [number, number, number];

/** A 3D camera: where it is, what it looks at, and which way is up (graph coordinates). */
export interface Camera3D {
  position: Vec3;
  target: Vec3;
  up: Vec3;
}

export const DEPTH_MODES: readonly DepthMode[] = ["time", "type", "flat"];
export const DEFAULT_DEPTH: DepthMode = "time";
/** The query parameter that holds the mode (`?mode=3d`). */
export const MODE_PARAM = "mode";
export const MODE_STORAGE_KEY = "nightloom.graph.mode";

export function isViewMode(value: unknown): value is ViewMode {
  return value === "2d" || value === "3d";
}

export function isDepthMode(value: unknown): value is DepthMode {
  return value === "time" || value === "type" || value === "flat";
}

/** The remembered mode (2D when nothing, or nothing readable, is stored). */
export function storedMode(): ViewMode {
  try {
    const value = window.localStorage.getItem(MODE_STORAGE_KEY);
    return isViewMode(value) ? value : "2d";
  } catch {
    return "2d";
  }
}

export function storeMode(mode: ViewMode): void {
  try {
    window.localStorage.setItem(MODE_STORAGE_KEY, mode);
  } catch {
    // Not stored: the choice lasts until the page is reloaded.
  }
}

/** The mode a page opens in: the URL's if it names one, else the remembered one. */
export function initialMode(search: string): ViewMode {
  const value = new URLSearchParams(search).get(MODE_PARAM);
  return isViewMode(value) ? value : storedMode();
}

/** Whether this browser can draw with WebGL 2 (three.js needs it). */
export function webglSupported(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2"));
  } catch {
    return false;
  }
}
