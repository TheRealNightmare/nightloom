/**
 * Which knots get a label in the 3D view, and on which side, each frame: the
 * hovered, selected and focused ones always, then the best-connected knots
 * on screen, up to {@link MAX_LABELS}. Each label tries the four sides of its
 * knot (right, left, below, above) and takes the first where it overlaps no
 * label already placed and no other knot; a label with no free side is not
 * drawn (a mandatory one then sits on the right anyway).
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

/** The sides a label tries, in order. */
export const LABEL_SIDES = ["right", "left", "below", "above"] as const;
export type LabelSide = (typeof LABEL_SIDES)[number];

/**
 * Where a label sits on `side` of its knot (right unless given), or centered
 * on it (group counts, whatever the side).
 */
export function labelBox(candidate: LabelCandidate, side: LabelSide = "right"): LabelBox {
  const width = labelWidth(candidate.chars);
  const { x, y, radius } = candidate;
  if (candidate.centered) {
    return { x: x - width / 2, y: y - LABEL_HEIGHT / 2, width, height: LABEL_HEIGHT };
  }
  switch (side) {
    case "right":
      return { x: x + radius + LABEL_GAP, y: y - LABEL_HEIGHT / 2, width, height: LABEL_HEIGHT };
    case "left":
      return {
        x: x - radius - LABEL_GAP - width,
        y: y - LABEL_HEIGHT / 2,
        width,
        height: LABEL_HEIGHT,
      };
    case "below":
      return { x: x - width / 2, y: y + radius + LABEL_GAP, width, height: LABEL_HEIGHT };
    case "above":
      return {
        x: x - width / 2,
        y: y - radius - LABEL_GAP - LABEL_HEIGHT,
        width,
        height: LABEL_HEIGHT,
      };
  }
}

/** A knot on screen that labels keep clear of. */
export interface KnotMark {
  x: number;
  y: number;
  radius: number;
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

/** Grid cells for the overlap test (pixels): each box is checked only against its neighbours. */
const CELL = 64;

/** The placed labels, by grid cell over the viewport (boxes past its edge go in the edge cells). */
class BoxGrid {
  private readonly cols: number;
  private readonly rows: number;
  private readonly cells: (LabelBox[] | undefined)[];

  constructor(viewport: Viewport) {
    this.cols = Math.max(1, Math.ceil(viewport.width / CELL));
    this.rows = Math.max(1, Math.ceil(viewport.height / CELL));
    this.cells = new Array<LabelBox[] | undefined>(this.cols * this.rows);
  }

  private column(x: number): number {
    return Math.min(Math.max(Math.floor(x / CELL), 0), this.cols - 1);
  }

  private row(y: number): number {
    return Math.min(Math.max(Math.floor(y / CELL), 0), this.rows - 1);
  }

  overlaps(box: LabelBox): boolean {
    const c1 = this.column(box.x + box.width + LABEL_GAP);
    const r1 = this.row(box.y + box.height + LABEL_GAP);
    for (let row = this.row(box.y - LABEL_GAP); row <= r1; row += 1) {
      for (let col = this.column(box.x - LABEL_GAP); col <= c1; col += 1) {
        const list = this.cells[row * this.cols + col];
        if (!list) continue;
        for (const other of list) {
          if (boxesOverlap(box, other)) return true;
        }
      }
    }
    return false;
  }

  add(box: LabelBox): void {
    const c1 = this.column(box.x + box.width + LABEL_GAP);
    const r1 = this.row(box.y + box.height + LABEL_GAP);
    for (let row = this.row(box.y - LABEL_GAP); row <= r1; row += 1) {
      for (let col = this.column(box.x - LABEL_GAP); col <= c1; col += 1) {
        const cell = row * this.cols + col;
        const list = this.cells[cell];
        if (list) list.push(box);
        else this.cells[cell] = [box];
      }
    }
  }
}

/** The knot map's cells (pixels): a label keeps clear of knots to about this. */
const KNOT_CELL = 4;
/** Reused between frames (grown when the viewport needs more cells). */
let knotCells = new Uint8Array(0);

/**
 * Where the knots are on screen: a coarse bitmap of {@link KNOT_CELL}-pixel
 * cells, a cell set when its center lies on a knot's disc. Drawn once a
 * frame; a label box then tests the cells whose centers it covers. A box
 * beside its own knot (LABEL_GAP past its disc) covers none of that knot's
 * cells.
 */
class KnotMap {
  private readonly cols: number;
  private readonly rows: number;
  private readonly cells: Uint8Array;

