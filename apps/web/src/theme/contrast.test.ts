/**
 * Every text color the UI puts on a background reaches WCAG AA (4.5:1) in
 * both themes. When a component starts using a new pair, add it here.
 */
import { describe, expect, it } from "vitest";

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

/** Non-text: focus rings, the selection and the brass underline (3:1, WCAG 1.4.11). */
const UI_PAIRS: readonly (readonly [color: string, background: string])[] = [
  ["--brass", "--bg"],
  ["--brass", "--surface"],
  ["--brass", "--surface-2"],
];

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

  it.each(UI_PAIRS)("%s on %s (focus ring, selection) ≥ 3", (color, background) => {
    expect(contrastRatio(token(theme, color), token(theme, background))).toBeGreaterThanOrEqual(
      AA_UI,
    );
  });
});
