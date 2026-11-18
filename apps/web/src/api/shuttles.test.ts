import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DNS_SHUTTLE, makeJob } from "../test/caseFixtures";
import { clearCookies, FakeApi, json } from "../test/fakeApi";
import { ConflictError, ForbiddenError, RateLimitedError, UnexpectedResponseError } from "./client";
import {
  cancelJob,
  createJob,
  getJob,
  getShuttlesFor,
  isActive,
  isJob,
  listJobs,
  listShuttles,
  resetShuttleCache,
  type Job,
} from "./shuttles";

let api: FakeApi;
const CASE = "case-1";

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
  resetShuttleCache();
});

describe("Shuttles", () => {
  it("lists the Shuttles for a type", async () => {
    api.on("GET", "/shuttles?input_type=domain", () => json([DNS_SHUTTLE]));
    expect(await listShuttles("domain")).toEqual([DNS_SHUTTLE]);
  });

  it("refuses a malformed list", async () => {
    api.on("GET", "/shuttles?input_type=domain", () => json([{ name: "dns" }]));
    await expect(listShuttles("domain")).rejects.toBeInstanceOf(UnexpectedResponseError);
    api.on("GET", "/shuttles?input_type=domain", () => json({ items: [] }));
    await expect(listShuttles("domain")).rejects.toBeInstanceOf(UnexpectedResponseError);
  });

  it("caches each type's list and retries after a failure", async () => {
    let fail = true;
    api.on("GET", "/shuttles?input_type=domain", () => (fail ? json({}, 500) : json([])));
    await expect(getShuttlesFor("domain")).rejects.toBeInstanceOf(UnexpectedResponseError);
    fail = false;
    expect(await getShuttlesFor("domain")).toEqual([]);
    expect(await getShuttlesFor("domain")).toEqual([]);
    expect(api.callsTo("GET", "/shuttles?input_type=domain")).toHaveLength(2);
  });
});

describe("jobs", () => {
  it("creates a job with the CSRF header", async () => {
    const job = makeJob();
    api.on("POST", `/cases/${CASE}/jobs`, () => json(job, 201));
    expect(await createJob(CASE, { shuttle: "dns", entity_key: "domain:example.org" })).toEqual(
      job,
    );
    const call = api.callsTo("POST", `/cases/${CASE}/jobs`)[0];
    expect(call?.headers["X-CSRF-Token"]).toBe("csrf-token");
    expect(JSON.parse(call?.init.body as string)).toEqual({
      shuttle: "dns",
      entity_key: "domain:example.org",
    });
  });

  it("keeps the server's reasons for 403, 409 and 429", async () => {
    const body = { shuttle: "dns", entity_key: "domain:example.org" };
    api.on("POST", `/cases/${CASE}/jobs`, () =>
      json({ detail: "This case does not allow person-related entities." }, 403),
    );
    const forbidden = await createJob(CASE, body).catch((error: unknown) => error);
    expect(forbidden).toBeInstanceOf(ForbiddenError);
    expect((forbidden as ForbiddenError).detail).toBe(
      "This case does not allow person-related entities.",
    );

    api.on("POST", `/cases/${CASE}/jobs`, () =>
      json({ detail: "You already have 5 queued or running jobs." }, 429),
    );
    const limited = await createJob(CASE, body).catch((error: unknown) => error);
    expect(limited).toBeInstanceOf(RateLimitedError);
    expect((limited as RateLimitedError).detail).toBe("You already have 5 queued or running jobs.");

    api.on("POST", `/cases/${CASE}/jobs`, () => json({ detail: "This case is not open." }, 409));
    await expect(createJob(CASE, body)).rejects.toThrow(ConflictError);
  });

  it("reads, lists and cancels jobs", async () => {
    const running = makeJob({ status: "running", started_at: "2026-11-12T08:30:01Z" });
    api
      .on("GET", `/cases/${CASE}/jobs/${running.id}`, () => json(running))
      .on("GET", `/cases/${CASE}/jobs?limit=20`, () =>
        json({ items: [running], next_cursor: "abc" }),
      )
      .on("GET", `/cases/${CASE}/jobs?limit=20&cursor=abc`, () =>
        json({ items: [], next_cursor: null }),
      )
      .on("POST", `/cases/${CASE}/jobs/${running.id}/cancel`, () =>
        json({ ...running, cancel_requested: true }),
      );
    expect(await getJob(CASE, running.id)).toEqual(running);
    expect(await listJobs(CASE)).toEqual({ items: [running], next_cursor: "abc" });
    expect(await listJobs(CASE, { cursor: "abc" })).toEqual({ items: [], next_cursor: null });
    expect((await cancelJob(CASE, running.id)).cancel_requested).toBe(true);
    expect(isActive(running)).toBe(true);
    expect(isActive(makeJob({ status: "failed" }))).toBe(false);
  });

  it("checks the job shape", () => {
    expect(isJob(makeJob())).toBe(true);
    expect(isJob({ ...makeJob(), status: "paused" })).toBe(false);
    const stats: Partial<Job["stats"]> = { ...makeJob().stats };
    delete stats.entities_touched;
    expect(isJob({ ...makeJob(), stats })).toBe(false);
    // Notes are optional (older jobs have none) but must be strings.
    const withoutNotes: Partial<Job["stats"]> = { ...makeJob().stats };
    delete withoutNotes.notes;
    expect(isJob({ ...makeJob(), stats: withoutNotes })).toBe(true);
    expect(isJob({ ...makeJob(), stats: { ...makeJob().stats, notes: [1] } })).toBe(false);
  });

  it("refuses a malformed page", async () => {
    api.on("GET", `/cases/${CASE}/jobs?limit=20`, () => json({ items: [{}], next_cursor: null }));
    await expect(listJobs(CASE)).rejects.toBeInstanceOf(UnexpectedResponseError);
  });
});
