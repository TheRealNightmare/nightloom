import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ReadinessView } from "../api/useReadiness";
import { StatusIndicator } from "./StatusIndicator";

function renderStatus(readiness: ReadinessView) {
  const onRecheck = vi.fn();
  render(<StatusIndicator readiness={readiness} onRecheck={onRecheck} />);
  return { onRecheck, button: screen.getByRole("button", { name: /API status/ }) };
}

describe("StatusIndicator", () => {
  it("shows the checking state", () => {
    const { button } = renderStatus({ kind: "checking" });
    expect(button).toHaveTextContent("Checking API…");
    expect(button).toHaveAttribute("data-state", "checking");
  });

  it("shows Ready", () => {
    const { button } = renderStatus({ kind: "ready", checks: {} });
    expect(button).toHaveAccessibleName("API status: Ready");
    expect(button).toHaveAccessibleDescription(/All dependencies are reachable/);
    expect(button).toHaveAttribute("data-state", "ready");
  });

  it("shows Degraded with the failed checks in the tooltip", () => {
    const { button } = renderStatus({
      kind: "degraded",
      failed: [
        { name: "neo4j", error: "timed out after 2s" },
        { name: "storage", error: "ConnectError" },
      ],
    });
    expect(button).toHaveAccessibleName("API status: Degraded");
    const tooltip = screen.getByRole("tooltip", { hidden: true });
    expect(tooltip).toHaveTextContent("neo4j: timed out after 2s");
    expect(tooltip).toHaveTextContent("storage: ConnectError");
    expect(button).toHaveAccessibleDescription(/neo4j: timed out after 2s/);
  });

  it("shows API offline with the reason", () => {
    const { button } = renderStatus({ kind: "offline", reason: "No answer within 5000 ms" });
    expect(button).toHaveAccessibleName("API status: API offline");
    expect(button).toHaveAttribute("data-state", "offline");
    expect(button).toHaveAccessibleDescription(/No answer within 5000 ms/);
  });

  it("rechecks when clicked", async () => {
    const { button, onRecheck } = renderStatus({ kind: "offline", reason: "down" });
    await userEvent.click(button);
    expect(onRecheck).toHaveBeenCalledOnce();
  });
});
