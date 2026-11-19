import { createContext, useContext } from "react";

import { documentTheme, type Theme } from "./theme";

export interface ThemeContextValue {
  theme: Theme;
  /** Switches to the other theme and remembers the choice. */
  toggleTheme: () => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);

/**
 * The current theme. Outside a ThemeProvider (isolated component tests) it is
 * whatever is on <html>, dark by default, and cannot be changed.
 */
export function useTheme(): ThemeContextValue {
  return (
    useContext(ThemeContext) ?? {
      theme: documentTheme() ?? "dark",
      toggleTheme: () => undefined,
    }
  );
}
