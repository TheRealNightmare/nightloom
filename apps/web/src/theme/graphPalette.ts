/**
 * The graph canvas's colors in each theme, from the design tokens. sigma
 * draws with WebGL, so it needs color values rather than CSS custom
 * properties; the values come from src/styles/tokens.css all the same, and
 * src/graph/encoding.ts maps them onto knots and threads.
 */
import { ENTITY_TYPES, typeToken, type EntityType } from "../domain/entities";
import { token, type Theme } from "./tokens";

export interface GraphPalette {
  /** What shows behind the (transparent) canvas: dimmed items blend toward it. */
  background: string;
  /** The knots' face (a disc of the surface color inside the type ring). */
  surface: string;
  /** Node and edge labels. */
  label: string;
  /** Strong text on the canvas: the count inside a group knot. */
  text: string;
  /** Threads (edges) at rest. */
  edge: string;
  /** The selection: the threads of the focused entity, the halo and the pinned ring. */
  selection: string;
  /** The selected node's label (brass, at text contrast). */
  selectionText: string;
  /** Node fill per entity type. */
  types: Readonly<Record<EntityType, string>>;
}

export function graphPalette(theme: Theme): GraphPalette {
  return {
    background: token(theme, "--bg"),
    surface: token(theme, "--surface"),
    label: token(theme, "--muted"),
    text: token(theme, "--text"),
    edge: token(theme, "--hair"),
    selection: token(theme, "--brass"),
    selectionText: token(theme, "--brass-text"),
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
