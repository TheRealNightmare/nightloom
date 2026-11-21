import { describe, expect, it } from "vitest";

import { makeJob, makeRun } from "../../test/caseFixtures";
import {
  panelEntries,
  pivotCappedText,
  pivotText,
  runProgressText,
  runStartedText,
} from "./runText";

describe("run text", () => {
  it("summarizes a run's progress", () => {
    expect(runProgressText(makeRun())).toBe("1 of 6 done · 1 running · 3 queued · 1 waiting");
    expect(
      runProgressText(
        makeRun({
          waiting: 0,
          jobs: { queued: 0, running: 0, succeeded: 3, failed: 2, cancelled: 1 },
        }),
      ),
    ).toBe("6 of 6 done · 2 failed · 1 cancelled");
  });

  it("says what a new run queued", () => {
    expect(runStartedText(makeRun({ total: 1, released: 1 }))).toBe("Queued 1 job.");
    expect(runStartedText(makeRun({ total: 7, released: 5, skipped: 2 }))).toBe(
      "Queued 5 of 7 jobs; the other 2 start as these finish. 2 skipped.",
    );
  });

  it("describes a pivot", () => {
    expect(pivotText(makeRun())).toBeNull();
    expect(pivotCappedText(makeRun())).toBeNull();
    const pivot = makeRun({ pivot: true, max_depth: 2, max_jobs: 200, max_entities: 500 });
    expect(pivotText({ ...pivot, discovered: 1 })).toBe("Pivot · 1 new entity · hop 0 of 2");
    expect(pivotText({ ...pivot, discovered: 7, depth: 2 })).toBe(
      "Pivot · 7 new entities · hop 2 of 2",
    );
    expect(pivotCappedText({ ...pivot, capped: "jobs" })).toBe(
      "Reached the limit of 200 jobs, so it stopped adding entities.",
    );
  });

  it("puts a run's jobs inside it and orders everything newest first", () => {
    const run = makeRun({ created_at: "2026-11-12T09:00:00Z" });
    const inRun = makeJob({ id: "a", run_id: run.id, created_at: "2026-11-12T09:00:01Z" });
    const newer = makeJob({ id: "b", created_at: "2026-11-12T10:00:00Z" });
    const older = makeJob({ id: "c", created_at: "2026-11-12T08:00:00Z" });
    const orphan = makeJob({ id: "d", run_id: "unknown", created_at: "2026-11-12T07:00:00Z" });
    const { entries, byRun } = panelEntries([newer, inRun, older, orphan], [run]);
    expect(entries.map((entry) => (entry.kind === "run" ? entry.run.id : entry.job.id))).toEqual([
      "b",
      run.id,
      "c",
      "d",
    ]);
    expect(byRun.get(run.id)?.map((job) => job.id)).toEqual(["a"]);
  });
});
