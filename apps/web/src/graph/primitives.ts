/**
 * The WebGL programs sigma draws the graph with (see ./encoding for what
 * each variable means):
 *
 * - **Knots**: one circle per node, painted by a small fragment layer of
 *   concentric bands, from the outside in: the (dashed) selection halo, the
 *   pinned ring, the type ring, a second ring for groups, the
 *   surface-colored face and the center dot. Every width and color is a
 *   per-node variable, so a width of 0 leaves a ring out and one program
 *   draws every state. (@sigma/node-border draws any border of 2 px or less
 *   in the previous border's color, which rules out thin rings.)
 * - **Threads**: sigma's built-in curved path (a quadratic Bézier with a
 *   per-edge curvature); parallel and reverse edges between the same pair
 *   fan out by sigma's parallel spread.
 * - **Label attachments**: small canvases drawn into sigma's label atlas
 *   (still WebGL, no DOM per node): the pin glyph left of a pinned entity's
 *   label and the type name under a group's count. One renderer per theme,
 *   so a theme change picks the other one instead of a stale cached image.
 */
import type { LabelAttachmentContext, PrimitivesDeclaration } from "sigma/primitives";
import { pathCurved, type FragmentLayer } from "sigma/rendering";

import { ENTITY_TYPE_INFO, type EntityNode } from "../domain/entities";
import { GRAPH_PALETTES, type GraphPalette } from "../theme/graphPalette";
import { THEMES } from "../theme/tokens";
import {
  attachmentName,
  CURVATURE,
  GROUP_ATTACHMENT,
  HALO_DASH_PX,
  LABEL_FAMILY,
  PIN_ATTACHMENT,
  TRANSPARENT,
} from "./encoding";

/** WebGL's FLOAT and UNSIGNED_BYTE (constants, so usable before any context exists). */
const FLOAT = 0x1406;
const UNSIGNED_BYTE = 0x1401;

const SIZE_VARIABLES = [
  "haloSize",
  "haloGap",
  "pinSize",
  "pinGap",
  "ringSize",
  "innerGap",
  "innerRingSize",
  "faceSize",
] as const;
const COLOR_VARIABLES = ["haloColor", "pinColor", "ringColor", "faceColor", "centerColor"] as const;

/**
 * The knot: concentric bands from the outside in (halo, gap, pinned ring,
 * gap, type ring, a group's gap and second ring, face, center), each with
 * a box-filtered 1 px edge so rings of any width stay crisp. Widths are
 * screen pixels; `faceSize` is the face's share of what lies inside the
 * rings. The halo is dashed. The outer edge is anti-aliased by sigma.
 */
export function knotLayer(): FragmentLayer {
  const parameters = [
    ...SIZE_VARIABLES.map((name) => `float v_${name}`),
    ...COLOR_VARIABLES.map((name) => `vec4 v_${name}`),
  ].join(", ");
  return {
    name: "knot",
    uniforms: [],
    attributes: [
      ...SIZE_VARIABLES.map((name) => ({ name, size: 1 as const, type: FLOAT, source: name })),
      ...COLOR_VARIABLES.map((name) => ({
        name,
        size: 4 as const,
        type: UNSIGNED_BYTE,
        normalized: true,
        source: name,
      })),
    ],
    // `context` is sigma's LayerContext: sdf < 0 inside the shape (UV units),
    // pixelToUV converts screen pixels to UV units, uv is in [-1, 1].
    glsl: /* glsl */ `
float knot_band(float depth, float from, float to) {
  return clamp(depth - from + 0.5, 0.0, 1.0) * clamp(to - depth + 0.5, 0.0, 1.0);
}

vec4 layer_knot(${parameters}) {
  float depth = -context.sdf / context.pixelToUV;
  float radius = context.shapeSize / context.pixelToUV;
  float haloEnd = v_haloSize;
  float pinStart = haloEnd + v_haloGap;
  float pinEnd = pinStart + v_pinSize;
  float ringStart = pinEnd + v_pinGap;
  float ringEnd = ringStart + v_ringSize;
  float innerStart = ringEnd + v_innerGap;
  float innerEnd = innerStart + v_innerRingSize;
  float faceEnd = innerEnd + v_faceSize * max(radius - innerEnd, 0.0);

  float turns = atan(context.uv.y, context.uv.x) / 6.2831853;
  float dashes = max(6.0, floor(6.2831853 * radius / ${HALO_DASH_PX.toFixed(1)}));
  float dashOn = step(0.4, fract(turns * dashes));

  vec4 color = vec4(0.0);
  color += vec4(v_haloColor.rgb, 1.0) * v_haloColor.a * dashOn * knot_band(depth, 0.0, haloEnd);
  color += vec4(v_pinColor.rgb, 1.0) * v_pinColor.a * knot_band(depth, pinStart, pinEnd);
  color += vec4(v_ringColor.rgb, 1.0) * v_ringColor.a * knot_band(depth, ringStart, ringEnd);
  color += vec4(v_faceColor.rgb, 1.0) * v_faceColor.a * knot_band(depth, ringEnd, innerStart);
  color += vec4(v_ringColor.rgb, 1.0) * v_ringColor.a * knot_band(depth, innerStart, innerEnd);
  color += vec4(v_faceColor.rgb, 1.0) * v_faceColor.a * knot_band(depth, innerEnd, faceEnd);
  color += vec4(v_centerColor.rgb, 1.0) * v_centerColor.a * knot_band(depth, faceEnd, 1.0e6);
  if (color.a <= 0.0) return vec4(0.0);
  return vec4(color.rgb / color.a, min(color.a, 1.0));
}
`,
  };
}

