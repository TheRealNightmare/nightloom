/**
 * The first view of a case's graph is framed again when nodes move without
 * the user: the saved pins arrive after the canvas first drew the graph.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSchemaCache, type ApiGraph } from "../api/graph";
import { fittedCameraState, type AnySigma } from "../graph/interactions";
import { CASE_ID, makeEntity, makeRelationship, serveCase } from "../test/caseFixtures";
import { clearCookies, FakeApi, json } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { FakeSigma, waitForSigma } from "../test/sigmaMock";

vi.mock("sigma", () => import("../test/sigmaMock"));

const path = `/cases/${CASE_ID}`;
const apex = makeEntity("domain", "example.org");
const www = makeEntity("domain", "www.example.org");
const ip = makeEntity("ip_address", "192.0.2.1");
const graph: ApiGraph = {
  nodes: [apex, www, ip],
  edges: [makeRelationship("HAS_SUBDOMAIN", apex, www), makeRelationship("RESOLVES_TO", www, ip)],
  truncated: false,
};
/** Pinned far from where the first layout puts them. */
const PINS = { [apex.key]: [900, 900], [www.key]: [1300, 900], [ip.key]: [1100, 1250] };

let api: FakeApi;

/** Opens the graph with the saved layout held back until the returned function is called. */
async function openGraph() {
  let release: () => void = () => undefined;
  const layout = new Promise<Response>((resolve) => {
    release = () => {
      resolve(json({ positions: PINS, updated_at: "2026-11-20T00:00:00Z" }));
    };
  });
  serveCase(api, { graph });
  api.on("GET", `${path}/layout`, () => layout);
  renderApp(`${path}/graph`);
  const sigma = await waitForSigma();
  return { sigma, release };
}

const camera = (sigma: FakeSigma) => sigma.camera.getState();
/** Where the framing puts the camera for the graph as it is now. */
const framed = (sigma: FakeSigma) =>
  fittedCameraState(sigma as unknown as AnySigma, sigma.graph.nodes());

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
  resetSchemaCache();
});

afterEach(() => {
  FakeSigma.reset();
  clearCookies();
});

describe("framing the first view", () => {
  it("frames the graph again when the saved pins arrive and move its nodes", async () => {
    const { sigma, release } = await openGraph();
    const before = camera(sigma);
    release();
    await waitFor(() => {
      expect(sigma.graph.getNodeAttributes(apex.key)["x"]).toBe(900);
    });
    await waitFor(() => {
      expect(camera(sigma)).not.toEqual(before);
    });
    const expected = framed(sigma);
    if (!expected) throw new Error("no framing");
    expect(camera(sigma).x).toBeCloseTo(expected.x, 6);
    expect(camera(sigma).y).toBeCloseTo(expected.y, 6);
    expect(camera(sigma).ratio).toBeCloseTo(expected.ratio, 6);
  });

  it("leaves the camera alone once the user moved it", async () => {
    const { sigma, release } = await openGraph();
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await waitFor(() => {
      expect(sigma.camera.getState().ratio).toBeLessThan(1);
    });
    const moved = camera(sigma);
    release();
    await waitFor(() => {
      expect(sigma.graph.getNodeAttributes(apex.key)["x"]).toBe(900);
    });
    // Give a refit every chance to happen.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(camera(sigma)).toEqual(moved);
  });
});
