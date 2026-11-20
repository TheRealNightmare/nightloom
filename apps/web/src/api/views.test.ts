import { afterEach, describe, expect, it } from "vitest";

import { FakeApi, json, noContent } from "../test/fakeApi";
import { STATE } from "../test/viewFixtures";
import { ConflictError, NotFoundError, UnexpectedResponseError } from "./client";
import {
  createView,
  deleteView,
  getView,
  isSavedView,
  isViewState,
  isViewSummary,
  listViews,
  updateView,
  type SavedView,
} from "./views";

const CASE = "6f1c0d7e-3b0a-4f7e-9a51-1d2e3f4a5b6c";
const VIEW = "0d3e9f2a-1b2c-4d5e-8f90-a1b2c3d4e5f6";

const SUMMARY = {
  id: VIEW,
  name: "Overview",
  created_by: { id: "u1", display_name: "Ada Analyst" },
  updated_by: null,
  created_at: "2026-11-20T10:00:00Z",
  updated_at: "2026-11-20T11:00:00Z",
};
const FULL: SavedView = { ...SUMMARY, state: STATE };

afterEach(() => {
  document.cookie = "nightloom_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("saved views API", () => {
  it("lists a case's views", async () => {
    new FakeApi().on("GET", `/cases/${CASE}/views`, () => json([SUMMARY])).install();
    expect(await listViews(CASE)).toEqual([SUMMARY]);
  });

  it("refuses a malformed list", async () => {
    new FakeApi().on("GET", `/cases/${CASE}/views`, () => json([{ id: 1 }])).install();
    await expect(listViews(CASE)).rejects.toBeInstanceOf(UnexpectedResponseError);
    new FakeApi().on("GET", `/cases/${CASE}/views`, () => json({ items: [] })).install();
    await expect(listViews(CASE)).rejects.toBeInstanceOf(UnexpectedResponseError);
  });

  it("reads one view with its state", async () => {
    new FakeApi().on("GET", `/cases/${CASE}/views/${VIEW}`, () => json(FULL)).install();
    expect(await getView(CASE, VIEW)).toEqual(FULL);
  });

  it("reports an unknown view", async () => {
    new FakeApi()
      .on("GET", `/cases/${CASE}/views/${VIEW}`, () => json({ detail: "View not found." }, 404))
      .install();
    await expect(getView(CASE, VIEW)).rejects.toBeInstanceOf(NotFoundError);
  });

  it("saves a view with POST and the CSRF header", async () => {
    document.cookie = "nightloom_csrf=token-1";
    const api = new FakeApi().on("POST", `/cases/${CASE}/views`, () => json(FULL, 201)).install();
    expect(await createView(CASE, "Overview", STATE)).toEqual(FULL);
    const [call] = api.callsTo("POST", `/cases/${CASE}/views`);
    expect(JSON.parse(call?.init.body as string)).toEqual({ name: "Overview", state: STATE });
    expect(call?.headers["X-CSRF-Token"]).toBe("token-1");
  });

  it("passes on a taken name as a conflict with the server's message", async () => {
    new FakeApi()
      .on("POST", `/cases/${CASE}/views`, () => json({ detail: "Name taken." }, 409))
      .install();
    await expect(createView(CASE, "Overview", STATE)).rejects.toEqual(
      new ConflictError("Name taken."),
    );
  });

  it("renames or replaces with PATCH, and deletes", async () => {
    const api = new FakeApi()
      .on("PATCH", `/cases/${CASE}/views/${VIEW}`, () => json({ ...FULL, name: "Hosts" }))
      .on("DELETE", `/cases/${CASE}/views/${VIEW}`, () => noContent())
      .install();
    expect((await updateView(CASE, VIEW, { name: "Hosts" })).name).toBe("Hosts");
    expect(
      JSON.parse(api.callsTo("PATCH", `/cases/${CASE}/views/${VIEW}`)[0]?.init.body as string),
    ).toEqual({
      name: "Hosts",
    });
    await deleteView(CASE, VIEW);
    expect(api.callsTo("DELETE", `/cases/${CASE}/views/${VIEW}`)).toHaveLength(1);
  });

  it("refuses malformed views", async () => {
    new FakeApi()
      .on("POST", `/cases/${CASE}/views`, () => json(SUMMARY, 201))
      .on("PATCH", `/cases/${CASE}/views/${VIEW}`, () => json({ ...FULL, state: null }))
      .install();
    await expect(createView(CASE, "x", STATE)).rejects.toBeInstanceOf(UnexpectedResponseError);
    await expect(updateView(CASE, VIEW, { name: "x" })).rejects.toBeInstanceOf(
      UnexpectedResponseError,
    );
  });
});

describe("type guards", () => {
  it("accept what the API sends", () => {
    expect(isViewSummary(SUMMARY)).toBe(true);
    expect(isSavedView(FULL)).toBe(true);
    expect(isViewState(STATE)).toBe(true);
    for (const seen of [{ kind: "all" }, { kind: "preset", preset: "7d" }]) {
      expect(isViewState({ ...STATE, filters: { ...STATE.filters, seen } })).toBe(true);
    }
    expect(isViewState({ ...STATE, filters: { ...STATE.filters, focus: null } })).toBe(true);
  });

  it.each([
    ["no object", null],
    ["another version", { ...STATE, version: 2 }],
    ["3D", { ...STATE, mode: "3d" }],
    ["a camera without ratio", { ...STATE, camera: { x: 0, y: 0, angle: 0 } }],
    ["an infinite camera", { ...STATE, camera: { ...STATE.camera, x: Infinity } }],
    ["no filters", { ...STATE, filters: null }],
    ["an unknown time filter", { ...STATE, filters: { ...STATE.filters, seen: { kind: "x" } } }],
    [
      "an unknown preset",
      { ...STATE, filters: { ...STATE.filters, seen: { kind: "preset", preset: "1y" } } },
    ],
    [
      "a bad range",
      { ...STATE, filters: { ...STATE.filters, seen: { kind: "range", from: 1, to: null } } },
    ],
    ["a bad focus", { ...STATE, filters: { ...STATE.filters, focus: { key: 1 } } }],
    ["types that are no strings", { ...STATE, filters: { ...STATE.filters, hidden_types: [1] } }],
    ["no groups", { ...STATE, groups: null }],
    ["a bad position", { ...STATE, positions: { a: [1] } }],
  ])("refuse %s", (_label, value) => {
    expect(isViewState(value)).toBe(false);
  });

  it("refuse summaries with bad authors", () => {
    expect(isViewSummary({ ...SUMMARY, created_by: { id: 1 } })).toBe(false);
    expect(isViewSummary([])).toBe(false);
  });
});
