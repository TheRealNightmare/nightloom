import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GRAPH_HELP } from "../graph/GraphCanvas";
import { DIM_OPACITY } from "../graph/encoding";
import { FakeSigma, spreadNodes, waitForSigma } from "../test/sigmaMock";
import { DemoPage } from "./DemoPage";

vi.mock("sigma", () => import("../test/sigmaMock"));

async function renderApp() {
  const view = render(<DemoPage />);
  return { ...view, sigma: await waitForSigma() };
}

const details = () => screen.getByRole("complementary", { name: "Details" });

afterEach(() => {
  FakeSigma.reset();
});

describe("DemoPage", () => {
  it("renders the graph and the overview", async () => {
    await renderApp();
    expect(screen.getByTestId("graph-canvas")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Demo investigation" })).toBeVisible();
  });

  it("shows a node's details when it is clicked, and clears them on the stage", async () => {
    const { sigma } = await renderApp();
    act(() => {
      sigma.emit("clickNode", { node: "asn:64496" });
    });
    expect(within(details()).getByRole("heading", { level: 2 })).toHaveTextContent("AS64496");
    expect(sigma.nodeView("asn:64496")).toMatchObject({ highlighted: true });

    act(() => {
      sigma.emit("clickStage");
    });
    expect(screen.getByRole("heading", { name: "Demo investigation" })).toBeVisible();
  });

  it("highlights the hovered node's neighbors and dims the rest", async () => {
    const { sigma } = await renderApp();
    spreadNodes(sigma);
    const refreshes = sigma.refresh.mock.calls.length;
    act(() => {
      sigma.emit("enterNode", { node: "net:203.0.113.0/24" });
    });
    act(() => {
      sigma.renderFrame();
    });
    expect(sigma.refresh.mock.calls.length).toBeGreaterThan(refreshes);
    expect(sigma.nodeView("ip:203.0.113.5")).toMatchObject({
      labelVisibility: "visible",
      opacity: 1,
    });
    expect(sigma.nodeView("domain:example.org")).toMatchObject({
      labelVisibility: "hidden",
      opacity: DIM_OPACITY,
    });
    // Only the hovered node's threads show their labels, and the canvas describes them.
    const canvas = screen.getByTestId("graph-canvas");
    // Described by its keyboard help, then the relationships on show.
    expect(canvas).toHaveAccessibleDescription(/^Tab moves .* 203\.0\.113\.0\/24: /);

    act(() => {
      sigma.emit("leaveNode", { node: "net:203.0.113.0/24" });
    });
    act(() => {
      sigma.renderFrame();
    });
    // Nothing dimmed, and labelled again (there is room for it).
    expect(sigma.nodeView("domain:example.org")).toMatchObject({
      opacity: 1,
      labelVisibility: "visible",
    });
    expect(canvas.getAttribute("aria-describedby")?.split(" ")).toHaveLength(2);
    expect(canvas).toHaveAccessibleDescription(`${GRAPH_HELP} `);
  });

  it("hides and shows an entity type from the legend", async () => {
    const { sigma } = await renderApp();
    const toggle = screen.getByRole("button", { name: /Certificate/ });
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(sigma.nodeView("cert:wildcard")).toMatchObject({ visibility: "hidden" });

    await userEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(sigma.nodeView("cert:wildcard")["visibility"]).toBeUndefined();
  });

  it("finds a node, selects it and moves the camera to it", async () => {
    const { sigma } = await renderApp();
    await userEvent.type(screen.getByRole("combobox", { name: "Search entities" }), "vpn{Enter}");
    expect(within(details()).getByRole("heading", { level: 2 })).toHaveTextContent(
      "vpn.example.org",
    );
    expect(sigma.camera.animate).toHaveBeenCalledWith(
      expect.objectContaining({ x: 0.25, y: 0.75 }),
      expect.anything(),
    );
  });

  it("follows connections from the side panel", async () => {
    const { sigma } = await renderApp();
    act(() => {
      sigma.emit("clickNode", { node: "tech:react" });
    });
    await userEvent.click(
      within(details()).getByRole("button", { name: /docs\.example\.org\/login/ }),
    );
    expect(within(details()).getByRole("heading", { level: 2 })).toHaveTextContent(
      "https://docs.example.org/login",
    );
    await userEvent.click(within(details()).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("heading", { name: "Demo investigation" })).toBeVisible();
  });

  it("resets the view", async () => {
    const { sigma } = await renderApp();
    act(() => {
      sigma.emit("clickNode", { node: "domain:example.org" });
    });
    await userEvent.click(screen.getByRole("button", { name: /IP address/ }));
    sigma.camera.animate.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    // The whole graph framed again, unrotated (see fitCamera.test.ts).
    expect(sigma.camera.animate).toHaveBeenCalledOnce();
    expect(sigma.camera.animate).toHaveBeenCalledWith(
      expect.objectContaining({ angle: 0 }),
      expect.anything(),
    );
    expect(screen.getByRole("heading", { name: "Demo investigation" })).toBeVisible();
    expect(screen.getByRole("button", { name: /IP address/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("labels every node of a small graph where there is room", async () => {
    const { sigma } = await renderApp();
    spreadNodes(sigma);
    act(() => {
      sigma.renderFrame();
    });
    const nodes = sigma.graph.nodes();
    const unlabelled = nodes.filter(
      (node) => sigma.nodeView(node)["labelVisibility"] !== "visible",
    );
    expect(unlabelled).toEqual([]);
  });

  it("tolerates a detached container instead of throwing", async () => {
    // A frame can run after navigating away, before sigma is killed.
    const { sigma } = await renderApp();
    expect(sigma.options.settings).toMatchObject({ allowInvalidContainer: true });
  });

  it("kills sigma on unmount", async () => {
    const { sigma, unmount } = await renderApp();
    unmount();
    expect(sigma.kill).toHaveBeenCalledOnce();
  });
});
