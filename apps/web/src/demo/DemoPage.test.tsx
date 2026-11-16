import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FakeSigma } from "../test/sigmaMock";
import { DemoPage } from "./DemoPage";

vi.mock("sigma", () => import("../test/sigmaMock"));

function renderApp() {
  const view = render(<DemoPage />);
  return { ...view, sigma: FakeSigma.latest() };
}

const details = () => screen.getByRole("complementary", { name: "Details" });

afterEach(() => {
  FakeSigma.reset();
});

describe("DemoPage", () => {
  it("renders the graph and the overview", () => {
    renderApp();
    expect(screen.getByTestId("graph-canvas")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Demo investigation" })).toBeVisible();
  });

  it("shows a node's details when it is clicked, and clears them on the stage", () => {
    const { sigma } = renderApp();
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

  it("highlights the hovered node's neighbors and dims the rest", () => {
    const { sigma } = renderApp();
    const refreshes = sigma.refresh.mock.calls.length;
    act(() => {
      sigma.emit("enterNode", { node: "net:203.0.113.0/24" });
    });
    expect(sigma.refresh.mock.calls.length).toBeGreaterThan(refreshes);
    expect(sigma.nodeView("ip:203.0.113.5")).toMatchObject({ labelVisibility: "visible" });
    expect(sigma.nodeView("domain:example.org")).toMatchObject({ labelVisibility: "hidden" });

    act(() => {
      sigma.emit("leaveNode", { node: "net:203.0.113.0/24" });
    });
    expect(sigma.nodeView("domain:example.org")).toEqual({});
  });

  it("hides and shows an entity type from the legend", async () => {
    const { sigma } = renderApp();
    const toggle = screen.getByRole("button", { name: /Certificate/ });
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(sigma.nodeView("cert:wildcard")).toMatchObject({ visibility: "hidden" });

    await userEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(sigma.nodeView("cert:wildcard")).toEqual({});
  });

  it("finds a node, selects it and moves the camera to it", async () => {
    const { sigma } = renderApp();
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
    const { sigma } = renderApp();
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
    const { sigma } = renderApp();
    act(() => {
      sigma.emit("clickNode", { node: "domain:example.org" });
    });
    await userEvent.click(screen.getByRole("button", { name: /IP address/ }));
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(sigma.camera.reset).toHaveBeenCalledOnce();
    expect(screen.getByRole("heading", { name: "Demo investigation" })).toBeVisible();
    expect(screen.getByRole("button", { name: /IP address/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("kills sigma on unmount", () => {
    const { sigma, unmount } = renderApp();
    unmount();
    expect(sigma.kill).toHaveBeenCalledOnce();
  });
});
