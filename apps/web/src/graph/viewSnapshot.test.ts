import { afterEach, describe, expect, it } from "vitest";

import { FakeSigma } from "../test/sigmaMock";
import { STATE } from "../test/viewFixtures";
import { EMPTY_FILTERS, filtersFromParams, filtersToParams, type GraphFilters } from "./filters";
import { graphCamera, moveCamera, type AnySigma } from "./interactions";
import {
  filtersFromApi,
  filtersToApi,
  nameLength,
  snapshotToState,
  stateToSnapshot,
  viewLink,
  viewNameError,
  type ViewSnapshot,
} from "./viewSnapshot";

const FILTERS: GraphFilters = {
  hiddenTypes: new Set(["url", "domain"]),
  hiddenSources: new Set(["wayback", "dns"]),
  hiddenSensitivities: new Set(["person"]),
  seen: { kind: "range", from: "2026-11-01", to: "2026-11-20" },
  minConfidence: 0.4,
  minReliability: "C",
  minCredibility: 2,
  focus: { id: "domain:example.org", hops: 3 },
  hideIsolated: true,
};

const SNAPSHOT: ViewSnapshot = {
  mode: "2d",
  depth: "time",
  camera: { x: 10, y: -4, ratio: 0.25, angle: 0.5 },
  camera3d: null,
  filters: FILTERS,
  expanded: ["group:domain:example.org|url"],
  pins: { "domain:example.org": [1, 2], "ip_address:192.0.2.1": [-3, 4.5] },
};

afterEach(() => {
  FakeSigma.reset();
});

describe("filters", () => {
  it("round-trip through the API's shape", () => {
    expect(filtersFromApi(filtersToApi(FILTERS))).toEqual(FILTERS);
    expect(filtersFromApi(filtersToApi(EMPTY_FILTERS))).toEqual(EMPTY_FILTERS);
    for (const seen of [{ kind: "preset", preset: "7d" }, { kind: "all" }] as const) {
      const filters = { ...EMPTY_FILTERS, seen };
      expect(filtersFromApi(filtersToApi(filters))).toEqual(filters);
    }
  });

  it("match what the URL holds", () => {
    // A view's filters, once in the URL, read back the same.
    const params = filtersToParams(filtersFromApi(STATE.filters));
    expect(filtersToApi(filtersFromParams(params))).toEqual(STATE.filters);
  });

  it("are written in a stable order, within the API's limits", () => {
    const api = filtersToApi({
      ...EMPTY_FILTERS,
      hiddenTypes: new Set(["url", "domain"]),
      hiddenSources: new Set(["z", "a", "", "x".repeat(101)]),
      minConfidence: 2,
      seen: { kind: "range", from: "nope", to: null },
    });
    expect(api.hidden_types).toEqual(["domain", "url"]);
    expect(api.hidden_sources).toEqual(["a", "z"]);
    expect(api.min_confidence).toBe(1);
    expect(api.seen).toEqual({ kind: "range", from: null, to: null });
  });

  it("ignore what this version does not know", () => {
    const filters = filtersFromApi({
      ...STATE.filters,
      hidden_types: ["url", "starship"],
      hidden_sensitivities: ["secret", "person"],
      min_reliability: "Z",
      min_credibility: 9,
      focus: { key: "domain:example.org", hops: 2.5 },
    });
    expect([...filters.hiddenTypes]).toEqual(["url"]);
    expect([...filters.hiddenSensitivities]).toEqual(["person"]);
    expect(filters.minReliability).toBeNull();
    expect(filters.minCredibility).toBeNull();
    expect(filters.focus).toEqual({ id: "domain:example.org", hops: 1 });
  });
});

