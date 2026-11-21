/**
 * Which labels the 2D canvas draws, through GraphView with sigma mocked (see
 * src/test/sigmaMock.ts): after each render the canvas places the labels
 * (./labelLayout) and its node reducer forces exactly those, so the drawn
 * labels are the nodes the reducer shows a label for.
 */
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sampleGraph } from "../demo/sampleGraph";
import { FakeSigma, waitForSigma } from "../test/sigmaMock";
import { ThemeProvider } from "../theme/ThemeProvider";
import type { NightloomGraph } from "./buildGraph";
import { LABEL_FONT, LABEL_SIZE } from "./encoding";
import { GraphView } from "./GraphView";
import { boxesIntersect, labelBox, measureText, type LabelBox } from "./labelLayout";

vi.mock("sigma", () => import("../test/sigmaMock"));

const WWW = "sub:www.example.org";
const API = "sub:api.example.org";
const MAIL = "sub:mail.example.org";

afterEach(() => {
  FakeSigma.reset();
});

async function renderDense() {
  render(
    <ThemeProvider>
      <GraphView data={sampleGraph} overview={() => <p>Overview</p>} />
    </ThemeProvider>,
  );
  const sigma = await waitForSigma();
  sigma.displayFromGraph = true;
  const graph = sigma.graph as unknown as NightloomGraph;
  // Pack every entity into a small patch, three example hosts on one line.
  act(() => {
    let i = 0;
    graph.forEachNode((node) => {
      graph.mergeNodeAttributes(node, { x: (i * 37) % 160, y: (i * 23) % 90 });
      i += 1;
    });
    graph.mergeNodeAttributes(WWW, { x: 0, y: 40 });
    graph.mergeNodeAttributes(API, { x: 30, y: 40 });
    graph.mergeNodeAttributes(MAIL, { x: 60, y: 41 });
    sigma.camera.setState({ x: 80, y: 45, ratio: 0.5, angle: 0 });
  });
  return { sigma, graph };
}

/** A render: the canvas places the labels afterwards. */
function render2d(sigma: FakeSigma) {
  act(() => {
    sigma.renderFrame();
  });
}

/** The labels drawn, with their boxes on screen (worked out from what the reducer gives sigma). */
function drawnLabels(sigma: FakeSigma, graph: NightloomGraph): Map<string, LabelBox> {
  const drawn = new Map<string, LabelBox>();
  graph.forEachNode((node, attributes) => {
    const view = sigma.nodeView(node);
    if (view["labelVisibility"] !== "visible" || view["visibility"] === "hidden") return;
    const label = view["label"] as string;
    const size = (view["labelSize"] as number | undefined) ?? LABEL_SIZE;
    const point = sigma.screenOf(node);
    drawn.set(
      node,
      labelBox({
        node,
        x: point.x,
        y: point.y,
        radius: view["size"] as number,
        textWidth: measureText(
          label,
          (view["labelFont"] as string | undefined) ?? LABEL_FONT,
          size,
        ),
        textHeight: size,
        position: view["labelPosition"] === "over" ? "over" : "right",
        before: attributes.pinned === true ? 12 : 0,
        mandatory: false,
        priority: 0,
      }),
    );
  });
  return drawn;
}

function overlapping(drawn: Map<string, LabelBox>, exempt: readonly string[] = []): string[] {
  const pairs: string[] = [];
  const entries = [...drawn];
  for (let i = 0; i < entries.length; i += 1) {
    for (let j = i + 1; j < entries.length; j += 1) {
      const [a, boxA] = entries[i] ?? [];
      const [b, boxB] = entries[j] ?? [];
      if (!a || !b || !boxA || !boxB) continue;
      if (exempt.includes(a) && exempt.includes(b)) continue;
      if (boxesIntersect(boxA, boxB)) pairs.push(`${a} / ${b}`);
    }
  }
  return pairs;
}

describe("2D labels", () => {
  it("draws no two labels over each other in a dense graph", async () => {
    const { sigma, graph } = await renderDense();
    render2d(sigma);
    const drawn = drawnLabels(sigma, graph);
    expect(drawn.size).toBeGreaterThan(2);
    expect(drawn.size).toBeLessThan(graph.order);
    expect(overlapping(drawn)).toEqual([]);
    // Nor over another entity's knot.
    const covered: string[] = [];
    for (const [node, box] of drawn) {
      graph.forEachNode((other) => {
        if (other === node) return;
        const { x, y } = sigma.screenOf(other);
        const radius = sigma.nodeView(other)["size"] as number;
        const knot = { x: x - radius, y: y - radius, width: 2 * radius, height: 2 * radius };
        if (boxesIntersect(box, knot)) covered.push(`${node} over ${other}`);
      });
    }
    expect(covered).toEqual([]);
    // The three hosts on one line no longer overlap: not all three are drawn.
    expect([WWW, API, MAIL].filter((node) => drawn.has(node)).length).toBeLessThan(3);
  });

  it("always labels the hovered and the selected entity, keeping the others off them", async () => {
    const { sigma, graph } = await renderDense();
    act(() => {
      sigma.emit("clickNode", { node: API });
      sigma.emit("enterNode", { node: MAIL });
    });
    render2d(sigma);
    const drawn = drawnLabels(sigma, graph);
    expect(drawn.has(API)).toBe(true);
    expect(drawn.has(MAIL)).toBe(true);
    expect(overlapping(drawn, [API, MAIL])).toEqual([]);
  });

  it("follows the camera: zoomed in, more labels fit", async () => {
    const { sigma, graph } = await renderDense();
    render2d(sigma);
    const before = drawnLabels(sigma, graph).size;
    act(() => {
      sigma.camera.setState({ ratio: 0.1 });
    });
    render2d(sigma);
    const after = drawnLabels(sigma, graph);
    expect(after.size).toBeGreaterThan(before);
    expect(overlapping(after)).toEqual([]);
  });
});
