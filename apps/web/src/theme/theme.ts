/**
 * Light and dark theme: the stored choice, else the system preference.
 *
 * public/theme-init.js applies the same rule before the first paint (an
 * external script, as the CSP allows no inline ones); keep the two in step.
 */
import { THEMES, token, type Theme } from "./tokens";

export type { Theme } from "./tokens";

/** localStorage key of an explicit choice (absent: follow the system). */
export const THEME_STORAGE_KEY = "nightloom.theme";
/** Set on <html> for a moment while the theme changes, for a short color transition. */
export const THEME_SWITCHING_ATTRIBUTE = "data-theme-switching";
const SWITCHING_MS = 250;

const LIGHT_QUERY = "(prefers-color-scheme: light)";

function isTheme(value: unknown): value is Theme {
  return (THEMES as readonly unknown[]).includes(value);
}

/** The system preference; dark when there is none or it cannot be read. */
export function systemTheme(): Theme {
  try {
    if (typeof window.matchMedia !== "function") return "dark";
    return window.matchMedia(LIGHT_QUERY).matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

/** The stored choice, or null (none, or storage is unavailable). */
export function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(value) ? value : null;
  } catch {
    return null;
  }
}

/** Remembers an explicit choice; false when storage is unavailable (the choice then lasts this page only). */
export function storeTheme(theme: Theme): boolean {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    return true;
  } catch {
    return false;
  }
}

export function initialTheme(): Theme {
  return storedTheme() ?? systemTheme();
}

/** The theme currently on <html> (as set by theme-init.js), if any. */
export function documentTheme(): Theme | null {
  const value = document.documentElement.getAttribute("data-theme");
  return isTheme(value) ? value : null;
}

/**
 * Puts `theme` on <html> and in the browser's theme-color. With `animate`,
 * colors cross-fade briefly; base.css does that only when the user has not
 * asked for reduced motion.
 */
export function applyTheme(theme: Theme, { animate = false }: { animate?: boolean } = {}): void {
  const root = document.documentElement;
  if (animate) {
    root.setAttribute(THEME_SWITCHING_ATTRIBUTE, "");
    window.setTimeout(() => {
      root.removeAttribute(THEME_SWITCHING_ATTRIBUTE);
    }, SWITCHING_MS);
  }
  root.setAttribute("data-theme", theme);
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    meta.content = token(theme, "--bg");
    meta.removeAttribute("media");
  }
}

/** Calls `onChange` when the system preference changes; returns the unsubscribe function. */
export function watchSystemTheme(onChange: (theme: Theme) => void): () => void {
  if (typeof window.matchMedia !== "function") return () => undefined;
  const query = window.matchMedia(LIGHT_QUERY);
  const listener = () => {
    onChange(systemTheme());
  };
  query.addEventListener("change", listener);
  return () => {
    query.removeEventListener("change", listener);
  };
}
