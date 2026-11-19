/**
 * The visual encoding of the graph ("Loom & thread"): entities are knots,
 * relationships are threads. Pure functions from data, view state and the
 * theme's palette to what sigma draws, so every visual rule is unit-tested
 * without WebGL. ./view applies them in sigma's reducers; ./primitives
 * declares the WebGL programs that read the variables they produce.
 *
 * | What                        | How it is drawn                                        |
 * | --------------------------- | ------------------------------------------------------ |
 * | entity type                 | the knot's ring and center, in the type's token color  |
 * | connections (degree)        | knot size, within KNOT_MIN_SIZE..KNOT_MAX_SIZE         |
 * | collapsed group             | larger knot, double ring, count in the center          |
 * | low confidence / unconfirmed| thinner thread at LOW_CONFIDENCE_OPACITY               |
 * | selected                    | larger knot, dashed brass halo, brass-text label       |
 * | hovered / selected threads  | brass, 2 px, with the relationship type as label       |
 * | pinned                      | thin brass outer ring and a pin glyph by the label     |
 */
import type { EntityNode, RelationEdge } from "../domain/entities";
import type { GraphPalette } from "../theme/graphPalette";

/** Fully transparent (a ring that takes space but draws nothing). */
export const TRANSPARENT = "transparent";

// --- knots ------------------------------------------------------------------------

/** Smallest and largest entity knot (radius, pixels at the default zoom). */
export const KNOT_MIN_SIZE = 7;
export const KNOT_MAX_SIZE = 24;
/** Group knots: larger than any entity of a similar size, within limits. */
export const GROUP_MIN_SIZE = 20;
export const GROUP_MAX_SIZE = 34;
/** The selected knot is drawn this much larger. */
export const SELECTED_SCALE = 1.2;

/** Ring widths (screen pixels). */
export const RING_WIDTH = 2;
export const GROUP_RING_WIDTH = 1.5;
export const GROUP_RING_GAP = 1.5;
export const HALO_WIDTH = 2;
export const PIN_WIDTH = 1.25;
/** The space between the knot and a halo or pinned ring. */
export const OUTER_GAP = 1.5;
/** Length of one dash plus its gap along the selection halo (pixels). */
export const HALO_DASH_PX = 7;
/**
 * The knot's face (between the ring and the center dot) as a share of what
 * lies inside the rings; the rest is the dot. Relative, so the dot keeps its
 * proportion at every zoom. A group's face fills the knot.
 */
export const FACE_FRACTION = 0.45;
export const GROUP_FACE_FRACTION = 1;

/** Better-connected entities are larger: the square root of the degree, capped. */
export function knotSize(degree: number): number {
  return Math.min(KNOT_MIN_SIZE + Math.sqrt(Math.max(degree, 0)) * 2.2, KNOT_MAX_SIZE);
}

/** A group knot grows with the number of entities it stands for (logarithmically). */
export function groupKnotSize(members: number): number {
  return Math.min(GROUP_MIN_SIZE + Math.log2(Math.max(members, 1)) * 1.5, GROUP_MAX_SIZE);
}

/** The knot's base size (before selection): by group size or by degree. */
export function baseKnotSize(entity: EntityNode, degree: number): number {
  return entity.group ? groupKnotSize(entity.group.members.length) : knotSize(degree);
}

/**
 * The per-node variables of the knot program (see ./primitives): ring
 * widths from the outside in (screen pixels, except `faceSize`, a share of
 * what lies inside the rings) and their colors. A width of 0 leaves that ring out.
 */
export interface KnotVariables {
  haloSize: number;
  haloColor: string;
  haloGap: number;
  pinSize: number;
  pinColor: string;
  pinGap: number;
  ringSize: number;
  ringColor: string;
  innerGap: number;
  innerRingSize: number;
  faceSize: number;
  faceColor: string;
  centerColor: string;
}

export interface KnotState {
  isGroup: boolean;
  selected: boolean;
  pinned: boolean;
}

/** The node's drawn size: rings outside the knot are added, so the knot keeps its size. */
export function knotDisplaySize(base: number, state: KnotState): number {
  let size = state.selected ? base * SELECTED_SCALE : base;
  if (state.selected) size += HALO_WIDTH + OUTER_GAP;
  if (state.pinned) size += PIN_WIDTH + OUTER_GAP;
  return size;
}

/**
 * The knot of an entity of `typeColor`: a surface-colored face, a ring in
 * the type color and a small center in the type color. Groups get a double
 * ring and an empty face (their count is the label, drawn over it).
 */
export function knotVariables(
  typeColor: string,
  state: KnotState,
  palette: GraphPalette,
): KnotVariables {
  const haloSize = state.selected ? HALO_WIDTH : 0;
  const pinSize = state.pinned ? PIN_WIDTH : 0;
  const ring = state.isGroup ? GROUP_RING_WIDTH : RING_WIDTH;
  const innerGap = state.isGroup ? GROUP_RING_GAP : 0;
  const innerRing = state.isGroup ? GROUP_RING_WIDTH : 0;
  return {
    haloSize,
    haloColor: palette.selection,
    haloGap: haloSize ? OUTER_GAP : 0,
    pinSize,
    pinColor: palette.selection,
    pinGap: pinSize ? OUTER_GAP : 0,
    ringSize: ring,
    ringColor: typeColor,
    innerGap,
    innerRingSize: innerRing,
    faceSize: state.isGroup ? GROUP_FACE_FRACTION : FACE_FRACTION,
    faceColor: palette.surface,
    centerColor: state.isGroup ? palette.surface : typeColor,
  };
}

