/**
 * The chrome around the graph canvas: zoom buttons, the 2D / 3D switch,
 * and the canvas following the light/dark theme (colors only).
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DemoPage } from "../demo/DemoPage";
import { sampleGraph } from "../demo/sampleGraph";
import { FakeSigma, waitForSigma } from "../test/sigmaMock";
import { GRAPH_PALETTES } from "../theme/graphPalette";
import { ThemeProvider } from "../theme/ThemeProvider";
import { ThemeToggle } from "../theme/ThemeToggle";
import type { NightloomGraph } from "./buildGraph";
import { NO_WEBGL } from "./GraphView";
import { MODE_STORAGE_KEY } from "./mode";

vi.mock("sigma", () => import("../test/sigmaMock"));

async function renderDemo() {
  render(
    <ThemeProvider>
      <ThemeToggle />
      <DemoPage />
    </ThemeProvider>,
  );
  const sigma = await waitForSigma();
  return { sigma, graph: sigma.graph as unknown as NightloomGraph };
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.setAttribute("data-theme", "dark");
});

afterEach(() => {
  FakeSigma.reset();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("graph chrome", () => {
  it("zooms in, out, rotates and goes back to the whole graph, unrotated", async () => {
    const { sigma } = await renderDemo();
    const zoom = screen.getByRole("group", { name: "Zoom and rotation" });
    expect(zoom).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(sigma.camera.ratio).toBeCloseTo(1 / 1.5);
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(sigma.camera.ratio).toBeCloseTo(1);
    await userEvent.click(screen.getByRole("button", { name: "Rotate right" }));
    expect(sigma.camera.angle).toBeCloseTo(Math.PI / 12);
    await userEvent.click(screen.getByRole("button", { name: "Rotate left" }));
    await userEvent.click(screen.getByRole("button", { name: "Rotate left" }));
    expect(sigma.camera.angle).toBeCloseTo(-Math.PI / 12);
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(sigma.camera.reset).toHaveBeenCalledTimes(1);
    expect(sigma.camera.angle).toBe(0);
  });

  it("switches to 3D, and stays in 2D with a notice where WebGL is missing", async () => {
    // jsdom has no WebGL: the 3D view falls back to 2D.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    await renderDemo();
    const view = screen.getByRole("group", { name: "View" });
    const twoD = screen.getByRole("button", { name: "2D" });
    const threeD = screen.getByRole("button", { name: "3D" });
    expect(view).toContainElement(twoD);
    expect(twoD).toHaveAttribute("aria-pressed", "true");
    expect(threeD).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(threeD);
    expect(threeD).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem(MODE_STORAGE_KEY)).toBe("3d");
    expect(screen.getByText(NO_WEBGL)).toBeInTheDocument();
    expect(screen.getByTestId("graph-canvas")).not.toHaveAttribute("data-mode", "3d");
    // No depth choice without a 3D view.
    expect(screen.queryByRole("group", { name: "Depth" })).not.toBeInTheDocument();

    await userEvent.click(twoD);
    expect(screen.queryByText(NO_WEBGL)).not.toBeInTheDocument();
    expect(window.localStorage.getItem(MODE_STORAGE_KEY)).toBe("2d");
  });

  it("repaints the canvas in the other theme's colors, without moving anything", async () => {
    const { sigma, graph } = await renderDemo();
    const node = sampleGraph.nodes[0];
    const edge = sampleGraph.edges[0];
    if (!node || !edge) throw new Error("empty sample");
    const before = graph.getNodeAttributes(node.id);
    expect(before.color).toBe(GRAPH_PALETTES.dark.types[node.type]);
    expect(sigma.nodeView(node.id)).toMatchObject({
      ringColor: GRAPH_PALETTES.dark.types[node.type],
      faceColor: GRAPH_PALETTES.dark.surface,
      labelColor: GRAPH_PALETTES.dark.label,
    });
    expect(sigma.edgeView(edge.id)["color"]).toBe(GRAPH_PALETTES.dark.edge);
    const refreshes = sigma.refresh.mock.calls.length;

    await userEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    const after = graph.getNodeAttributes(node.id);
    expect(after.color).toBe(GRAPH_PALETTES.light.types[node.type]);
    expect([after.x, after.y, after.size]).toEqual([before.x, before.y, before.size]);
    expect(graph.getEdgeAttribute(edge.id, "color")).toBe(GRAPH_PALETTES.light.edge);
    expect(sigma.nodeView(node.id)).toMatchObject({
      ringColor: GRAPH_PALETTES.light.types[node.type],
      faceColor: GRAPH_PALETTES.light.surface,
      labelColor: GRAPH_PALETTES.light.label,
    });
    expect(sigma.edgeView(edge.id)["color"]).toBe(GRAPH_PALETTES.light.edge);
    expect(sigma.refresh.mock.calls.length).toBeGreaterThan(refreshes);
    // The same sigma instance: the camera is kept.
    expect(FakeSigma.latest()).toBe(sigma);

    // The selection is brass in the light theme too, and the halo follows.
    act(() => {
      sigma.emit("clickNode", { node: node.id });
    });
    expect(sigma.nodeView(node.id)).toMatchObject({
      haloColor: GRAPH_PALETTES.light.selection,
      labelColor: GRAPH_PALETTES.light.selectionText,
    });
  });

  it("widens threads as the camera zooms in, repainting only on visible steps", async () => {
    const { sigma } = await renderDemo();
    const edge = sampleGraph.edges[0];
    if (!edge) throw new Error("empty sample");
    expect(sigma.edgeView(edge.id)["size"]).toBeCloseTo(1);
    const refreshes = sigma.refresh.mock.calls.length;
    act(() => {
      sigma.zoomTo(0.99);
    });
    expect(sigma.refresh.mock.calls.length).toBe(refreshes);
    act(() => {
      sigma.zoomTo(0.25);
    });
    expect(sigma.refresh.mock.calls.length).toBe(refreshes + 1);
    // 1.5 px on screen, which sigma scales by 1 / sqrt(ratio).
    expect(sigma.edgeView(edge.id)["size"]).toBeCloseTo(1.5 * Math.sqrt(0.25));
  });

  it("draws with the knot and thread programs", async () => {
    const { sigma } = await renderDemo();
    const { primitives, styles } = sigma.options as {
      primitives: { nodes: { variables: object }; edges: object };
      styles: { edges: { path: string; labelVisibility: string } };
    };
    expect(Object.keys(primitives.nodes.variables)).toContain("ringColor");
    expect(styles.edges).toMatchObject({ path: "curved", labelVisibility: "hidden" });
  });
});
