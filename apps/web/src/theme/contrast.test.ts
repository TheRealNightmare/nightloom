/**
 * Every text color the UI puts on a background reaches WCAG AA (4.5:1) in
 * both themes, and the colors that mark controls (focus ring, selection,
 * form control edges) reach 3:1 (WCAG 1.4.11). The stylesheets are scanned:
 * a text color that is not listed here, or a text/background pair declared
 * together that fails, fails the test.
 */
import { describe, expect, it } from "vitest";

import appCss from "../styles/app.css?raw";
import baseCss from "../styles/base.css?raw";
import { ENTITY_TYPES, typeToken } from "../domain/entities";
import { contrastRatio, relativeLuminance } from "./contrast";
import { THEMES, token } from "./tokens";

const BACKGROUNDS = ["--bg", "--surface", "--surface-2"] as const;

/** Text colors used on every background (body text, labels, chips, notices). */
const TEXT_ON_ANY = [
  "--text",
  "--muted",
  "--brass-text",
  "--copper",
  "--ok",
  "--danger",
  // Type eyebrow labels in the side panel and legend counts.
  ...ENTITY_TYPES.map(typeToken),
];

/** Pairs with a fixed background. */
const FIXED_PAIRS: readonly (readonly [text: string, background: string])[] = [
  // Primary buttons, count badges, the Admiralty badge, selected segments.
  ["--on-brass", "--brass"],
];

/**
 * Non-text (3:1, WCAG 1.4.11): focus rings, the selection and the brass
 * underline, and the edge of text fields, selects and switches.
 */
const UI_PAIRS: readonly (readonly [color: string, background: string])[] = BACKGROUNDS.flatMap(
  (background) => [["--brass", background] as const, ["--control-line", background] as const],
);

/**
 * Component-local custom properties used as a text color, and the tokens
 * they are set to. `--swatch` is an entity type's color (`.eyebrow--type`).
 */
const LOCAL_TEXT_PROPERTIES: Readonly<Record<string, readonly string[]>> = {
  "--swatch": ENTITY_TYPES.map(typeToken),
};

const RULE = /([^{}]+)\{([^{}]*)\}/g;
const COLOR = /(?:^|[;{\s])color:\s*var\((--[\w-]+)\)/;
const BACKGROUND = /(?:^|[;{\s])background(?:-color)?:\s*var\((--[\w-]+)\)\s*;/;

/** The text colors of a stylesheet, and the text/background pairs declared in the same rule. */
function stylesheetColors(css: string): {
  text: Set<string>;
  pairs: { selector: string; text: string; background: string }[];
} {
  const text = new Set<string>();
  const pairs: { selector: string; text: string; background: string }[] = [];
  for (const [, selector, body] of css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(RULE)) {
    if (selector === undefined || body === undefined) continue;
    const color = COLOR.exec(body)?.[1];
    if (color === undefined) continue;
    text.add(color);
    const background = BACKGROUND.exec(body)?.[1];
    if (background !== undefined)
      pairs.push({ selector: selector.trim(), text: color, background });
  }
  return { text, pairs };
}

const USED = [appCss, baseCss].map(stylesheetColors);
const USED_TEXT = new Set(USED.flatMap(({ text }) => [...text]));
const USED_PAIRS = USED.flatMap(({ pairs }) => pairs);

const AA_TEXT = 4.5;
const AA_UI = 3;

describe("contrastRatio", () => {
  it("matches the WCAG reference values", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    expect(contrastRatio("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
    expect(contrastRatio("#fff", "#000")).toBeCloseTo(21, 5);
    expect(relativeLuminance("#ffffff")).toBeCloseTo(1, 5);
  });

  it("rejects what is not a hex color", () => {
    expect(() => contrastRatio("var(--bg)", "#ffffff")).toThrow(/hex color/);
  });
});

describe("the stylesheets", () => {
  it("are scanned (the parser finds the app's colors)", () => {
    expect(USED_TEXT).toContain("--text");
    expect(USED_TEXT).toContain("--muted");
    expect(USED_PAIRS.length).toBeGreaterThan(3);
    expect(stylesheetColors(".a { color: var(--x); background: var(--y); }").pairs).toEqual([
      { selector: ".a", text: "--x", background: "--y" },
    ]);
    // background-color counts, a color-mix() background does not (it is not a token).
    expect(stylesheetColors(".b{color:var(--x);background-color:var(--z);}").pairs).toHaveLength(1);
    expect(
      stylesheetColors(".c { color: var(--x); background: color-mix(in srgb, var(--y) 9%, red); }")
        .pairs,
    ).toHaveLength(0);
  });

  it("use only text colors this test checks", () => {
    const checked = new Set([
      ...TEXT_ON_ANY,
      ...FIXED_PAIRS.map(([text]) => text),
      ...Object.keys(LOCAL_TEXT_PROPERTIES),
    ]);
    expect([...USED_TEXT].filter((name) => !checked.has(name))).toEqual([]);
    for (const tokens of Object.values(LOCAL_TEXT_PROPERTIES)) {
      for (const name of tokens) expect(TEXT_ON_ANY).toContain(name);
    }
  });
});

describe.each(THEMES)("the %s theme", (theme) => {
  const rows: { text: string; background: string; ratio: number; shown: string }[] = [];
  const row = (text: string, background: string) => {
    const ratio = contrastRatio(token(theme, text), token(theme, background));
    rows.push({ text, background, ratio, shown: ratio.toFixed(2) });
  };
  for (const text of TEXT_ON_ANY) for (const background of BACKGROUNDS) row(text, background);
  for (const [text, background] of FIXED_PAIRS) row(text, background);

  it.each(rows)("$text on $background: $shown:1 ≥ 4.5", ({ ratio }) => {
    expect(ratio).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each(USED_PAIRS)(
    "$text on $background, declared together in $selector, ≥ 4.5",
    ({ text, background }) => {
      const resolved = LOCAL_TEXT_PROPERTIES[text] ?? [text];
      for (const name of resolved) {
        expect(contrastRatio(token(theme, name), token(theme, background))).toBeGreaterThanOrEqual(
          AA_TEXT,
        );
      }
    },
  );

  it.each(UI_PAIRS)("%s on %s (focus ring, selection, control edges) ≥ 3", (color, background) => {
    expect(contrastRatio(token(theme, color), token(theme, background))).toBeGreaterThanOrEqual(
      AA_UI,
    );
  });
});
