/**
 * The chrome around the graph canvas: zoom buttons, the 2D / 3D placeholder,
 * and the canvas following the light/dark theme (colors only).
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DemoPage } from "../demo/DemoPage";
import { sampleGraph } from "../demo/sampleGraph";
import { FakeSigma } from "../test/sigmaMock";
import { GRAPH_PALETTES } from "../theme/graphPalette";
import { ThemeProvider } from "../theme/ThemeProvider";
import { ThemeToggle } from "../theme/ThemeToggle";
import type { NightloomGraph } from "./buildGraph";

vi.mock("sigma", () => import("../test/sigmaMock"));

function renderDemo() {
  render(
    <ThemeProvider>
      <ThemeToggle />
      <DemoPage />
    </ThemeProvider>,
  );
  const sigma = FakeSigma.latest();
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
  it("zooms in, out and back to the whole graph", async () => {
    const { sigma } = renderDemo();
    const zoom = screen.getByRole("group", { name: "Zoom" });
    expect(zoom).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(sigma.camera.zoomIn).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(sigma.camera.zoomOut).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(sigma.camera.reset).toHaveBeenCalledTimes(1);
  });

  it("offers only the 2D view for now", async () => {
    renderDemo();
    const view = screen.getByRole("group", { name: "View" });
    const twoD = screen.getByRole("button", { name: "2D" });
    const threeD = screen.getByRole("button", { name: "3D" });
    expect(view).toContainElement(twoD);
    expect(twoD).toHaveAttribute("aria-pressed", "true");
    expect(threeD).toHaveAttribute("aria-pressed", "false");
    expect(threeD).toHaveAttribute("aria-disabled", "true");
    expect(threeD).toHaveAttribute("title", "3D view coming soon");
    await userEvent.click(threeD);
    expect(threeD).toHaveAttribute("aria-pressed", "false");
  });

  it("repaints the canvas in the other theme's colors, without moving anything", async () => {
    const { sigma, graph } = renderDemo();
    const node = sampleGraph.nodes[0];
    if (!node) throw new Error("empty sample");
    const before = graph.getNodeAttributes(node.id);
    expect(before.color).toBe(GRAPH_PALETTES.dark.types[node.type]);
    const labelColor = (sigma.options as { styles: { nodes: { labelColor: () => string } } }).styles
      .nodes.labelColor;
    expect(labelColor()).toBe(GRAPH_PALETTES.dark.label);
    const refreshes = sigma.refresh.mock.calls.length;

    await userEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    const after = graph.getNodeAttributes(node.id);
    expect(after.color).toBe(GRAPH_PALETTES.light.types[node.type]);
    expect([after.x, after.y, after.size]).toEqual([before.x, before.y, before.size]);
    const edge = sampleGraph.edges[0];
    if (edge) expect(graph.getEdgeAttribute(edge.id, "color")).toBe(GRAPH_PALETTES.light.edge);
    expect(labelColor()).toBe(GRAPH_PALETTES.light.label);
    expect(sigma.refresh.mock.calls.length).toBeGreaterThan(refreshes);
    // The same sigma instance: the camera is kept.
    expect(FakeSigma.latest()).toBe(sigma);

    // Dimming blends toward the light background.
    act(() => {
      sigma.emit("enterNode", { node: node.id });
    });
    const far = graph.filterNodes((n) => n !== node.id && !graph.areNeighbors(n, node.id))[0];
    if (far) expect(sigma.nodeView(far)["color"]).not.toBe(graph.getNodeAttribute(far, "color"));
  });
});
