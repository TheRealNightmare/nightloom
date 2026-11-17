import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sampleGraph } from "../demo/sampleGraph";
import { buildGraph } from "../graph/buildGraph";
import { connectionsOf, countByType } from "../graph/view";
import { SidePanel, TypeSummary } from "./SidePanel";

const graph = buildGraph(sampleGraph, { layoutIterations: 0 });
const types = countByType(sampleGraph.nodes);
const overview = (
  <>
    <h2>Overview</h2>
    <TypeSummary types={types} />
  </>
);

function renderPanel(selectedId: string | null) {
  const onSelect = vi.fn();
  const onClear = vi.fn();
  const selected = selectedId === null ? null : graph.getNodeAttribute(selectedId, "entity");
  render(
    <SidePanel
      selected={selected}
      connections={selectedId === null ? [] : connectionsOf(graph, selectedId)}
      overview={overview}
      onSelect={onSelect}
      onClear={onClear}
    />,
  );
  return { onSelect, onClear };
}

describe("SidePanel", () => {
  it("shows the overview it is given when nothing is selected", () => {
    renderPanel(null);
    const panel = screen.getByRole("complementary", { name: "Details" });
    expect(within(panel).getByRole("heading", { name: "Overview" })).toBeVisible();
    expect(panel).toHaveTextContent("IP address");
  });

  it("renders custom entity details instead of the default ones", () => {
    const selected = graph.getNodeAttribute("asn:64500", "entity");
    render(
      <SidePanel
        selected={selected}
        connections={[]}
        overview={null}
        renderEntity={(entity) => <p>Custom {entity.label}</p>}
        onSelect={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(screen.getByText("Custom AS64500")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Provenance" })).toBeNull();
  });

  it("joins list properties", () => {
    const selected = {
      ...graph.getNodeAttribute("asn:64500", "entity"),
      properties: { data_classes: ["email", "password"] },
    };
    render(
      <SidePanel
        selected={selected}
        connections={[]}
        overview={null}
        onSelect={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(screen.getByText("email, password")).toBeVisible();
  });

  it("shows the selected entity with its properties and provenance", () => {
    renderPanel("asn:64500");
    const panel = screen.getByRole("complementary", { name: "Details" });
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent("AS64500");
    expect(panel).toHaveTextContent("asn:64500");
    expect(panel).toHaveTextContent("ASN");
    expect(panel).toHaveTextContent("Example Hosting (fictional)");

    const provenance = within(panel)
      .getByRole("heading", { name: "Provenance" })
      .closest("section");
    expect(provenance).not.toBeNull();
    const facts = within(provenance!);
    expect(facts.getByText("rdap")).toBeVisible();
    expect(facts.getByText("May 19, 2025, 12:00 AM UTC")).toHaveAttribute(
      "datetime",
      "2025-05-19T00:00:00Z",
    );
    expect(facts.getByText("90%")).toBeVisible();
  });

  it("lists connections in both directions and selects one on click", async () => {
    const { onSelect } = renderPanel("ip:203.0.113.5");
    const panel = screen.getByRole("complementary", { name: "Details" });
    expect(within(panel).getByRole("heading", { name: "Connections (5)" })).toBeVisible();
    expect(panel).toHaveTextContent("→ within");
    expect(panel).toHaveTextContent("← resolves to");

    await userEvent.click(within(panel).getByRole("button", { name: "cdn.example.org" }));
    expect(onSelect).toHaveBeenCalledWith("sub:cdn.example.org");
  });

  it("closes the details", async () => {
    const { onClear } = renderPanel("domain:example.org");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("omits the properties section for entities without properties", () => {
    renderPanel("sub:www.example.org");
    expect(screen.queryByRole("heading", { name: "Properties" })).toBeNull();
  });
});
