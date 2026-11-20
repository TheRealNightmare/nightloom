import { describe, expect, it } from "vitest";

import {
  ConflictError,
  ForbiddenError,
  NetworkError,
  RateLimitedError,
  ValidationError,
} from "../../api/client";
import { makeJob, makeShuttle } from "../../test/caseFixtures";
import { FORBIDDEN_MESSAGE } from "../errors";
import {
  ACTIVE_SHUTTLE_REASON,
  describeJobError,
  elapsedMs,
  isOverdue,
  OVERDUE_MESSAGE,
  formatDuration,
  PERSON_SHUTTLE_REASON,
  shuttleBlockedReason,
  statsText,
  TOO_MANY_JOBS_MESSAGE,
} from "./jobText";
import { mergeJobs } from "./useCaseJobs";

describe("job text", () => {
  it("formats durations", () => {
    expect(formatDuration(-5)).toBe("0 s");
    expect(formatDuration(45_900)).toBe("45 s");
    expect(formatDuration(185_000)).toBe("3 min 05 s");
    expect(formatDuration(3_720_000)).toBe("1 h 02 min");
  });

  it("measures from the start (or creation) to the end (or now)", () => {
    const now = Date.parse("2026-11-12T08:31:00Z");
    expect(elapsedMs(makeJob(), now)).toBe(60_000); // queued since creation
    const running = makeJob({ status: "running", started_at: "2026-11-12T08:30:30Z" });
    expect(elapsedMs(running, now)).toBe(30_000);
    const done = makeJob({
      status: "succeeded",
      started_at: "2026-11-12T08:30:30Z",
      finished_at: "2026-11-12T08:30:34Z",
    });
    expect(elapsedMs(done, now)).toBe(4_000);
  });

  it("stops a running job's time at its deadline", () => {
    const running = makeJob({
      status: "running",
      started_at: "2026-11-12T08:30:00Z",
      deadline: "2026-11-12T08:35:00Z",
    });
    expect(elapsedMs(running, Date.parse("2026-11-12T08:31:00Z"))).toBe(60_000);
    expect(elapsedMs(running, Date.parse("2026-11-12T09:00:00Z"))).toBe(300_000);
  });

  it("calls a running job overdue only after its hard time limit", () => {
    const running = makeJob({
      status: "running",
      started_at: "2026-11-12T08:30:00Z",
      overdue_at: "2026-11-12T08:36:30Z",
    });
    expect(isOverdue(running, Date.parse("2026-11-12T08:36:00Z"))).toBe(false);
    expect(isOverdue(running, Date.parse("2026-11-12T08:37:00Z"))).toBe(true);
    expect(isOverdue({ ...running, status: "failed" }, Date.parse("2026-11-12T09:00:00Z"))).toBe(
      false,
    );
    expect(isOverdue(makeJob(), Date.parse("2026-11-13T00:00:00Z"))).toBe(false); // queued
    expect(OVERDUE_MESSAGE).toBe("overdue — the worker may have stopped");
  });

  it("sums entity and relationship stats", () => {
    const job = makeJob({
      stats: {
        entities_created: 3,
        entities_updated: 1,
        relationships_created: 4,
        relationships_updated: 0,
        findings_rejected: 2,
        rejected_reasons: { invalid_value: 2 },
        entities_touched: [],
      },
    });
    expect(statsText(job)).toBe("Created 7 · Updated 1 · Rejected 2");
  });

  it("describes errors from starting or cancelling a job", () => {
    expect(describeJobError(new ForbiddenError("Active Shuttles cannot run."))).toBe(
      "Active Shuttles cannot run.",
    );
    expect(describeJobError(new ForbiddenError())).toBe(FORBIDDEN_MESSAGE);
    expect(describeJobError(new RateLimitedError(null, "You already have 5 jobs."))).toBe(
      "You already have 5 jobs.",
    );
    expect(describeJobError(new RateLimitedError(null))).toBe(TOO_MANY_JOBS_MESSAGE);
    expect(describeJobError(new ConflictError("This case is not open."))).toBe(
      "This case is not open.",
    );
    expect(
      describeJobError(new ValidationError([{ field: null, message: "Unknown Shuttle." }])),
    ).toBe("Unknown Shuttle.");
    expect(describeJobError(new NetworkError())).toMatch(/Cannot reach/);
  });

  it("explains which Shuttles cannot run in a case", () => {
    expect(shuttleBlockedReason(makeShuttle(), false)).toBeNull();
    expect(shuttleBlockedReason(makeShuttle({ mode: "active" }), true)).toBe(ACTIVE_SHUTTLE_REASON);
    const person = makeShuttle({ sensitivity: "person" });
    expect(shuttleBlockedReason(person, false)).toBe(PERSON_SHUTTLE_REASON);
    expect(shuttleBlockedReason(person, true)).toBeNull();
  });

  it("merges job updates, newest additions first", () => {
    const a = makeJob({ id: "a" });
    const b = makeJob({ id: "b" });
    const updated = { ...a, status: "running" as const };
    const c = makeJob({ id: "c" });
    expect(mergeJobs([a, b], [updated, c]).map((job) => [job.id, job.status])).toEqual([
      ["c", "queued"],
      ["a", "running"],
      ["b", "queued"],
    ]);
  });
});
