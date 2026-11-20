/**
 * Colors live in one place: src/styles/tokens.css. Every other stylesheet and
 * component uses its custom properties (or currentColor), so both themes stay
 * complete. The two files that cannot read CSS variables (the favicon and the
 * theme-color meta tags) may only repeat token values.
 */
import { describe, expect, it } from "vitest";

import indexHtml from "../../index.html?raw";
import favicon from "../../public/favicon.svg?raw";
import manifest from "../../public/manifest.webmanifest?raw";
import { THEME_TOKENS } from "../theme/tokens";

/** Every stylesheet and module of the app (not tests or test helpers), by path. */
const SOURCES = import.meta.glob<string>(
  ["../**/*.{css,ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**", "!./tokens.css"],
  { query: "?raw", import: "default", eager: true },
);

const HEX = /#[0-9a-f]{3,8}\b/gi;
/** rgb(), rgba(), hsl(), hsla(), hwb(): colors written out instead of tokens. */
const COLOR_FUNCTION = /\b(?:rgba?|hsla?|hwb)\(/gi;

/** Comments may mention colors ("#rrggbb"); code may not. */
function withoutComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("color literals", () => {
  it("appear only in tokens.css", () => {
    const files = Object.entries(SOURCES);
    // The glob found the app (and not only a few files).
    expect(files.length).toBeGreaterThan(50);
    expect(Object.keys(SOURCES)).toContain("./app.css");
    const offenders = files.flatMap(([file, text]) => {
      const code = withoutComments(text);
      const found = [...(code.match(HEX) ?? []), ...(code.match(COLOR_FUNCTION) ?? [])];
      return found.map((literal) => `${file}: ${literal}`);
    });
    expect(offenders).toEqual([]);
  });

  it("in the favicon, the web manifest and the theme-color tags are token values", () => {
    const values = new Set(
      Object.values(THEME_TOKENS).flatMap((tokens) =>
        Object.values(tokens).map((v) => v.toLowerCase()),
      ),
    );
    for (const [file, text] of [
      ["public/favicon.svg", favicon],
      ["public/manifest.webmanifest", manifest],
      ["index.html", indexHtml],
    ] as const) {
      const hexes = text.match(HEX) ?? [];
      expect(hexes.length, file).toBeGreaterThan(0);
      for (const hex of hexes) expect(values, `${file}: ${hex}`).toContain(hex.toLowerCase());
    }
  });
});

describe("tokens.css", () => {
  it("defines the same tokens in both themes", () => {
    expect(Object.keys(THEME_TOKENS.light).sort()).toEqual(Object.keys(THEME_TOKENS.dark).sort());
  });
});
