import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { sampleGraph } from "../demo/sampleGraph";
import { buildGraph } from "../graph/buildGraph";
import { useGraphExplorer } from "./useGraphExplorer";

const graph = buildGraph(sampleGraph, { layoutIterations: 0 });

function setup() {
  return renderHook(() => useGraphExplorer(graph));
}

describe("useGraphExplorer", () => {
  it("tracks hover and selection", () => {
    const { result } = setup();
    act(() => {
      result.current.hoverNode("ip:192.0.2.10");
    });
    expect(result.current.view.hoveredNode).toBe("ip:192.0.2.10");
    const before = result.current.view;
    act(() => {
      result.current.hoverNode("ip:192.0.2.10");
    });
    expect(result.current.view).toBe(before); // no re-render for the same node

    act(() => {
      result.current.selectNode("domain:example.org");
    });
    expect(result.current.view.selectedNode).toBe("domain:example.org");
    act(() => {
      result.current.selectNode(null);
    });
    expect(result.current.view.selectedNode).toBeNull();
  });

  it("ignores unknown nodes", () => {
    const { result } = setup();
    act(() => {
      result.current.selectNode("does-not-exist");
    });
    expect(result.current.view.selectedNode).toBeNull();
  });

  it("clears selection and hover when their type is hidden", () => {
    const { result } = setup();
    act(() => {
      result.current.selectNode("asn:64496");
      result.current.hoverNode("asn:64500");
    });
    act(() => {
      result.current.toggleEntityType("asn");
    });
    expect(result.current.view.hiddenTypes.has("asn")).toBe(true);
    expect(result.current.view.selectedNode).toBeNull();
    expect(result.current.view.hoveredNode).toBeNull();
  });

  it("keeps selection when another type is hidden", () => {
    const { result } = setup();
    act(() => {
      result.current.selectNode("asn:64496");
      result.current.toggleEntityType("ip");
    });
    expect(result.current.view.selectedNode).toBe("asn:64496");
  });

  it("shows a hidden type again when one of its entities is selected", () => {
    const { result } = setup();
    act(() => {
      result.current.toggleEntityType("certificate");
    });
    act(() => {
      result.current.selectNode("cert:mail");
    });
    expect(result.current.view.hiddenTypes.has("certificate")).toBe(false);
    expect(result.current.view.selectedNode).toBe("cert:mail");
  });

  it("shows all types and resets everything", () => {
    const { result } = setup();
    act(() => {
      result.current.toggleEntityType("ip");
      result.current.toggleEntityType("url");
    });
    act(() => {
      result.current.showAllTypes();
    });
    expect(result.current.view.hiddenTypes.size).toBe(0);

    act(() => {
      result.current.toggleEntityType("ip");
      result.current.selectNode("domain:example.org");
    });
    act(() => {
      result.current.resetView();
    });
    expect(result.current.view).toEqual({
      hoveredNode: null,
      selectedNode: null,
      hiddenTypes: new Set(),
    });
  });
});
