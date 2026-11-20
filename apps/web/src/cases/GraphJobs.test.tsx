/**
 * Running Shuttles from the case Graph tab: the "Run a Shuttle" section of the
 * side panel and the Jobs panel (live status, cancel, history, results).
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSchemaCache, type ApiGraph } from "../api/graph";
import type { Job } from "../api/shuttles";
import {
  CASE_ID,
  DNS_SHUTTLE,
  makeEntity,
  makeJob,
  makeRelationship,
  makeShuttle,
  serveCase,
} from "../test/caseFixtures";
import { clearCookies, FakeApi, json } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { FakeSigma } from "../test/sigmaMock";
import { PULSE_MS } from "./GraphTab";
import { NO_JOBS_MESSAGE } from "./graph/JobsPanel";
import { PERSON_SHUTTLE_REASON } from "./graph/jobText";
import { NO_SHUTTLES_MESSAGE, USAGE_NOTE_LABEL } from "./graph/RunShuttles";
import { JOB_POLL_MS } from "./graph/useCaseJobs";

vi.mock("sigma", () => import("../test/sigmaMock"));

let api: FakeApi;
const path = `/cases/${CASE_ID}`;
const apex = makeEntity("domain", "example.org");
const ip = makeEntity("ip_address", "192.0.2.1");
const ns = makeEntity("domain", "ns1.example.net");
const START: ApiGraph = { nodes: [apex], edges: [], truncated: false };
const AFTER: ApiGraph = {
  nodes: [apex, ip, ns],
  edges: [makeRelationship("RESOLVES_TO", apex, ip), makeRelationship("HAS_NS", apex, ns)],
  truncated: false,
};

const queued = makeJob();
const running = makeJob({ status: "running", started_at: "2026-11-12T08:30:01Z" });
const succeeded = makeJob({
  status: "succeeded",
  started_at: "2026-11-12T08:30:01Z",
  finished_at: "2026-11-12T08:30:03Z",
  stats: {
    entities_created: 2,
    entities_updated: 1,
    relationships_created: 2,
    relationships_updated: 0,
    findings_rejected: 0,
    rejected_reasons: {},
    entities_touched: [ip.key, ns.key, apex.key],
  },
});

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
  serveCase(api, { graph: START, ...options });
  api.on("GET", `${path}/entities/${encodeURIComponent(apex.key)}`, () =>
    json({ ...apex, neighbours: 0 }),
  );
  renderApp(`${path}/graph`);
  await screen.findByText(/\d+ entit(y|ies), \d+ relationships?/);
}

const details = () => screen.getByRole("complementary", { name: "Details" });

async function select(key: string) {
  await screen.findByTestId("graph-canvas");
  act(() => {
    FakeSigma.latest().emit("clickNode", { node: key });
  });
}

const jobsPanel = () => screen.getByRole("region", { name: "Jobs" });

function jobRow(name = "dns on example.org") {
  return within(jobsPanel()).getByRole("listitem", { name });
}

async function poll() {
  await act(() => vi.advanceTimersByTimeAsync(JOB_POLL_MS));
}

describe("Run a Shuttle section", () => {
  it("lists the Shuttles for the entity's type with badges and links", async () => {
    await openGraph();
    await select(apex.key);
    const section = within(details()).getByRole("region", { name: "Run a Shuttle" });
    const card = await within(section).findByRole("listitem", { name: "DNS records" });
    expect(within(card).getByText(DNS_SHUTTLE.description)).toBeVisible();
    expect(within(card).getByText("Passive")).toBeVisible();
    expect(within(card).getByText("Network")).toBeVisible();
    expect(within(card).getByText("Infrastructure")).toBeVisible();
    const terms = within(card).getByRole("link", { name: "Terms" });
    expect(terms).toHaveAttribute("href", DNS_SHUTTLE.terms_url);
    expect(terms).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(card).getByRole("button", { name: "Run DNS records" })).toBeEnabled();
    // No usage conditions declared: nothing shown.
    expect(within(card).queryByText(new RegExp(USAGE_NOTE_LABEL))).toBeNull();
    expect(within(card).queryByTestId("usage-note-icon")).toBeNull();
  });

  it("shows a Shuttle's usage note under its description, with an info icon", async () => {
    const note =
      "Free for non-commercial use. Building a paid product on this data requires a Shodan license.";
    const internetdb = makeShuttle({
      name: "internetdb",
      title: "Open ports and exposures (Shodan InternetDB)",
      description: "Looks up open ports in Shodan's InternetDB.",
      terms_url: "https://account.shodan.io/legal/tos",
      homepage: "https://internetdb.shodan.io",
      usage_note: note,
    });
    await openGraph({ shuttles: [internetdb] });
    await select(apex.key);
    const card = await within(details()).findByRole("listitem", {
      name: "Open ports and exposures (Shodan InternetDB)",
    });
    const usage = within(card).getByText(note);
    expect(usage).toBeVisible();
    // Screen readers hear what it is; the icon itself is decorative.
    expect(usage.closest("p")).toHaveTextContent(`${USAGE_NOTE_LABEL}: ${note}`);
    const icon = within(card).getByTestId("usage-note-icon");
    expect(icon).toHaveAttribute("aria-hidden", "true");
    expect(usage.closest("p")).toContainElement(icon);
    // Below the description, right before the terms link.
    const description = within(card).getByText(internetdb.description);
    const terms = within(card).getByRole("link", { name: "Terms" });
    expect(terms).toHaveAttribute("href", internetdb.terms_url);
    const following = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(description.compareDocumentPosition(usage) & following).toBeTruthy();
    expect(usage.compareDocumentPosition(terms) & following).toBeTruthy();
    expect(usage.closest("p")?.nextElementSibling).toContainElement(terms);
  });

  it("disables person-related and active Shuttles with the reason", async () => {
    const person = makeShuttle({ name: "people", title: "People search", sensitivity: "person" });
    const active = makeShuttle({ name: "scan", title: "Port scan", mode: "active" });
    await openGraph({ shuttles: [person, active] });
    await select(apex.key);
    const people = await within(details()).findByRole("listitem", { name: "People search" });
    const run = within(people).getByRole("button", { name: "Run People search" });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription(PERSON_SHUTTLE_REASON);
    const scan = within(details()).getByRole("listitem", { name: "Port scan" });
    expect(within(scan).getByText("Active")).toBeVisible();
    expect(within(scan).getByRole("button", { name: "Run Port scan" })).toBeDisabled();
    expect(within(scan).getByText(/ownership verification/)).toBeVisible();
  });

  it("allows person-related Shuttles when the case does", async () => {
    const person = makeShuttle({ name: "people", title: "People search", sensitivity: "person" });
    await openGraph({ shuttles: [person], overrides: { allow_person_entities: true } });
    await select(apex.key);
    const card = await within(details()).findByRole("listitem", { name: "People search" });
    expect(within(card).getByRole("button", { name: "Run People search" })).toBeEnabled();
  });

  it("says when no Shuttle accepts the type", async () => {
    await openGraph({ shuttles: [] });
    await select(apex.key);
    expect(await within(details()).findByText(NO_SHUTTLES_MESSAGE)).toBeVisible();
  });

  it("is hidden from viewers and in closed cases", async () => {
    await openGraph({ role: "viewer" });
    await select(apex.key);
    expect(within(details()).queryByRole("region", { name: "Run a Shuttle" })).toBeNull();
    // Viewers still see the job history.
    await userEvent.click(screen.getByRole("button", { name: "Jobs" }));
    expect(await within(jobsPanel()).findByText(NO_JOBS_MESSAGE)).toBeVisible();
  });

  it.each([
    [403, "This case does not allow person-related entities."],
    [409, "This case is not open. Reopen it to run Shuttles."],
    [429, "You already have 5 queued or running jobs. Wait for one to finish."],
  ])("shows a %s answer inline", async (status, detail) => {
    api.on("POST", `${path}/jobs`, () => json({ detail }, status));
    await openGraph();
    await select(apex.key);
    const card = await within(details()).findByRole("listitem", { name: "DNS records" });
    await userEvent.click(within(card).getByRole("button", { name: "Run DNS records" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent(detail);
  });

  it("shows a load error for the Shuttle list", async () => {
    await openGraph();
    api.on("GET", "/shuttles?input_type=domain", () => json({}, 500));
    await select(apex.key);
    expect(await within(details()).findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});

describe("jobs", () => {
  it("runs a Shuttle, follows the job live and highlights its results", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    let current: Job = queued;
    api.on("POST", `${path}/jobs`, () => json(queued, 201));
    api.on("GET", `${path}/jobs/${queued.id}`, () => json(current));
    await openGraph();
    await select(apex.key);

    const card = await within(details()).findByRole("listitem", { name: "DNS records" });
    await user.click(within(card).getByRole("button", { name: "Run DNS records" }));
    expect(await within(card).findByText(/Queued\. Follow it under Jobs/)).toBeVisible();
    expect(JSON.parse(api.callsTo("POST", `${path}/jobs`)[0]?.init.body as string)).toEqual({
      shuttle: "dns",
      entity_key: apex.key,
    });

    // The Jobs panel opens with the new job; the toggle counts active jobs.
    expect(within(jobRow()).getByText("Queued")).toBeVisible();
    expect(screen.getByRole("button", { name: /Jobs/ })).toHaveTextContent("Jobs1");
    expect(screen.getByLabelText("1 active")).toBeVisible();

    current = running;
    await poll();
    expect(within(jobRow()).getByText("Running")).toBeVisible();
    expect(within(jobRow()).getByTestId("job-elapsed")).toHaveTextContent(/^Running for /);

    // On success the graph is loaded again and the touched entities pulse.
    api.on("GET", `${path}/graph?limit=2000`, () => json(AFTER));
    current = succeeded;
    await poll();
    expect(within(jobRow()).getByText("Succeeded")).toBeVisible();
    expect(within(jobRow()).getByText("Created 4 · Updated 1 · Rejected 0")).toBeVisible();
    expect(within(jobRow()).getByTestId("job-elapsed")).toHaveTextContent("Took 2 s");
    expect(await screen.findByText("3 entities, 2 relationships")).toBeVisible();
    await waitFor(() => {
      expect(FakeSigma.latest().nodeView(ip.key)).toMatchObject({ highlighted: true });
    });
    expect(screen.queryByLabelText("1 active")).toBeNull();

    // Polling stops once nothing is active, and the highlight fades.
    const polls = api.callsTo("GET", `${path}/jobs/${queued.id}`).length;
    await act(() => vi.advanceTimersByTimeAsync(PULSE_MS));
    expect(api.callsTo("GET", `${path}/jobs/${queued.id}`)).toHaveLength(polls);
    expect(FakeSigma.latest().nodeView(ip.key)).not.toHaveProperty("highlighted");
  });

  it("shows a failed job's sanitized error without reloading the graph", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const failed = makeJob({
      status: "failed",
      started_at: "2026-11-12T08:30:01Z",
      finished_at: "2026-11-12T08:30:02Z",
      error: "This Shuttle does not look up internal-only names. Nothing was queried.",
    });
    let current: Job = running;
    api.on("GET", `${path}/jobs/${running.id}`, () => json(current));
    await openGraph({ jobs: [running] });
    await userEvent.click(screen.getByRole("button", { name: /Jobs/ }));
    current = failed;
    await poll();
    expect(within(jobRow()).getByText("Failed")).toBeVisible();
    expect(within(jobRow()).getByText(failed.error ?? "")).toBeVisible();
    expect(api.callsTo("GET", `${path}/graph?limit=2000`)).toHaveLength(1);
  });

  it("shows the Shuttle's notes with the job", async () => {
    const noted = makeJob({
      id: "0b6f8e1c-2a3d-4c5e-8f9a-1b2c3d4e5f60",
      shuttle_name: "crtsh",
      status: "succeeded",
      started_at: "2026-11-12T08:30:01Z",
      finished_at: "2026-11-12T08:30:09Z",
      stats: {
        entities_created: 3,
        entities_updated: 0,
        relationships_created: 3,
        relationships_updated: 0,
        findings_rejected: 0,
        rejected_reasons: {},
        entities_touched: [],
        notes: [
          "Expired certificates excluded (result too large).",
          "Dropped 2 name(s): 1 email addresses, 1 IP addresses.",
        ],
      },
    });
    await openGraph({ jobs: [noted, succeeded] });
    await userEvent.click(screen.getByRole("button", { name: /Jobs/ }));
    const notes = within(jobRow("crtsh on example.org")).getByRole("list", { name: "Notes" });
    expect(
      within(notes)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(noted.stats.notes);
    expect(within(jobRow()).queryByRole("list", { name: "Notes" })).toBeNull();
  });

  it("marks a job still running past its hard time limit as overdue", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(Date.parse("2026-11-12T08:40:00Z"));
    const stuck = makeJob({
      shuttle_name: "crtsh",
      status: "running",
      started_at: "2026-11-12T08:30:00Z",
      timeout_seconds: 300,
      deadline: "2026-11-12T08:35:00Z",
      overdue_at: "2026-11-12T08:36:30Z",
    });
    const onTime = makeJob({
      id: "0b6f8e1c-2a3d-4c5e-8f9a-1b2c3d4e5f60",
      status: "running",
      started_at: "2026-11-12T08:39:00Z",
      deadline: "2026-11-12T08:39:30Z",
      overdue_at: "2026-11-12T08:41:00Z",
    });
    await openGraph({ jobs: [stuck, onTime] });
    await userEvent.click(screen.getByRole("button", { name: /Jobs/ }));
    const row = jobRow("crtsh on example.org");
    expect(within(row).getByText("overdue — the worker may have stopped")).toHaveClass(
      "job__overdue",
    );
    // The time stops at the deadline: 5 minutes, not 10.
    expect(within(row).getByTestId("job-elapsed")).toHaveTextContent("Running for 5 min 00 s");
    expect(within(jobRow()).queryByText(/overdue/)).toBeNull();
  });

  it("does not poll while the page is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.on("GET", `${path}/jobs/${running.id}`, () => json(running));
    await openGraph({ jobs: [running] });
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await poll();
    await poll();
    expect(api.callsTo("GET", `${path}/jobs/${running.id}`)).toHaveLength(0);
    visibility.mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await poll();
    expect(api.callsTo("GET", `${path}/jobs/${running.id}`)).toHaveLength(1);
  });

  it("cancels a running job", async () => {
    const cancelling = { ...running, cancel_requested: true };
    api.on("GET", `${path}/jobs/${running.id}`, () => json(cancelling));
    api.on("POST", `${path}/jobs/${running.id}/cancel`, () => json(cancelling));
    await openGraph({ jobs: [running] });
    await userEvent.click(screen.getByRole("button", { name: /Jobs/ }));
    await userEvent.click(within(jobRow()).getByRole("button", { name: "Cancel" }));
    expect(await within(jobRow()).findByRole("button", { name: "Cancelling…" })).toBeDisabled();
    expect(api.callsTo("POST", `${path}/jobs/${running.id}/cancel`)[0]?.headers).toMatchObject({
      "X-CSRF-Token": "csrf-token",
    });
  });

  it("shows a refused cancel inline", async () => {
    api.on("POST", `${path}/jobs/${running.id}/cancel`, () =>
      json({ detail: "This job has already finished." }, 409),
    );
    await openGraph({ jobs: [running] });
    await userEvent.click(screen.getByRole("button", { name: /Jobs/ }));
    await userEvent.click(within(jobRow()).getByRole("button", { name: "Cancel" }));
    expect(await within(jobRow()).findByRole("alert")).toHaveTextContent(
      "This job has already finished.",
    );
  });

  it("viewers cannot cancel", async () => {
    await openGraph({ role: "viewer", jobs: [running] });
    await userEvent.click(screen.getByRole("button", { name: /Jobs/ }));
    expect(within(jobRow()).queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("shows the running job over the graph, with its timer and Cancel", async () => {
    const cancelling = { ...running, cancel_requested: true };
    api.on("GET", `${path}/jobs/${running.id}`, () => json(cancelling));
    api.on("POST", `${path}/jobs/${running.id}/cancel`, () => json(cancelling));
    await openGraph({ jobs: [running, succeeded] });
    const chip = await screen.findByRole("button", { name: "Running: dns on example.org" });
    expect(chip).toBeVisible();
    expect(chip.parentElement).toHaveTextContent(/\d+ (s|min)/);

    // The label opens the Jobs panel; Cancel names what it cancels.
    await userEvent.click(chip);
    expect(jobsPanel()).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Cancel dns on example.org" }));
    expect(api.callsTo("POST", `${path}/jobs/${running.id}/cancel`)).toHaveLength(1);
    expect(await screen.findByRole("button", { name: "Cancel dns on example.org" })).toBeDisabled();
  });

  it("shows a refused cancel on the running-job chip", async () => {
    api.on("POST", `${path}/jobs/${running.id}/cancel`, () =>
      json({ detail: "This job has already finished." }, 409),
    );
    await openGraph({ jobs: [running] });
    await userEvent.click(await screen.findByRole("button", { name: "Cancel dns on example.org" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This job has already finished.");
  });

  it("shows no running-job chip without an active job, and no Cancel for viewers", async () => {
    await openGraph({ role: "viewer", jobs: [running] });
    expect(
      await screen.findByRole("button", { name: "Running: dns on example.org" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel dns on example.org" })).toBeNull();
  });

  it("pages through the history and can be hidden", async () => {
    const older = makeJob({
      id: "0d6c2e4b-1111-4222-8333-944455566677",
      input_key: "domain:old.example.org",
      status: "cancelled",
      finished_at: "2026-11-11T08:30:00Z",
    });
    serveCase(api, { graph: START, jobs: [succeeded] });
    api.on("GET", `${path}/jobs?limit=20`, () =>
      json({ items: [succeeded], next_cursor: "cursor-1" }),
    );
    api.on("GET", `${path}/jobs?limit=20&cursor=cursor-1`, () =>
      json({ items: [older, succeeded], next_cursor: null }),
    );
    renderApp(`${path}/graph`);
    await userEvent.click(await screen.findByRole("button", { name: /Jobs/ }));
    expect(await within(jobsPanel()).findAllByRole("listitem")).toHaveLength(1);
    // A job that had finished before the page loaded is history: no reload.
    expect(api.callsTo("GET", `${path}/graph?limit=2000`)).toHaveLength(1);

    await userEvent.click(within(jobsPanel()).getByRole("button", { name: "Load more" }));
    expect(
      await within(jobsPanel()).findByRole("listitem", { name: "dns on old.example.org" }),
    ).toBeVisible();
    expect(within(jobsPanel()).getAllByRole("listitem")).toHaveLength(2);
    expect(within(jobsPanel()).queryByRole("button", { name: "Load more" })).toBeNull();
    expect(within(jobRow("dns on old.example.org")).getByText("Cancelled")).toBeVisible();

    await userEvent.click(within(jobsPanel()).getByRole("button", { name: "Hide" }));
    expect(screen.queryByRole("region", { name: "Jobs" })).toBeNull();
  });

  it("shows when the job history cannot load", async () => {
    serveCase(api, { graph: START });
    api.on("GET", `${path}/jobs?limit=20`, () => json({}, 500));
    renderApp(`${path}/graph`);
    await userEvent.click(await screen.findByRole("button", { name: /Jobs/ }));
    expect(await within(jobsPanel()).findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});
