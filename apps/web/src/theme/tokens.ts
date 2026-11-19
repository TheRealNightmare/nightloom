/**
 * The design tokens of src/styles/tokens.css as values, for code that cannot
 * use CSS custom properties: the WebGL graph renderer and the contrast test.
 * The stylesheet stays the single source; this module parses it (Vite's
 * `?raw` import inlines its text at build time).
 */
import tokensCss from "../styles/tokens.css?raw";

export const THEMES = ["dark", "light"] as const;
export type Theme = (typeof THEMES)[number];

export type TokenMap = Readonly<Record<string, string>>;

const RULE = /([^{}]+)\{([^{}]*)\}/g;
const DECLARATION = /(--[\w-]+)\s*:\s*([^;]+);/g;
const VAR_REFERENCE = /^var\((--[\w-]+)\)$/;

function declarations(body: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [, name, value] of body.matchAll(DECLARATION)) {
    if (name !== undefined && value !== undefined) result[name] = value.trim();
  }
  return result;
}

/** Follows `var(--x)` references (as the browser would) within one theme. */
function resolve(tokens: Record<string, string>): Record<string, string> {
  const resolved: Record<string, string> = {};
  const lookup = (name: string, seen: ReadonlySet<string>): string => {
    const value = tokens[name];
    if (value === undefined) throw new Error(`Unknown design token ${name}`);
    const reference = VAR_REFERENCE.exec(value)?.[1];
    if (reference === undefined) return value;
    if (seen.has(reference)) throw new Error(`Circular design token ${reference}`);
    return lookup(reference, new Set([...seen, reference]));
  };
  for (const name of Object.keys(tokens)) resolved[name] = lookup(name, new Set([name]));
  return resolved;
}

/**
 * Reads the custom properties of `:root` (shared) and of
 * `:root[data-theme="dark"|"light"]` from a stylesheet's text.
 */
export function parseTokens(css: string): Record<Theme, TokenMap> {
  const shared: Record<string, string> = {};
  const themed: Record<Theme, Record<string, string>> = { dark: {}, light: {} };
  for (const [, rawSelector, body] of css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(RULE)) {
    if (rawSelector === undefined || body === undefined) continue;
    const selector = rawSelector.trim();
    if (selector === ":root") {
      Object.assign(shared, declarations(body));
      continue;
    }
    const theme = /^:root\[data-theme="(dark|light)"\]$/.exec(selector)?.[1] as Theme | undefined;
    if (theme !== undefined) Object.assign(themed[theme], declarations(body));
  }
  return {
    dark: resolve({ ...shared, ...themed.dark }),
    light: resolve({ ...shared, ...themed.light }),
  };
}

/** Every token's value in each theme. */
export const THEME_TOKENS: Readonly<Record<Theme, TokenMap>> = parseTokens(tokensCss);

/** One token's value, e.g. `token("light", "--bg")`. */
export function token(theme: Theme, name: string): string {
  const value = THEME_TOKENS[theme][name];
  if (value === undefined) throw new Error(`Unknown design token ${name}`);
  return value;
}