const color = (fallback = TRANSPARENT) => ({ type: "color" as const, default: fallback });
const number = (fallback = 0) => ({ type: "number" as const, default: fallback });

/** The per-node variables the knot layer reads (filled in by ./view's reducer). */
export const KNOT_VARIABLES = {
  haloSize: number(),
  haloColor: color(),
  haloGap: number(),
  pinSize: number(),
  pinColor: color(),
  pinGap: number(),
  ringSize: number(),
  ringColor: color(),
  innerGap: number(),
  innerRingSize: number(),
  faceSize: number(),
  faceColor: color(),
  centerColor: color(),
};

// --- label attachments --------------------------------------------------------------

/** A canvas of `width` × `height` CSS pixels, drawn at the device's pixel ratio. */
function hiDpiCanvas(width: number, height: number, pixelRatio: number) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(width * pixelRatio);
  canvas.height = Math.ceil(height * pixelRatio);
  const context = canvas.getContext("2d");
  context?.scale(pixelRatio, pixelRatio);
  return { canvas, context };
}

/** Size of the pin drawn before a pinned entity's label (pixels). */
export const PIN_BOX = 12;

/** A map pin, 12 × 12 px, in brass. */
export function drawPin(palette: GraphPalette, pixelRatio: number): HTMLCanvasElement | null {
  const { canvas, context } = hiDpiCanvas(PIN_BOX, PIN_BOX, pixelRatio);
  if (!context) return null;
  context.fillStyle = palette.selection;
  context.strokeStyle = palette.selection;
  context.lineWidth = 1.5;
  context.lineCap = "round";
  // Head: a disc with a hole; needle: a short line down.
  context.beginPath();
  context.arc(6, 4.5, 3.5, 0, 2 * Math.PI);
  context.arc(6, 4.5, 1.25, 0, 2 * Math.PI, true);
  context.fill();
  context.beginPath();
  context.moveTo(6, 8);
  context.lineTo(6, 11.25);
  context.stroke();
  return canvas;
}

/** The group type tag under a group's count (pixels). */
export const TAG_FONT_SIZE = 11;
export const TAG_PADDING_X = 5;
export const TAG_HEIGHT = 16;

/** The group's type ("URLs") in a small surface-colored tag. */
export function drawGroupTag(
  text: string,
  palette: GraphPalette,
  pixelRatio: number,
): HTMLCanvasElement | null {
  const font = `600 ${String(TAG_FONT_SIZE)}px ${LABEL_FAMILY}, sans-serif`;
  const measure = document.createElement("canvas").getContext("2d");
  if (!measure) return null;
  measure.font = font;
  const width = Math.ceil(measure.measureText(text).width) + 2 * TAG_PADDING_X;
  const { canvas, context } = hiDpiCanvas(width, TAG_HEIGHT, pixelRatio);
  if (!context) return null;
  context.fillStyle = palette.surface;
  context.strokeStyle = palette.edge;
  context.lineWidth = 1;
  context.beginPath();
  context.roundRect(0.5, 0.5, width - 1, TAG_HEIGHT - 1, 4);
  context.fill();
  context.stroke();
  context.fillStyle = palette.label;
  context.font = font;
  context.textBaseline = "middle";
  context.fillText(text, TAG_PADDING_X, TAG_HEIGHT / 2 + 0.5);
  return canvas;
}

function pinRenderer(palette: GraphPalette) {
  return ({ pixelRatio }: LabelAttachmentContext) => {
    const canvas = drawPin(palette, pixelRatio);
    return canvas ? { type: "canvas" as const, canvas } : null;
  };
}

function groupRenderer(palette: GraphPalette) {
  return ({ attributes, pixelRatio }: LabelAttachmentContext) => {
    const entity = attributes["entity"] as EntityNode | undefined;
    if (!entity) return null;
    const canvas = drawGroupTag(ENTITY_TYPE_INFO[entity.type].plural, palette, pixelRatio);
    return canvas ? { type: "canvas" as const, canvas } : null;
  };
}

function labelAttachments() {
  return Object.fromEntries(
    THEMES.flatMap((theme) => [
      [attachmentName(PIN_ATTACHMENT, theme), pinRenderer(GRAPH_PALETTES[theme])],
      [attachmentName(GROUP_ATTACHMENT, theme), groupRenderer(GRAPH_PALETTES[theme])],
    ]),
  );
}

/**
 * The primitives to give sigma. Built on demand: sigma's layer and path
 * helpers read WebGL constants, which exist only in a browser.
 */
export function graphPrimitives() {
  return {
    nodes: {
      layers: [knotLayer()],
      variables: KNOT_VARIABLES,
      labelAttachments: labelAttachments(),
    },
    edges: {
      paths: [pathCurved()],
      variables: { curvature: number(CURVATURE) },
    },
  } satisfies PrimitivesDeclaration;
}

export type GraphPrimitives = ReturnType<typeof graphPrimitives>;