// --- node labels --------------------------------------------------------------------

/** The interface font, as loaded by src/styles/fonts.css (wait for it before drawing). */
export const LABEL_FAMILY = '"Source Sans 3 Variable"';
export const LABEL_FONT = `400 ${LABEL_FAMILY}`;
export const LABEL_FONT_STRONG = `600 ${LABEL_FAMILY}`;
export const LABEL_SIZE = 12;
export const GROUP_COUNT_SIZE = 13;
export const EDGE_LABEL_SIZE = 11;

export interface NodeLabelStyle {
  label: string;
  labelColor: string;
  labelFont: string;
  labelSize: number;
  labelPosition: "right" | "over";
}

/** "312" from 312 members, "1.2k" from 1,234, "35k" from 35,000. */
export function compactCount(count: number): string {
  if (count < 1000) return String(count);
  if (count < 10_000) return `${(Math.floor(count / 100) / 10).toFixed(1).replace(/\.0$/, "")}k`;
  return `${String(Math.floor(count / 1000))}k`;
}

/**
 * An entity's label sits to the right of its knot, in --muted (--brass-text
 * when selected). A group's label is its count, over the center of the knot;
 * the type name goes below it (see GROUP_ATTACHMENT).
 */
export function nodeLabelStyle(
  entity: EntityNode,
  selected: boolean,
  palette: GraphPalette,
): NodeLabelStyle {
  if (entity.group) {
    return {
      label: compactCount(entity.group.members.length),
      labelColor: selected ? palette.selectionText : palette.text,
      labelFont: LABEL_FONT_STRONG,
      labelSize: GROUP_COUNT_SIZE,
      labelPosition: "over",
    };
  }
  return {
    label: entity.label,
    labelColor: selected ? palette.selectionText : palette.label,
    labelFont: selected ? LABEL_FONT_STRONG : LABEL_FONT,
    labelSize: LABEL_SIZE,
    labelPosition: "right",
  };
}

/** Names of the label attachments (./primitives registers one per theme). */
export const PIN_ATTACHMENT = "pin";
export const GROUP_ATTACHMENT = "groupType";

export function attachmentName(kind: string, theme: string): string {
  return `${kind}:${theme}`;
}

// --- threads ------------------------------------------------------------------------

/** Below this confidence a relationship is drawn as a faint, thin thread. */
export const LOW_CONFIDENCE = 0.6;
export const LOW_CONFIDENCE_OPACITY = 0.45;
export const LOW_CONFIDENCE_WIDTH_FACTOR = 0.6;
/** The focused entity's threads; an uncertain one stays a little faint. */
export const FOCUS_THREAD_WIDTH = 2;
export const FOCUS_UNCERTAIN_OPACITY = 0.7;
/** Every thread bends the same way; parallel ones fan out by PARALLEL_SPREAD. */
export const CURVATURE = 0.12;
export const PARALLEL_SPREAD = 0.3;
/** Others dim to this opacity while something is hovered or selected. */
export const DIM_OPACITY = 0.25;

/**
 * Whether the relationship is uncertain: low confidence, or marked as not
 * confirmed (e.g. a PTR record whose name does not resolve back to the
 * address; the DNS Shuttle notes `forward_confirmed: false`).
 */
export function isUncertain(relation: RelationEdge): boolean {
  if (relation.confidence < LOW_CONFIDENCE) return true;
  const note = relation.details?.note ?? "";
  return /\bforward_confirmed:\s*false\b/.test(note);
}

/**
 * A thread's width in pixels at a camera ratio (1 = the whole graph, smaller
 * = zoomed in): 1 px zoomed out, up to 1.5 px zoomed in.
 */
export function threadWidth(cameraRatio: number): number {
  const zoomIn = Math.min(Math.max((1 - cameraRatio) / 0.75, 0), 1);
  return 1 + 0.5 * zoomIn;
}

/**
 * Converts a width in screen pixels to sigma's edge size, which sigma
 * scales by 1 / sqrt(ratio) (its default zoomToSizeRatioFunction).
 */
export function edgeSizeFor(pixels: number, cameraRatio: number): number {
  return pixels * Math.sqrt(Math.max(cameraRatio, 1e-6));
}

/** Rounds the ratio so a zoom only repaints when the thread width visibly changes. */
export function threadWidthStep(cameraRatio: number): number {
  return Math.round(threadWidth(cameraRatio) * 8) / 8;
}

export interface ThreadStyle {
  color: string;
  /** Width in screen pixels. */
  width: number;
  opacity: number;
  curvature: number;
}

/** A thread at rest: --hair, thin and faint when uncertain. */
export function threadStyle(
  uncertain: boolean,
  cameraRatio: number,
  palette: GraphPalette,
): ThreadStyle {
  const width = threadWidth(cameraRatio);
  return {
    color: palette.edge,
    width: uncertain ? width * LOW_CONFIDENCE_WIDTH_FACTOR : width,
    opacity: uncertain ? LOW_CONFIDENCE_OPACITY : 1,
    curvature: CURVATURE,
  };
}

/** A thread of the hovered or selected entity: brass and 2 px (still faint when uncertain). */
export function focusThreadStyle(uncertain: boolean, palette: GraphPalette): ThreadStyle {
  return {
    color: palette.selection,
    width: FOCUS_THREAD_WIDTH,
    opacity: uncertain ? FOCUS_UNCERTAIN_OPACITY : 1,
    curvature: CURVATURE,
  };
}
