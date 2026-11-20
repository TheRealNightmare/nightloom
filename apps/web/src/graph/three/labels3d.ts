/**
 * Which knots get a label in the 3D view, each frame: the hovered, selected
 * and focused ones always, then the best-connected knots on screen, as long
 * as their label overlaps no label already placed, up to {@link MAX_LABELS}.
 * Labels are HTML elements over the canvas (see GraphCanvas3D); this module
 * only decides, so it is unit-tested and its cost is measured.
 */
import type { Viewport } from "./projection";

/** At most this many labels besides the hovered / selected / focused ones. */
export const MAX_LABELS = 40;
/** Label text size and line box (pixels; the label layer's CSS uses the same). */
export const LABEL_FONT_PX = 12;
export const LABEL_HEIGHT = 16;
/** Average glyph width as a share of the font size (Source Sans 3). */
export const GLYPH_WIDTH = 0.55;
/** Space between a knot and its label, and kept free around each label. */
export const LABEL_GAP = 4;
/** Labels farther than this past the screen's edge are not placed. */
const OFFSCREEN_MARGIN = 0;

export interface LabelCandidate {
  index: number;
  x: number;
  y: number;
  /** Distance in front of the camera (≤ 0: behind it, never labelled). */
  depth: number;
  /** Knot radius on screen (pixels). */
  radius: number;
  /** Text length in characters. */
  chars: number;
  degree: number;
  /** Hovered, selected or focused: always labelled. */
  mandatory: boolean;
  /** A group's count is drawn over the knot, not beside it. */
  centered: boolean;
}

export interface LabelBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PlacedLabel {
  index: number;
  box: LabelBox;
}

export function labelWidth(chars: number): number {
  return Math.ceil(chars * LABEL_FONT_PX * GLYPH_WIDTH) + 2;
}

/** Where a label sits: to the right of its knot, or centered on it (group counts). */
export function labelBox(candidate: LabelCandidate): LabelBox {
  const width = labelWidth(candidate.chars);
  if (candidate.centered) {
    return {
      x: candidate.x - width / 2,
      y: candidate.y - LABEL_HEIGHT / 2,
      width,
      height: LABEL_HEIGHT,
    };
  }
  return {
    x: candidate.x + candidate.radius + LABEL_GAP,
    y: candidate.y - LABEL_HEIGHT / 2,
    width,
    height: LABEL_HEIGHT,
  };
}

export function boxesOverlap(a: LabelBox, b: LabelBox, gap = LABEL_GAP): boolean {
  return (
    a.x < b.x + b.width + gap &&
    b.x < a.x + a.width + gap &&
    a.y < b.y + b.height + gap &&
    b.y < a.y + a.height + gap
  );
}

function onScreen(candidate: LabelCandidate, viewport: Viewport): boolean {
  return (
    candidate.depth > 0 &&
    candidate.x >= -OFFSCREEN_MARGIN &&
    candidate.y >= -OFFSCREEN_MARGIN &&
    candidate.x <= viewport.width + OFFSCREEN_MARGIN &&
    candidate.y <= viewport.height + OFFSCREEN_MARGIN
  );
}

/** Grid cells for the overlap test (pixels): each placed box is checked only against its neighbours. */
const CELL = 64;

class BoxGrid {
  private readonly cells = new Map<number, LabelBox[]>();

  private key(cx: number, cy: number): number {
    return cx * 100_003 + cy;
  }

  private range(box: LabelBox): [number, number, number, number] {
    return [
      Math.floor((box.x - LABEL_GAP) / CELL),
      Math.floor((box.y - LABEL_GAP) / CELL),
      Math.floor((box.x + box.width + LABEL_GAP) / CELL),
      Math.floor((box.y + box.height + LABEL_GAP) / CELL),
    ];
  }

  overlaps(box: LabelBox): boolean {
    const [x0, y0, x1, y1] = this.range(box);
    for (let cx = x0; cx <= x1; cx += 1) {
      for (let cy = y0; cy <= y1; cy += 1) {
        for (const other of this.cells.get(this.key(cx, cy)) ?? []) {
          if (boxesOverlap(box, other)) return true;
        }
      }
    }
    return false;
  }

  add(box: LabelBox): void {
    const [x0, y0, x1, y1] = this.range(box);
    for (let cx = x0; cx <= x1; cx += 1) {
      for (let cy = y0; cy <= y1; cy += 1) {
        const key = this.key(cx, cy);
        const list = this.cells.get(key);
        if (list) list.push(box);
        else this.cells.set(key, [box]);
      }
    }
  }
}

/**
 * The labels to draw: every mandatory candidate on screen, then the others
 * by degree (nearer first on ties), skipping any that would overlap a label
 * already placed, until `max` of them are placed. Off-screen and behind-the
 * camera candidates are culled first.
 */
export function selectLabels(
  candidates: readonly LabelCandidate[],
  viewport: Viewport,
  max = MAX_LABELS,
): PlacedLabel[] {
  const grid = new BoxGrid();
  const placed: PlacedLabel[] = [];
  const optional: LabelCandidate[] = [];
  for (const candidate of candidates) {
    if (!onScreen(candidate, viewport)) continue;
    if (candidate.mandatory) {
      const box = labelBox(candidate);
      grid.add(box);
      placed.push({ index: candidate.index, box });
    } else {
      optional.push(candidate);
    }
  }
  optional.sort((a, b) => b.degree - a.degree || a.depth - b.depth || a.index - b.index);
  let count = 0;
  for (const candidate of optional) {
    if (count >= max) break;
    const box = labelBox(candidate);
    if (grid.overlaps(box)) continue;
    grid.add(box);
    placed.push({ index: candidate.index, box });
    count += 1;
  }
  return placed;
}
