import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CASE_ID, makeRun, QUICK_PROFILE } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent } from "../test/fakeApi";
import { RateLimitedError, UnexpectedResponseError } from "./client";
import {
  cancelRun,
  createProfile,
  createRun,
  deleteProfile,
  getProfiles,
  isScanProfile,
  isScanRun,
  listProfiles,
  listRuns,
  resetProfileCache,
  runLabel,
  updateProfile,
} from "./scans";

let api: FakeApi;
const runs = `/cases/${CASE_ID}/scan-runs`;

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
  resetProfileCache();
});

describe("scan profiles", () => {
  it("lists profiles, once per page load, and again after a change", async () => {
    api.on("GET", "/scan-profiles", () => json([QUICK_PROFILE]));
    expect(await listProfiles()).toEqual([QUICK_PROFILE]);
    await getProfiles();
    await getProfiles();
    expect(api.callsTo("GET", "/scan-profiles")).toHaveLength(2);
    api.on("POST", "/scan-profiles", () =>
      json({ ...QUICK_PROFILE, id: "p1", built_in: false }, 201),
    );
    await createProfile({ name: "Mine", description: "", shuttles: ["dns"] });
    await getProfiles();
    expect(api.callsTo("GET", "/scan-profiles")).toHaveLength(3);
    api.on("PATCH", "/scan-profiles/p%201", () => json({ ...QUICK_PROFILE, id: "p 1" }));
    await updateProfile("p 1", { name: "Renamed" });
    expect(
      JSON.parse(api.callsTo("PATCH", "/scan-profiles/p%201")[0]?.init.body as string),
    ).toEqual({
      name: "Renamed",
    });
    api.on("DELETE", "/scan-profiles/p1", () => noContent());
    await getProfiles();
    expect(api.callsTo("GET", "/scan-profiles")).toHaveLength(4);
    await deleteProfile("p1");
    await getProfiles();
    expect(api.callsTo("GET", "/scan-profiles")).toHaveLength(5);
  });

  it("refuses malformed profiles and retries a failed load", async () => {
    expect(isScanProfile(QUICK_PROFILE)).toBe(true);
    expect(isScanProfile({ ...QUICK_PROFILE, input_types: ["planet"] })).toBe(false);
    api.on("GET", "/scan-profiles", () => json([{ id: "x" }]));
    await expect(getProfiles()).rejects.toBeInstanceOf(UnexpectedResponseError);
    api.on("GET", "/scan-profiles", () => json([QUICK_PROFILE]));
    expect(await getProfiles()).toEqual([QUICK_PROFILE]);
  });
});

describe("scan runs", () => {
  it("starts, lists and cancels runs", async () => {
    const run = makeRun();
    api.on("POST", runs, () => json(run, 201));
    api.on("GET", `${runs}?limit=20`, () => json({ items: [run] }));
    api.on("POST", `${runs}/${run.id}/cancel`, () => json({ ...run, status: "cancelled" }));
    expect(
      await createRun(CASE_ID, { profile_id: "quick-domain-recon", entity_keys: ["domain:a.org"] }),
    ).toEqual(run);
    expect(await listRuns(CASE_ID)).toEqual([run]);
    expect((await cancelRun(CASE_ID, run.id)).status).toBe("cancelled");
  });

  it("refuses malformed runs and passes on refusals", async () => {
    expect(isScanRun(makeRun())).toBe(true);
    expect(isScanRun({ ...makeRun(), status: "paused" })).toBe(false);
    expect(isScanRun({ ...makeRun(), jobs: { queued: -1 } })).toBe(false);
    api.on("GET", `${runs}?limit=20`, () => json({ items: [{}] }));
    await expect(listRuns(CASE_ID)).rejects.toBeInstanceOf(UnexpectedResponseError);
    api.on("POST", runs, () => json({ detail: "Two runs already." }, 429));
    await expect(
      createRun(CASE_ID, { profile_id: "x", entity_keys: ["domain:a.org"] }),
    ).rejects.toBeInstanceOf(RateLimitedError);
  });

  it("names a run by its profile and how many entities", () => {
    expect(runLabel(makeRun({ entities: 1 }))).toBe("Quick domain recon on 1 entity");
    expect(runLabel(makeRun({ entities: 12 }))).toBe("Quick domain recon on 12 entities");
  });
});
