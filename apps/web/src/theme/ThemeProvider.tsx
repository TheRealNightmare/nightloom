import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { ThemeContext, type ThemeContextValue } from "./context";
import {
  applyTheme,
  documentTheme,
  storedTheme,
  storeTheme,
  systemTheme,
  watchSystemTheme,
  type Theme,
} from "./theme";

interface State {
  theme: Theme;
  /** The user chose it (it is stored); otherwise it follows the system. */
  chosen: boolean;
}

/**
 * Owns the light/dark theme: starts from what theme-init.js put on <html>
 * (the stored choice, else the system preference), follows the system until
 * the user picks one, and remembers that pick.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>(() => {
    const stored = storedTheme();
    return { theme: stored ?? documentTheme() ?? systemTheme(), chosen: stored !== null };
  });
  const { theme, chosen } = state;

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (chosen) return;
    return watchSystemTheme((next) => {
      setState((current) => (current.chosen ? current : { theme: next, chosen: false }));
    });
  }, [chosen]);

  const toggleTheme = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    // Without storage the choice lasts until the page is reloaded.
    storeTheme(next);
    applyTheme(next, { animate: true });
    setState({ theme: next, chosen: true });
  }, [theme]);

  const value = useMemo<ThemeContextValue>(() => ({ theme, toggleTheme }), [theme, toggleTheme]);
  return <ThemeContext value={value}>{children}</ThemeContext>;
}
