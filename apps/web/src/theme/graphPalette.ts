/**
 * The graph canvas's colors in each theme, from the design tokens. sigma
 * draws with WebGL, so it needs color values rather than CSS custom
 * properties; the values come from src/styles/tokens.css all the same.
 */
import { ENTITY_TYPES, typeToken, type EntityType } from "../domain/entities";
import { token, type Theme } from "./tokens";

export interface GraphPalette {
  /** What shows behind the (transparent) canvas: dimmed items blend toward it. */
  background: string;
  /** Node and edge labels. */
  label: string;
  /** Threads (edges) at rest. */
  edge: string;
  /** The selection: the threads of the focused entity. */
  selection: string;
  /** Node fill per entity type. */
  types: Readonly<Record<EntityType, string>>;
}

export function graphPalette(theme: Theme): GraphPalette {
  return {
    background: token(theme, "--bg"),
    label: token(theme, "--muted"),
    edge: token(theme, "--hair"),
    selection: token(theme, "--brass"),
    types: Object.fromEntries(
      ENTITY_TYPES.map((type) => [type, token(theme, typeToken(type))]),
    ) as Record<EntityType, string>,
  };
}

export const GRAPH_PALETTES: Readonly<Record<Theme, GraphPalette>> = {
  dark: graphPalette("dark"),
  light: graphPalette("light"),
};

/** Used where no theme is given (the reducers' default). */
export const DEFAULT_GRAPH_PALETTE = GRAPH_PALETTES.dark;
