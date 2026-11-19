import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GRAPH_PALETTES } from "../theme/graphPalette";
import { CURVATURE } from "./encoding";
import { drawGroupTag, drawPin, graphPrimitives, KNOT_VARIABLES, knotLayer } from "./primitives";

/** A 2D context that records what is drawn (jsdom has no canvas). */
function recordingContext() {
  const calls: string[] = [];
  const state: Record<string, unknown> = { calls, fillStyle: "", strokeStyle: "", font: "" };
  const context = new Proxy(state, {
    get(target, key: string) {
      if (key in target) return target[key];
      if (key === "measureText") return (text: string) => ({ width: text.length * 6 });
      return (...args: unknown[]) => {
        calls.push(`${key}(${args.map(String).join(",")})`);
      };
    },
    set(target, key: string, value: unknown) {
      target[key] = value;
      calls.push(`${key}=${String(value)}`);
      return true;
    },
  });
  return { context, calls };
}

let drawn: string[];

beforeEach(() => {
  drawn = [];
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => {
    const { context, calls } = recordingContext();
    drawn = calls;
    return context as unknown as CanvasRenderingContext2D;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the knot program", () => {
  it("paints concentric rings from per-node variables", () => {
    const layer = knotLayer();
    // Halo, pinned ring, type ring, gap, inner ring, face and center: seven bands.
    expect(layer.glsl.match(/knot_band\(depth,/g)).toHaveLength(7);
    // The layer function takes every variable, in the order of its attributes.
    const sources = layer.attributes.map((attribute) => attribute.source);
    expect(sources.sort()).toEqual(Object.keys(KNOT_VARIABLES).sort());
    for (const attribute of layer.attributes) {
      expect(layer.glsl).toContain(`v_${attribute.name}`);
    }
  });

  it("declares curved threads and the knot's variables", () => {
    const primitives = graphPrimitives();
    expect(primitives.edges.paths.map((path) => path.name)).toEqual(["curved"]);
    expect(primitives.edges.variables.curvature.default).toBe(CURVATURE);
    expect(primitives.nodes.variables).toBe(KNOT_VARIABLES);
    expect(Object.keys(primitives.nodes.labelAttachments).sort()).toEqual([
      "groupType:dark",
      "groupType:light",
      "pin:dark",
      "pin:light",
    ]);
  });
});

describe("label attachments", () => {
  it("draw the pin in the theme's brass at the device pixel ratio", () => {
    const canvas = drawPin(GRAPH_PALETTES.light, 2);
    expect(canvas?.width).toBe(24);
    expect(drawn).toContain(`fillStyle=${GRAPH_PALETTES.light.selection}`);
    expect(drawn).toContain("scale(2,2)");
  });

  it("draw a group's type name in a surface tag", () => {
    const canvas = drawGroupTag("URLs", GRAPH_PALETTES.dark, 1);
    expect(canvas?.height).toBe(16);
    expect(drawn).toContain(`fillStyle=${GRAPH_PALETTES.dark.surface}`);
    expect(drawn.some((call) => call.startsWith("fillText(URLs"))).toBe(true);
  });

  it("render through sigma's attachment renderers", () => {
    const { labelAttachments } = graphPrimitives().nodes;
    const context = { node: "n", pixelRatio: 1, labelWidth: 10, labelHeight: 10 };
    const pin = labelAttachments["pin:dark"]?.({ ...context, attributes: {} });
    expect(pin).toMatchObject({ type: "canvas" });
    const tag = labelAttachments["groupType:light"]?.({
      ...context,
      attributes: { entity: { type: "url" } },
    });
    expect(tag).toMatchObject({ type: "canvas" });
    expect(drawn.some((call) => call.startsWith("fillText(URLs"))).toBe(true);
    expect(labelAttachments["groupType:light"]?.({ ...context, attributes: {} })).toBeNull();
  });

  it("draw nothing without a 2D context", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    expect(drawPin(GRAPH_PALETTES.dark, 1)).toBeNull();
    expect(drawGroupTag("URLs", GRAPH_PALETTES.dark, 1)).toBeNull();
    const { labelAttachments } = graphPrimitives().nodes;
    const context = { node: "n", pixelRatio: 1, labelWidth: 10, labelHeight: 10 };
    expect(labelAttachments["pin:dark"]?.({ ...context, attributes: {} })).toBeNull();
    expect(
      labelAttachments["groupType:dark"]?.({
        ...context,
        attributes: { entity: { type: "url" } },
      }),
    ).toBeNull();
  });
});
