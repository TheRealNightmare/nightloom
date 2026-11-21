/**
 * Which node labels the 2D canvas draws: none may overlap another.
 *
 * sigma's own choice (a grid of cells, the largest knots' labels in each)
 * never looks at the text: two long labels in neighbouring cells overlap,
 * and labels forced visible (a group's count, the hovered entity's
 * neighbours) skip the grid altogether. So the canvas decides instead, on
 * the knots' screen positions, and forces exactly the labels chosen here.
 *
 * Greedy, by priority: the hovered, selected and keyboard-focused entities'
 * labels always show (even over each other); then group counts, then
 * labels the view asks for (the hovered entity's neighbours, pulsing
 * entities), then the best-connected and largest knots. A label shows only
 * where its box (text, plus the pin before a pinned entity's text or the
 * type tag under a group's count) overlaps no label already placed and no
 * other knot (a group's count sits on its own knot).
 */
import { LABEL_FAMILY } from "./encoding";

export interface LabelBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A knot on screen, which labels other than its own keep off. */
export interface KnotMark {
  node: string;
  x: number;
  y: number;
  radius: number;
}

export interface LabelCandidate {
  node: string;
  /** The knot's center on screen (pixels). */
  x: number;
  y: number;
  /** The knot's radius on screen (pixels). */
  radius: number;
  /** Width and height of the text (pixels). */
  textWidth: number;
  textHeight: number;
  /** "right": beside the knot; "over": centered on it (a group's count). */
  position: "right" | "over";
  /** Extra room before the text, toward the knot (a pinned entity's pin, pixels). */
  before?: number;
  /** Extra room under the text: width and height (a group's type tag, pixels). */
  below?: { width: number; height: number };
  /** Hovered, selected or keyboard-focused: always drawn, never hidden by another. */
  mandatory: boolean;
  /** Higher first among the others. */
  priority: number;
}

/** Space between a knot and its label (sigma's default label margin). */
export const LABEL_MARGIN = 5;
/** Room kept free around each label (pixels). */
export const LABEL_PADDING = 2;
/** Labels whose box lies further than this outside the viewport are not placed. */
const OFFSCREEN_MARGIN = 32;
/** Side of the cells placed boxes are bucketed in (pixels). */
const BUCKET = 64;

/** The box a candidate's label covers on screen, `LABEL_PADDING` included. */
export function labelBox(candidate: LabelCandidate): LabelBox {
  const { x, y, radius, textWidth, textHeight, before = 0, below } = candidate;
  const pad = LABEL_PADDING;
  if (candidate.position === "over") {
    const width = Math.max(textWidth, below?.width ?? 0);
    const height = textHeight + (below?.height ?? 0);
    return {
      x: x - width / 2 - pad,
      y: y - textHeight / 2 - pad,
      width: width + 2 * pad,
      height: height + 2 * pad,
    };
  }
  return {
    x: x + radius + LABEL_MARGIN - before - pad,
    y: y - textHeight / 2 - pad,
    width: before + textWidth + 2 * pad,
    height: textHeight + 2 * pad,
  };
}

export function boxesIntersect(a: LabelBox, b: LabelBox): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

/** What sits in a grid cell: a placed label, or a knot (owned by its node). */
interface Occupant {
  owner: string | null;
  box: LabelBox;
}

/** Mandatory first, then higher priority, then the node's name (a stable order). */
function compare(a: LabelCandidate, b: LabelCandidate): number {
  if (a.mandatory !== b.mandatory) return a.mandatory ? -1 : 1;
  if (a.priority !== b.priority) return b.priority - a.priority;
  return a.node < b.node ? -1 : a.node > b.node ? 1 : 0;
}

/**
 * The nodes whose label is drawn, with their boxes: every mandatory one,
 * and each other one whose box overlaps no label placed before it and none
 * of the `knots` but its own. `viewport` (pixels) skips labels well outside
 * it. Boxes are bucketed in a grid of {@link BUCKET} px cells, so each
 * label is tested only against its neighbours.
 */