describe("snapshots", () => {
  it("round-trip: capture → state → apply gives the same snapshot", () => {
    const state = snapshotToState(SNAPSHOT);
    expect(state).not.toBeNull();
    expect(stateToSnapshot(state!, () => true)).toEqual(SNAPSHOT);
    // And through JSON, as the API stores it.
    const stored = JSON.parse(JSON.stringify(state)) as typeof state;
    expect(stateToSnapshot(stored!, () => true)).toEqual(SNAPSHOT);
  });

  it("need a camera (the graph must be drawn)", () => {
    expect(snapshotToState({ ...SNAPSHOT, camera: null })).toBeNull();
  });

  it("skip pins of entities the case does not have", () => {
    const applied = stateToSnapshot(STATE, (id) => id !== "domain:example.org");
    expect(applied.pins).toEqual({});
    expect(stateToSnapshot(STATE, () => true).pins).toEqual({ "domain:example.org": [1, 2] });
  });

  it("are saved as version 2, with the 3D camera and depth of a 3D view", () => {
    const camera3d = {
      position: [1, 2, 300] as [number, number, number],
      target: [1, 2, 0] as [number, number, number],
      up: [0, 1, 0] as [number, number, number],
    };
    const three: ViewSnapshot = { ...SNAPSHOT, mode: "3d", depth: "type", camera3d };
    const state = snapshotToState(three);
    expect(state).toMatchObject({ version: 2, mode: "3d", depth: "type", camera3d });
    expect(stateToSnapshot(state!, () => true)).toEqual(three);
    // A 2D view keeps no 3D camera.
    expect(snapshotToState({ ...SNAPSHOT, camera3d })).not.toHaveProperty("camera3d");
  });

  it("read version-1 views as 2D, with the default depth", () => {
    const applied = stateToSnapshot(STATE, () => true);
    expect(applied).toMatchObject({ mode: "2d", depth: "time", camera3d: null });
    expect(applied.camera).toEqual(STATE.camera);
  });

  it("drop duplicate groups", () => {
    const state = snapshotToState({ ...SNAPSHOT, expanded: ["group:a|url", "group:a|url"] });
    expect(state?.groups.expanded).toEqual(["group:a|url"]);
  });
});

describe("camera", () => {
  const real = (fake: FakeSigma) => fake as unknown as AnySigma;

  function sigma() {
    const nodes: string[] = [];
    return new FakeSigma(
      {
        nodes: () => nodes,
        edges: () => [],
        hasNode: () => false,
        getNodeAttributes: () => ({}),
      },
      document.createElement("div"),
    );
  }

  it("is captured in graph terms and restored exactly", () => {
    const source = sigma();
    source.camera.setState({ x: 12, y: -3, ratio: 0.4, angle: 0.3 });
    const captured = graphCamera(real(source));
    expect(captured.x).toBeCloseTo(12);
    expect(captured.y).toBeCloseTo(-3);
    expect(captured.ratio).toBeCloseTo(0.4);
    expect(captured.angle).toBeCloseTo(0.3);

    const target = sigma();
    moveCamera(real(target), captured, 0);
    expect(graphCamera(real(target)).x).toBeCloseTo(12);
    expect(graphCamera(real(target)).ratio).toBeCloseTo(0.4);
    expect(target.refresh).toHaveBeenCalled();
    // The frame is frozen, so a layout that moves nodes does not move the picture.
    expect(target.setCustomBBox).toHaveBeenCalled();
    expect(target.camera.setState).toHaveBeenCalled();
  });

  it("animates when given a duration, within the camera's bounds", () => {
    const target = sigma();
    moveCamera(real(target), { x: 0, y: 0, ratio: 100, angle: -0.2 }, 400);
    expect(target.camera.animate).toHaveBeenCalledWith(
      { x: 0, y: 0, ratio: 4, angle: -0.2 },
      expect.objectContaining({ duration: 400 }),
    );
  });
});

describe("names", () => {
  it.each([
    ["Overview", null],
    ["  Overview  ", null],
    ["x".repeat(80), null],
    ["é".repeat(80), null],
    ["😀".repeat(80), null],
    ["", "Enter a name."],
    ["   ", "Enter a name."],
    ["x".repeat(81), "Use at most 80 characters."],
    ["tab\there", "Remove line breaks, tabs and other control characters."],
    ["new\nline", "Remove line breaks, tabs and other control characters."],
    ["sep end", "Remove line breaks, tabs and other control characters."],
  ])("%j → %j", (name, expected) => {
    expect(viewNameError(name)).toBe(expected);
  });

  it("are counted in characters, not UTF-16 units", () => {
    expect(nameLength("😀é")).toBe(2);
  });
});

describe("links", () => {
  it("open the view in the case graph", () => {
    expect(
      viewLink({ origin: "https://nightloom.example", pathname: "/cases/c1/graph" }, "v 1"),
    ).toBe("https://nightloom.example/cases/c1/graph?view=v+1");
  });
});
