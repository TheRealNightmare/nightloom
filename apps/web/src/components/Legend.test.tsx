import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { EntityType } from "../domain/entities";
import { Legend } from "./Legend";

const types = [
  { type: "domain", count: 1 },
  { type: "ip_address", count: 7 },
] as const;

function renderLegend(hidden: EntityType[] = []) {
  const onToggle = vi.fn();
  const onShowAll = vi.fn();
  render(
    <Legend
      types={types}
      hiddenTypes={new Set(hidden)}
      onToggle={onToggle}
      onShowAll={onShowAll}
    />,
  );
  return { onToggle, onShowAll };
}

describe("Legend", () => {
  it("lists each type with its count as a pressed toggle", () => {
    renderLegend();
    const ip = screen.getByRole("button", { name: /IP address/ });
    expect(ip).toHaveAttribute("aria-pressed", "true");
    expect(ip).toHaveTextContent("7");
    expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();
  });

  it("toggles a type", async () => {
    const { onToggle } = renderLegend();
    await userEvent.click(screen.getByRole("button", { name: /Domain/ }));
    expect(onToggle).toHaveBeenCalledWith("domain");
  });

  it("marks hidden types and offers to show all", async () => {
    const { onShowAll } = renderLegend(["ip_address"]);
    expect(screen.getByRole("button", { name: /IP address/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await userEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(onShowAll).toHaveBeenCalledOnce();
  });

  it("explains the low-confidence thread and the pinned ring", () => {
    renderLegend();
    const marks = screen.getByRole("list", { name: "Other marks" });
    expect(marks).toHaveTextContent("Low confidence");
    expect(marks).toHaveTextContent("Pinned");
    // A key, not more toggles.
    expect(screen.getAllByRole("button")).toHaveLength(types.length);
  });
});