export function placeLabels(
  candidates: readonly LabelCandidate[],
  viewport?: { width: number; height: number },
  knots: readonly KnotMark[] = [],
): Map<string, LabelBox> {
  const entries: { candidate: LabelCandidate; box: LabelBox }[] = [];
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const candidate of candidates) {
    const box = labelBox(candidate);
    if (
      !candidate.mandatory &&
      viewport !== undefined &&
      (box.x + box.width < -OFFSCREEN_MARGIN ||
        box.y + box.height < -OFFSCREEN_MARGIN ||
        box.x > viewport.width + OFFSCREEN_MARGIN ||
        box.y > viewport.height + OFFSCREEN_MARGIN)
    ) {
      continue;
    }
    entries.push({ candidate, box });
    minX = Math.min(minX, box.x);
    minY = Math.min(minY, box.y);
    maxX = Math.max(maxX, box.x + box.width);
    maxY = Math.max(maxY, box.y + box.height);
  }
  const placed = new Map<string, LabelBox>();
  if (entries.length === 0) return placed;
  entries.sort((a, b) => compare(a.candidate, b.candidate));

  // Only knots that can meet a label matter: those within the labels' bounds.
  const columns = Math.floor((maxX - minX) / BUCKET) + 1;
  const rows = Math.floor((maxY - minY) / BUCKET) + 1;
  const grid: (Occupant[] | undefined)[] = new Array<Occupant[] | undefined>(columns * rows);
  const cellsOf = (box: LabelBox) => ({
    x0: Math.max(0, Math.floor((box.x - minX) / BUCKET)),
    x1: Math.min(columns - 1, Math.floor((box.x + box.width - minX) / BUCKET)),
    y0: Math.max(0, Math.floor((box.y - minY) / BUCKET)),
    y1: Math.min(rows - 1, Math.floor((box.y + box.height - minY) / BUCKET)),
  });
  const occupy = (occupant: Occupant) => {
    const { x0, x1, y0, y1 } = cellsOf(occupant.box);
    for (let row = y0; row <= y1; row += 1) {
      for (let column = x0; column <= x1; column += 1) {
        const index = row * columns + column;
        const cell = grid[index];
        if (cell) cell.push(occupant);
        else grid[index] = [occupant];
      }
    }
  };
  for (const knot of knots) {
    const box = {
      x: knot.x - knot.radius,
      y: knot.y - knot.radius,
      width: 2 * knot.radius,
      height: 2 * knot.radius,
    };
    if (box.x > maxX || box.y > maxY || box.x + box.width < minX || box.y + box.height < minY) {
      continue;
    }
    occupy({ owner: knot.node, box });
  }
  for (const { candidate, box } of entries) {
    if (placed.has(candidate.node)) continue;
    if (!candidate.mandatory) {
      const { x0, x1, y0, y1 } = cellsOf(box);
      let blocked = false;
      for (let row = y0; row <= y1 && !blocked; row += 1) {
        for (let column = x0; column <= x1 && !blocked; column += 1) {
          for (const other of grid[row * columns + column] ?? []) {
            if (other.owner !== candidate.node && boxesIntersect(box, other.box)) {
              blocked = true;
              break;
            }
          }
        }
      }
      if (blocked) continue;
    }
    placed.set(candidate.node, box);
    // A label's owner is null: it blocks every other label, its own knot's included.
    occupy({ owner: null, box });
  }
  return placed;
}

// --- text measurement --------------------------------------------------------------

/** Average glyph width as a share of the font size, when the text cannot be measured. */
const GLYPH_WIDTH = 0.56;

export type MeasureText = (text: string, font: string, size: number) => number;

let context: OffscreenCanvasRenderingContext2D | null | undefined;
const widths = new Map<string, number>();
const MAX_CACHED = 5000;

/**
 * The width of `text` in `font` (sigma's "weight family" form) at `size`
 * pixels: measured on an offscreen canvas, or estimated where there is none
 * (tests).
 */
export const measureText: MeasureText = (text, font, size) => {
  const key = `${font}|${String(size)}|${text}`;
  const cached = widths.get(key);
  if (cached !== undefined) return cached;
  if (context === undefined) {
    context =
      typeof OffscreenCanvas === "undefined" ? null : new OffscreenCanvas(1, 1).getContext("2d");
  }
  let width: number;
  if (context) {
    const [weight, ...family] = font.split(" ");
    context.font = `${weight ?? "400"} ${String(size)}px ${family.join(" ") || LABEL_FAMILY}`;
    width = context.measureText(text).width;
  } else {
    width = Array.from(text).length * size * GLYPH_WIDTH;
  }
  if (widths.size >= MAX_CACHED) widths.clear();
  widths.set(key, width);
  return width;
};
