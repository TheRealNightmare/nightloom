/**
 * The graph's per-browser preferences (View menu), in localStorage. Every
 * read and write is guarded: storage can be unavailable (private windows,
 * blocked site data), and then the defaults apply for the session.
 */
import { isWheelSetting, type WheelSetting } from "./wheel";

export const WHEEL_STORAGE_KEY = "nightloom.graph.wheel";
export const MINIMAP_STORAGE_KEY = "nightloom.graph.minimap";

/** "auto": shown above MINIMAP_DEFAULT_THRESHOLD visible entities. */
export type MinimapPreference = "auto" | "on" | "off";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Not stored: the choice lasts until the page is reloaded.
  }
}

export function storedWheelSetting(): WheelSetting {
  const value = read(WHEEL_STORAGE_KEY);
  return isWheelSetting(value) ? value : "auto";
}

export function storeWheelSetting(value: WheelSetting): void {
  write(WHEEL_STORAGE_KEY, value);
}

export function storedMinimap(): MinimapPreference {
  const value = read(MINIMAP_STORAGE_KEY);
  return value === "on" || value === "off" ? value : "auto";
}

export function storeMinimap(value: MinimapPreference): void {
  write(MINIMAP_STORAGE_KEY, value);
}
