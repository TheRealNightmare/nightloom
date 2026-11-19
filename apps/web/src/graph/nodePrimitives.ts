/**
 * How sigma draws nodes: its default fill, plus a dashed outline for group
 * nodes (see ./groups). The outline is a WebGL fragment layer: it reads the
 * node's `dashColor` and draws nothing when that is transparent, so ordinary
 * entities look exactly as before.
 */
import type { PrimitivesDeclaration } from "sigma/primitives";
import { layerFill, type FragmentLayer } from "sigma/rendering";

import { NO_DASH } from "./buildGraph";

/** WebGL's UNSIGNED_BYTE (a constant, so it is usable before any context exists). */
const UNSIGNED_BYTE = 0x1401;

/** Outline width and the target length of one dash plus its gap, in screen pixels. */
const DASH_WIDTH_PX = 2.5;
const DASH_PERIOD_PX = 9;

export const dashedOutlineLayer: FragmentLayer = {
  name: "dashedOutline",
  uniforms: [],
  attributes: [
    { name: "dashColor", size: 4, type: UNSIGNED_BYTE, normalized: true, source: "dashColor" },
  ],
  // `context` is sigma's LayerContext: sdf < 0 inside the shape, uv in [-1, 1].
  glsl: /* glsl */ `
vec4 layer_dashedOutline(vec4 v_dashColor) {
  if (v_dashColor.a <= 0.0) return vec4(0.0);
  float inside = -context.sdf;
  if (inside > ${DASH_WIDTH_PX.toFixed(1)} * context.pixelToUV) return vec4(0.0);
  float turns = atan(context.uv.y, context.uv.x) / 6.2831853;
  float dashes = max(8.0, floor(context.pixelSize * 3.14159 / ${DASH_PERIOD_PX.toFixed(1)}));
  float on = step(0.5, fract(turns * dashes));
  return vec4(v_dashColor.rgb, v_dashColor.a * on);
}
`,
};

/**
 * The primitives to give sigma. Built on demand: sigma's layer helpers read
 * WebGL constants, which exist only in a browser.
 */
export function nodePrimitives() {
  return {
    nodes: {
      layers: [layerFill(), dashedOutlineLayer],
      variables: { dashColor: { type: "color" as const, default: NO_DASH } },
    },
  } satisfies PrimitivesDeclaration;
}

export type NodePrimitives = ReturnType<typeof nodePrimitives>;
