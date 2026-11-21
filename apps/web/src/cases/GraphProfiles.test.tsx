/**
 * Profile runs from the case Graph tab: "Run a profile" for one entity or a
 * selection, and runs grouped in the Jobs panel with progress and Cancel run.
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSchemaCache, type ApiGraph } from "../api/graph";
import type { ScanRun } from "../api/scans";
import type { Job } from "../api/shuttles";
import {
  CASE_ID,
  EXPOSURE_PROFILE,
  makeEntity,
  makeJob,
  makeRun,
  QUICK_PROFILE,
  serveCase,
} from "../test/caseFixtures";
import { clearCookies, FakeApi, json } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { FakeSigma, sigmaEvent, waitForSigma } from "../test/sigmaMock";
import { NO_PROFILES_MESSAGE, PROFILE_RECENT_HINT } from "./graph/RunProfile";
import { JOB_POLL_MS } from "./graph/useCaseJobs";

vi.mock("sigma", () => import("../test/sigmaMock"));

let api: FakeApi;
const path = `/cases/${CASE_ID}`;
const apex = makeEntity("domain", "example.org");
const www = makeEntity("domain", "www.example.org");
const ip = makeEntity("ip_address", "192.0.2.1");
const GRAPH: ApiGraph = { nodes: [apex, www, ip], edges: [], truncated: false };

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
  resetSchemaCache();
});

afterEach(() => {
  vi.useRealTimers();
  FakeSigma.reset();
  clearCookies();
});

type ServeOptions = Parameters<typeof serveCase>[1];

async function openGraph(options: ServeOptions = {}) {
  serveCase(api, { graph: GRAPH, ...options });
  for (const entity of [apex, www, ip]) {
    api.on("GET", `${path}/entities/${encodeURIComponent(entity.key)}`, () =>
      json({ ...entity, neighbours: 0 }),
    );
  }
  renderApp(`${path}/graph`);
  await screen.findByText(/\d+ entit(y|ies), \d+ relationships?/);
}

const details = () => screen.getByRole("complementary", { name: "Details" });
const profileSection = () => within(details()).getByRole("region", { name: "Run a profile" });
const runsBody = () =>
  JSON.parse(api.callsTo("POST", `${path}/scan-runs`).at(-1)?.init.body as string) as unknown;

async function select(...keys: string[]) {
  const sigma = await waitForSigma();
  act(() => {
    sigma.emit("clickNode", { node: keys[0] });
  });
  for (const key of keys.slice(1)) {
    act(() => {
      sigma.emit("clickNode", {
        node: key,
        event: sigmaEvent({ x: 0, y: 0 }, new MouseEvent("click", { ctrlKey: true })),
      });
    });
  }
}

describe("Run a profile", () => {
  it("offers the profiles for the entity's type and runs one", async () => {
    const started = makeRun({ entities: 1, total: 3, released: 3, waiting: 0, skipped: 0 });
    api.on("POST", `${path}/scan-runs`, () => json(started, 201));
    await openGraph();
    await select(apex.key);
    const section = await waitFor(profileSection);
    const picker = within(section).getByRole("combobox", { name: "Profile" });
    // Exposure check has no Shuttle for domains.
    expect(
      within(picker)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Quick domain recon"]);
    expect(within(section).getByText(QUICK_PROFILE.description)).toBeVisible();
    expect(within(section).getByText("rdap")).toBeVisible();
    const box = within(section).getByRole("checkbox", { name: "Use recent results" });
    expect(box).toHaveAccessibleDescription(PROFILE_RECENT_HINT);

    await userEvent.click(within(section).getByRole("button", { name: "Run on example.org" }));
    expect(await within(section).findByRole("status")).toHaveTextContent(
      "Queued 3 jobs. Follow it under Jobs.",
    );
    expect(runsBody()).toEqual({ profile_id: "quick-domain-recon", entity_keys: [apex.key] });
    // The Jobs panel opens with the run.
    expect(screen.getByRole("region", { name: "Jobs" })).toBeVisible();

    await userEvent.click(box);
    await userEvent.click(within(section).getByRole("button", { name: "Run on example.org" }));
    expect(runsBody()).toEqual({
      profile_id: "quick-domain-recon",
      entity_keys: [apex.key],
      fresh: true,
    });
  });

  it("runs on a selection and reports what waits and what was skipped", async () => {
    const started = makeRun({ entities: 3, total: 7, released: 5, waiting: 2, skipped: 2 });
    api.on("POST", `${path}/scan-runs`, () => json(started, 201));
    await openGraph();
    await select(apex.key, www.key, ip.key);
    expect(within(details()).getByRole("heading", { name: "3 entities selected" })).toBeVisible();
    const section = await waitFor(profileSection);
    const picker = within(section).getByRole("combobox", { name: "Profile" });
    expect(within(picker).getAllByRole("option")).toHaveLength(2);
    await userEvent.selectOptions(picker, "Exposure check");
    await userEvent.click(within(section).getByRole("button", { name: "Run on 3 entities" }));
    expect(runsBody()).toEqual({
      profile_id: "exposure-check",
      entity_keys: [apex.key, www.key, ip.key],
    });
    expect(await within(section).findByRole("status")).toHaveTextContent(
      "Queued 5 of 7 jobs; the other 2 start as these finish. 2 skipped.",
    );
  });

  it("shows a refused run, and says when no profile fits", async () => {
    api.on("POST", `${path}/scan-runs`, () =>
      json({ detail: "You already have 2 profile runs in progress." }, 429),
    );
    await openGraph({ profiles: [EXPOSURE_PROFILE, QUICK_PROFILE] });
    await select(apex.key);
    const section = await waitFor(profileSection);
    await userEvent.click(within(section).getByRole("button", { name: /^Run on/ }));
    expect(await within(section).findByRole("alert")).toHaveTextContent(
      "You already have 2 profile runs in progress.",
    );
  });

  it("says when no profile has a Shuttle for the type", async () => {
    await openGraph({ profiles: [EXPOSURE_PROFILE] });
    await select(apex.key);
    expect(await within(profileSection()).findByText(NO_PROFILES_MESSAGE)).toBeVisible();
  });

  it("is hidden from viewers", async () => {
    await openGraph({ role: "viewer" });
    await select(apex.key);
    await within(details()).findByRole("heading", { level: 2 });
    expect(within(details()).queryByRole("region", { name: "Run a profile" })).toBeNull();
  });
});

describe("runs in the Jobs panel", () => {
  const runJob = (id: string, status: Job["status"], key = apex.key): Job =>
    makeJob({
      id,
      status,
      input_key: key,
      run_id: makeRun().id,
      started_at: status === "queued" ? null : "2026-11-12T09:00:01Z",
      finished_at: status === "succeeded" ? "2026-11-12T09:00:03Z" : null,
    });

  const openJobs = () => userEvent.click(screen.getByRole("button", { name: /Jobs/ }));
  const jobsPanel = () => screen.getByRole("region", { name: "Jobs" });

  it("groups a run's jobs under it, with progress and Cancel run", async () => {
    const run = makeRun();
    const single = makeJob({
      id: "1d2c3b4a-0000-4000-8000-000000000001",
      status: "succeeded",
      created_at: "2026-11-12T08:00:00Z",
      finished_at: "2026-11-12T08:00:02Z",
    });
    const jobs = [
      runJob("1d2c3b4a-0000-4000-8000-000000000002", "succeeded"),
      runJob("1d2c3b4a-0000-4000-8000-000000000003", "running", www.key),
      single,
    ];
    let current: ScanRun = run;
    api.on("POST", `${path}/scan-runs/${run.id}/cancel`, () => {
      current = { ...run, status: "cancelled", waiting: 0, finished_at: "2026-11-12T09:01:00Z" };
      return json(current);
    });
    await openGraph({ runs: [run], jobs });
    await openJobs();
    const panel = jobsPanel();
    const history = within(panel).getByRole("list", { name: "Job history" });
    const rows = within(history).getAllByRole("listitem");
    expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual([
      "Quick domain recon on 2 entities",
      "dns on example.org",
    ]);
    const row = rows[0]!;
    expect(within(row).getByText("Running")).toBeVisible();
    expect(within(row).getByTestId("run-progress")).toHaveTextContent(
      "1 of 6 done · 1 running · 3 queued · 1 waiting",
    );
    expect(within(row).getByRole("progressbar")).toHaveAttribute("value", "1");
    // Its jobs, on demand.
    await userEvent.click(within(row).getByRole("button", { name: "Show its jobs (2)" }));
    const nested = within(row).getByRole("list", {
      name: "Jobs of Quick domain recon on 2 entities",
    });
    expect(within(nested).getAllByRole("listitem")).toHaveLength(2);

    await userEvent.click(
      within(row).getByRole("button", { name: "Cancel Quick domain recon on 2 entities" }),
    );
    await waitFor(() => {
      expect(within(row).getByText("Cancelled")).toBeVisible();
    });
    expect(api.callsTo("POST", `${path}/scan-runs/${run.id}/cancel`)).toHaveLength(1);
  });

  it("shows why a run stopped, and no Cancel run for viewers", async () => {
    const stopped = makeRun({
      status: "stopped",
      stop_reason: "case_not_open",
      waiting: 0,
      finished_at: "2026-11-12T09:02:00Z",
    });
    await openGraph({
      runs: [stopped, makeRun({ id: "2b", created_at: "2026-11-12T09:30:00Z" })],
      role: "viewer",
    });
    await openJobs();
    const rows = within(jobsPanel()).getAllByRole("listitem", {
      name: "Quick domain recon on 2 entities",
    });
    expect(within(rows[1]!).getByText(/case is no longer open/)).toBeVisible();
    expect(within(jobsPanel()).queryByRole("button", { name: /^Cancel Quick/ })).toBeNull();
  });

  it("polls a running run and picks up the jobs it queues", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const run = makeRun({
      released: 1,
      waiting: 5,
      jobs: { queued: 1, running: 0, succeeded: 0, failed: 0, cancelled: 0 },
    });
    const first = runJob("1d2c3b4a-0000-4000-8000-00000000000a", "queued");
    let runs = [run];
    let jobs = [first];
    await openGraph({ runs, jobs });
    api.on("GET", `${path}/scan-runs?limit=20`, () => json({ items: runs }));
    api.on("GET", `${path}/jobs?limit=20`, () => json({ items: jobs, next_cursor: null }));
    api.on("GET", `${path}/jobs/${first.id}`, () => json(jobs[0]));
    await openJobs();
    const released = runJob("1d2c3b4a-0000-4000-8000-00000000000b", "queued", www.key);
    jobs = [released, first];
    runs = [{ ...run, released: 2, waiting: 4, jobs: { ...run.jobs, queued: 2 } }];
    await act(() => vi.advanceTimersByTimeAsync(JOB_POLL_MS));
    const row = within(jobsPanel()).getByRole("listitem", {
      name: "Quick domain recon on 2 entities",
    });
    await waitFor(() => {
      expect(within(row).getByTestId("run-progress")).toHaveTextContent("2 queued · 4 waiting");
    });
    expect(within(row).getByRole("button", { name: "Show its jobs (2)" })).toBeVisible();
  });
});