  constructor(knots: readonly KnotMark[], viewport: Viewport) {
    const cols = Math.max(1, Math.ceil(viewport.width / KNOT_CELL));
    const rows = Math.max(1, Math.ceil(viewport.height / KNOT_CELL));
    if (knotCells.length < cols * rows) knotCells = new Uint8Array(cols * rows);
    const cells = knotCells;
    cells.fill(0, 0, cols * rows);
    for (const knot of knots) {
      const r = knot.radius;
      if (!(r > 0)) continue;
      const c0 = Math.max(0, Math.ceil((knot.x - r) / KNOT_CELL - 0.5));
      const c1 = Math.min(cols - 1, Math.floor((knot.x + r) / KNOT_CELL - 0.5));
      const r0 = Math.max(0, Math.ceil((knot.y - r) / KNOT_CELL - 0.5));
      const r1 = Math.min(rows - 1, Math.floor((knot.y + r) / KNOT_CELL - 0.5));
      for (let row = r0; row <= r1; row += 1) {
        // The run of cells on this row whose centers lie on the disc.
        const dy = (row + 0.5) * KNOT_CELL - knot.y;
        const span = Math.sqrt(Math.max(r * r - dy * dy, 0));
        const from = Math.max(c0, Math.ceil((knot.x - span) / KNOT_CELL - 0.5));
        const to = Math.min(c1, Math.floor((knot.x + span) / KNOT_CELL - 0.5));
        if (from <= to) cells.fill(1, row * cols + from, row * cols + to + 1);
      }
    }
    this.cols = cols;
    this.rows = rows;
    this.cells = cells;
  }

  /** Whether the box at (`x`, `y`), `width` × `height`, covers the center of a cell on a knot. */
  hits(x: number, y: number, width: number, height: number): boolean {
    const c0 = Math.max(0, Math.ceil(x / KNOT_CELL - 0.5));
    const c1 = Math.min(this.cols - 1, Math.floor((x + width) / KNOT_CELL - 0.5));
    const r0 = Math.max(0, Math.ceil(y / KNOT_CELL - 0.5));
    const r1 = Math.min(this.rows - 1, Math.floor((y + height) / KNOT_CELL - 0.5));
    const cells = this.cells;
    for (let row = r0; row <= r1; row += 1) {
      const end = row * this.cols + c1;
      for (let at = row * this.cols + c0; at <= end; at += 1) {
        if (cells[at] === 1) return true;
      }
    }
    return false;
  }
}

/**
 * The labels to draw: every mandatory candidate on screen, then the others
 * by degree (nearer first on ties), until `max` of them are placed. Each
 * label takes the first of {@link LABEL_SIDES} where it stays on screen and
 * overlaps no label already placed and no knot of `knots` (the candidates
 * themselves when `knots` is not given; a label never overlaps its own); an optional label with
 * no such side is skipped, a mandatory one goes on the right. Off-screen and
 * behind-the-camera candidates are culled first.
 */
export function selectLabels(
  candidates: readonly LabelCandidate[],
  viewport: Viewport,
  max = MAX_LABELS,
  knots?: readonly KnotMark[],
): PlacedLabel[] {
  const labels = new BoxGrid(viewport);
  const knotMap = new KnotMap(
    knots ?? candidates.filter((candidate) => onScreen(candidate, viewport)),
    viewport,
  );

  // A box being tried (no allocation per side; copied once it is taken).
  const trial: LabelBox = { x: 0, y: 0, width: 0, height: LABEL_HEIGHT };
  const freeBox = (candidate: LabelCandidate): LabelBox | null => {
    if (candidate.centered) {
      const box = labelBox(candidate);
      return labels.overlaps(box) ? null : box;
    }
    const width = labelWidth(candidate.chars);
    const { x, y, radius } = candidate;
    const off = radius + LABEL_GAP;
    trial.width = width;
    for (let side = 0; side < LABEL_SIDES.length; side += 1) {
      // right, left, below, above (as labelBox places them).
      trial.x = side === 0 ? x + off : side === 1 ? x - off - width : x - width / 2;
      trial.y = side < 2 ? y - LABEL_HEIGHT / 2 : side === 2 ? y + off : y - off - LABEL_HEIGHT;
      if (
        trial.x < 0 ||
        trial.y < 0 ||
        trial.x + width > viewport.width ||
        trial.y + LABEL_HEIGHT > viewport.height
      ) {
        continue;
      }
      if (knotMap.hits(trial.x, trial.y, width, LABEL_HEIGHT)) continue;
      if (labels.overlaps(trial)) continue;
      return { ...trial };
    }
    return null;
  };

  const placed: PlacedLabel[] = [];
  const optional: LabelCandidate[] = [];
  for (const candidate of candidates) {
    if (!onScreen(candidate, viewport)) continue;
    if (candidate.mandatory) {
      const box = freeBox(candidate) ?? labelBox(candidate);
      labels.add(box);
      placed.push({ index: candidate.index, box });
    } else {
      optional.push(candidate);
    }
  }
  optional.sort((a, b) => b.degree - a.degree || a.depth - b.depth || a.index - b.index);
  let count = 0;
  for (const candidate of optional) {
    if (count >= max) break;
    const box = freeBox(candidate);
    if (box === null) continue;
    labels.add(box);
    placed.push({ index: candidate.index, box });
    count += 1;
  }
  return placed;
}
