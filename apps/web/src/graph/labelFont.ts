import { LABEL_FAMILY, LABEL_SIZE } from "./encoding";

/** The font sigma measures and rasterizes node labels with. */
export const LABEL_FONT_SPEC = `${String(LABEL_SIZE)}px ${LABEL_FAMILY}`;
/** Never hold the graph back for long on a font: draw with the fallback after this. */
export const LABEL_FONT_TIMEOUT_MS = 3000;

/**
 * Resolves once the label font (regular and semibold) can be drawn, or is
 * `null` when it already can (or the browser has no font loading API, as in
 * tests), so the caller draws at once. sigma measures and rasterizes label
 * glyphs once, so drawing before the webfont arrives would keep the
 * fallback font's metrics.
 */
export function waitForLabelFont(fonts: FontFaceSet | undefined): Promise<unknown> | null {
  if (!fonts || typeof fonts.load !== "function") return null;
  if (fonts.check(LABEL_FONT_SPEC) && fonts.check(`600 ${LABEL_FONT_SPEC}`)) return null;
  return Promise.race([
    Promise.all([fonts.load(LABEL_FONT_SPEC), fonts.load(`600 ${LABEL_FONT_SPEC}`)]),
    new Promise((resolve) => setTimeout(resolve, LABEL_FONT_TIMEOUT_MS)),
  ]);
}
