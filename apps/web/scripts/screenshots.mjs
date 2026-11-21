// README screenshots (docs/images/), run by `make screenshots` against a
// throwaway stack of its own (see the Makefile): it signs in as the demo
// admin, builds fictional demo cases through the API and captures each
// screen at 1600×1000.
//
// Fictional data only: example.org / example.com / example.net names, the
// RFC 5737 IPv4 and RFC 3849 IPv6 documentation ranges, AS64496–AS64511, no
// real people. Deterministic: every entity is pinned at a fixed position (no
// force layout), the stored dates are set to fixed values afterwards, the
// page clock is fixed, the sign-in loom runs on a fake clock for a fixed time,
// and the only jobs (and the one profile run) run Shuttles that need no
// internet (the stack's DNS resolver is the test server, which answers
// nothing for these names), all finished before any capture. The verified
// domains' state is set in the database: the throwaway stack cannot prove
// ownership of example.org.
//
// Environment: NIGHTLOOM_SHOTS_URL, NIGHTLOOM_SHOTS_EMAIL,
// NIGHTLOOM_SHOTS_PASSWORD, NIGHTLOOM_SHOTS_COMPOSE (the docker compose
// command of the throwaway stack, for setting the dates) and
// NIGHTLOOM_SHOTS_OUT (the output directory).
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";

import { chromium } from "@playwright/test";

const env = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name}`);
  return value;
};
const baseURL = env("NIGHTLOOM_SHOTS_URL");
const email = env("NIGHTLOOM_SHOTS_EMAIL");
const password = env("NIGHTLOOM_SHOTS_PASSWORD");
const compose = env("NIGHTLOOM_SHOTS_COMPOSE");
const outDir = env("NIGHTLOOM_SHOTS_OUT");

const VIEWPORT = { width: 1600, height: 1000 };
/** "Now" in every captured page: shortly after the newest stored date. */
const NOW = new Date("2026-11-18T10:00:00Z");
/** The last time anything was seen. */
const LAST_SEEN = "2026-11-17T16:40:00Z";

// --- the demo case ----------------------------------------------------------------------

/** [type, value, first seen (UTC date), x, y]: the layout is fixed, as pins. */
const ENTITIES = [
  ["domain", "example.org", "2024-02-12", 0, 0],
  ["domain", "www.example.org", "2024-02-12", -200, -130],
  ["domain", "mail.example.org", "2024-03-04", 0, 210],
  ["domain", "api.example.org", "2024-09-18", 210, -130],
  ["ip_address", "192.0.2.10", "2024-02-13", -260, -320],
  ["ip_address", "2001:db8::10", "2024-05-21", -480, -190],
  ["ip_address", "192.0.2.24", "2024-09-18", 160, -330],
  ["ip_address", "198.51.100.7", "2024-03-04", -150, 380],
  ["ip_address", "2001:db8:1::25", "2024-06-30", 170, 400],
  ["netblock", "192.0.2.0/24", "2025-01-15", -60, -490],
  ["netblock", "2001:db8::/32", "2025-01-15", -560, 140],
  ["netblock", "198.51.100.0/24", "2025-02-03", -90, 560],
  ["asn", "AS64496", "2025-04-09", -340, -620],
  ["asn", "AS64500", "2025-04-09", 190, 660],
  ["organization", "Example Hosting", "2026-10-05", -640, -500],
  ["url", "https://www.example.org/login", "2026-07-22", -520, 0],
  ["domain", "example.com", "2025-06-02", 520, 60],
  ["domain", "shop.example.com", "2025-08-19", 700, -110],
  ["ip_address", "203.0.113.42", "2025-08-20", 620, -320],
  ["netblock", "203.0.113.0/24", "2025-09-01", 820, -470],
  ["asn", "AS64511", "2025-09-01", 560, -630],
  ["url", "https://shop.example.com/cart", "2026-08-11", 660, 250],
  ["domain", "example.net", "2026-01-14", 1000, -250],
  ["domain", "cdn.example.net", "2026-03-02", 960, -40],
  ["ip_address", "203.0.113.43", "2026-03-02", 1060, -520],
];

/** [type, source value, target value]. */
const RELATIONSHIPS = [
  ["HAS_SUBDOMAIN", "example.org", "www.example.org"],
  ["HAS_SUBDOMAIN", "example.org", "mail.example.org"],
  ["HAS_SUBDOMAIN", "example.org", "api.example.org"],
  ["HAS_MX", "example.org", "mail.example.org"],
  ["RESOLVES_TO", "www.example.org", "192.0.2.10"],
  ["RESOLVES_TO", "www.example.org", "2001:db8::10"],
  ["RESOLVES_TO", "api.example.org", "192.0.2.24"],
  ["RESOLVES_TO", "mail.example.org", "198.51.100.7"],
  ["RESOLVES_TO", "mail.example.org", "2001:db8:1::25"],
  ["WITHIN", "192.0.2.10", "192.0.2.0/24"],
  ["WITHIN", "192.0.2.24", "192.0.2.0/24"],
  ["WITHIN", "2001:db8::10", "2001:db8::/32"],
  ["WITHIN", "2001:db8:1::25", "2001:db8::/32"],
  ["WITHIN", "198.51.100.7", "198.51.100.0/24"],
  ["ANNOUNCED_BY", "192.0.2.0/24", "AS64496"],
  ["ANNOUNCED_BY", "2001:db8::/32", "AS64496"],
  ["ANNOUNCED_BY", "198.51.100.0/24", "AS64500"],
  ["OWNS", "Example Hosting", "AS64496"],
  ["OWNS", "Example Hosting", "192.0.2.0/24"],
  ["HOSTS", "www.example.org", "https://www.example.org/login"],
  ["HAS_SUBDOMAIN", "example.com", "shop.example.com"],
  ["RESOLVES_TO", "shop.example.com", "203.0.113.42"],
  ["WITHIN", "203.0.113.42", "203.0.113.0/24"],
  ["ANNOUNCED_BY", "203.0.113.0/24", "AS64511"],
  ["HOSTS", "shop.example.com", "https://shop.example.com/cart"],
  ["HAS_SUBDOMAIN", "example.net", "cdn.example.net"],
  ["RESOLVES_TO", "cdn.example.net", "203.0.113.43"],
  ["WITHIN", "203.0.113.43", "203.0.113.0/24"],
  ["ALIAS_OF", "cdn.example.net", "shop.example.com"],
];

const CASES = [
  {
    title: "Example infrastructure review",
    purpose:
      "Map the public infrastructure of example.org, example.com and example.net that we operate, " +
      "to find forgotten hosts before the yearly audit.",
    legal_basis: "owned_assets",
    created: "2024-02-12T09:05:00Z",
    updated: "2026-11-17T16:40:00Z",
    demo: true,
  },
  {
    title: "Phishing kit tracking",
    purpose:
      "Track the lookalike domains of a phishing campaign aimed at example.com customers and " +
      "report them to the registrars.",
    legal_basis: "threat_intelligence",
    created: "2026-09-02T13:20:00Z",
    updated: "2026-11-12T08:15:00Z",
  },
  {
    title: "Self-assessment: example.net",
    purpose:
      "Review what example.net exposes to the internet after the migration to the new hosting " +
      "provider.",
    legal_basis: "self_assessment",
    created: "2026-05-18T10:00:00Z",
    updated: "2026-10-30T15:45:00Z",
  },
];

/** [Shuttle, entity value, created, finished]: offline or answered by the test DNS server. */
const JOBS = [
  ["registrable-domain", "shop.example.com", "2026-11-17T16:31:00Z", "2026-11-17T16:31:02Z"],
  ["dns", "example.org", "2026-11-17T16:34:00Z", "2026-11-17T16:34:05Z"],
  ["registrable-domain", "cdn.example.net", "2026-11-17T16:38:00Z", "2026-11-17T16:38:01Z"],
];

const VIEWS = ["Mail and DNS", "Hosting at AS64496", "Shop (example.com)"];

/** An admin's scan profile of an offline Shuttle, run on three subdomains of the demo case. */
const PROFILE = {
  name: "Parent domains",
  description: "Links each subdomain to the domain it belongs to. No network: quick and quiet.",
  shuttles: ["registrable-domain"],
};
const RUN_ON = ["mail.example.org", "shop.example.com", "cdn.example.net"];
const RUN_DATES = ["2026-11-17T16:39:00Z", "2026-11-17T16:39:40Z"];

/** Verified domains of the demo case: [domain, method, status, error]. */
const VERIFICATIONS = [
  ["example.org", "dns_txt", "verified", null],
  ["example.net", "http_file", "failed", "file_not_found"],
];

// --- helpers ------------------------------------------------------------------------------

function run(command, input) {
  return execFileSync("sh", ["-c", command], {
    input,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "inherit"],
  });
}

const sqlText = (value) => `'${String(value).replaceAll("'", "''")}'`;
const cypherText = (value) => `'${String(value).replaceAll("\\", "\\\\").replaceAll("'", "\\'")}'`;

function sql(statements) {
  run(
    `${compose} exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`,
    statements,
  );
}

function cypher(statements) {
  run(
    `${compose} exec -T neo4j sh -c 'cypher-shell -u neo4j -p "\${NEO4J_AUTH#neo4j/}"'`,
    statements,
  );
}

async function apiCall(page, method, apiPath, data) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === "nightloom_csrf")?.value ?? "";
  const response = await page.request.fetch(`/api/v1${apiPath}`, {
    method,
    headers: { "X-CSRF-Token": csrf },
    ...(data === undefined ? {} : { data }),
  });
  if (!response.ok()) {
    throw new Error(`${method} ${apiPath}: ${response.status()} ${await response.text()}`);
  }
  return response.status() === 204 ? null : response.json();
}

async function shot(page, name) {
  // Fonts, the last frame of any transition, no caret or hover marks.
  await page.evaluate(() => document.fonts.ready);
  await page.mouse.move(VIEWPORT.width - 1, VIEWPORT.height - 1);
  await page.waitForTimeout(400);
  const file = path.join(outDir, name);
  await page.screenshot({ path: file, caret: "hide", animations: "disabled" });
  console.log(`captured ${file}`);
}

async function newPage(browser, theme, storageState) {
  const context = await browser.newContext({
    baseURL,
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: theme,
    reducedMotion: "no-preference",
    ...(storageState ? { storageState } : {}),
  });
  await context.addInitScript((value) => {
    window.localStorage.setItem("nightloom.theme", value);
  }, theme);
  return context.newPage();
}

// --- the run ------------------------------------------------------------------------------

mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch();
try {
  // 1. Sign-in (dark), the loom on a fake clock: always the same frame.
  const signin = await newPage(browser, "dark");
  await signin.clock.install({ time: NOW });
  await signin.goto("/");
  await signin.getByLabel("Email").waitFor();
  await signin.waitForLoadState("load");
  for (let i = 0; i < 40; i += 1) {
    await signin.clock.runFor(250);
    if ((await signin.locator(".loom").getAttribute("data-loom")) === "scene") break;
  }
  if ((await signin.locator(".loom").getAttribute("data-loom")) !== "scene") {
    throw new Error("The sign-in loom did not start (no WebGL?)");
  }
  await signin.clock.runFor(6_000);
  await shot(signin, "signin-dark.png");

  await signin.context().close();

  // Sign in for real, on a page with real timers.
  const setup = await newPage(browser, "dark");
  await setup.goto("/");
  await setup.getByLabel("Email").fill(email);
  await setup.getByLabel("Password", { exact: true }).fill(password);
  await setup.getByRole("button", { name: "Sign in" }).click();
  await setup.getByRole("heading", { name: "Cases", level: 1 }).waitFor();
  const storageState = await setup.context().storageState();

  // 2. The demo data, through the API.
  let demoId = "";
  const caseDates = [];
  for (const item of CASES) {
    const created = await apiCall(setup, "POST", "/cases", {
      title: item.title,
      purpose: item.purpose,
      legal_basis: item.legal_basis,
      ethics_acknowledged: true,
    });
    caseDates.push([created.id, item.created, item.updated]);
    if (item.demo) demoId = created.id;
  }

  const keys = new Map();
  for (const [type, value] of ENTITIES) {
    const entity = await apiCall(setup, "POST", `/cases/${demoId}/entities`, { type, value });
    keys.set(value, entity.key);
  }
  for (const [type, source, target] of RELATIONSHIPS) {
    await apiCall(setup, "POST", `/cases/${demoId}/relationships`, {
      type,
      source_key: keys.get(source),
      target_key: keys.get(target),
    });
  }
  await apiCall(setup, "PUT", `/cases/${demoId}/layout`, {
    positions: Object.fromEntries(ENTITIES.map(([, value, , x, y]) => [keys.get(value), [x, y]])),
  });

  // Jobs: start them, then wait until every one has finished.
  const jobIds = [];
  for (const [shuttle, value] of JOBS) {
    const job = await apiCall(setup, "POST", `/cases/${demoId}/jobs`, {
      shuttle,
      entity_key: keys.get(value),
    });
    jobIds.push(job.id);
  }
  for (let attempt = 0; ; attempt += 1) {
    const page = await apiCall(setup, "GET", `/cases/${demoId}/jobs?limit=50`);
    const open = page.items.filter((job) => job.status === "queued" || job.status === "running");
    if (open.length === 0) break;
    if (attempt > 120) throw new Error("The demo jobs did not finish within two minutes");
    await setup.waitForTimeout(1000);
  }

  // A profile run, waited on like the jobs.
  const profile = await apiCall(setup, "POST", "/scan-profiles", PROFILE);
  const scanRun = await apiCall(setup, "POST", `/cases/${demoId}/scan-runs`, {
    profile_id: profile.id,
    entity_keys: RUN_ON.map((value) => keys.get(value)),
  });
  for (let attempt = 0; ; attempt += 1) {
    const current = await apiCall(setup, "GET", `/cases/${demoId}/scan-runs/${scanRun.id}`);
    if (current.status !== "running") break;
    if (attempt > 120) throw new Error("The demo profile run did not finish within two minutes");
    await setup.waitForTimeout(1000);
  }

  // Domains to verify; their state is set below (the stack cannot prove them).
  const verificationIds = [];
  for (const [domain, method] of VERIFICATIONS) {
    const item = await apiCall(setup, "POST", `/cases/${demoId}/verifications`, { domain, method });
    verificationIds.push(item.id);
  }

  // Saved views, through the UI (the snapshot is whatever the graph shows).
  await setup.goto(`/cases/${demoId}/graph`);
  await setup.getByTestId("graph-canvas").waitFor();
  for (const name of VIEWS) {
    await setup.getByRole("button", { name: "Views" }).click();
    await setup.getByRole("menuitem", { name: "Save current view…" }).click();
    await setup.getByLabel("Name").fill(name);
    await setup.getByRole("button", { name: "Save view" }).click();
    await setup.getByRole("dialog").waitFor({ state: "detached" });
  }
  await setup.context().close();

  // 3. Fixed dates: cases, jobs, views, and when each entity and thread was first seen.
  const at = (date) => `${date}T09:00:00Z`;
  sql(
    [
      ...caseDates.map(
        ([id, created, updated]) =>
          `UPDATE cases SET created_at = ${sqlText(created)}, ethics_ack_at = ${sqlText(created)}, ` +
          `updated_at = ${sqlText(updated)} WHERE id = ${sqlText(id)};`,
      ),
      ...jobIds.map((id, index) => {
        const [, , created, finished] = JOBS[index];
        return (
          `UPDATE jobs SET created_at = ${sqlText(created)}, started_at = ${sqlText(created)}, ` +
          `finished_at = ${sqlText(finished)} WHERE id = ${sqlText(id)};`
        );
      }),
      `UPDATE saved_views SET created_at = '2026-11-16T11:00:00Z', ` +
        `updated_at = '2026-11-16T11:00:00Z' WHERE case_id = ${sqlText(demoId)};`,
      `UPDATE scan_runs SET created_at = ${sqlText(RUN_DATES[0])}, ` +
        `finished_at = ${sqlText(RUN_DATES[1])} WHERE id = ${sqlText(scanRun.id)};`,
      `UPDATE jobs SET created_at = ${sqlText(RUN_DATES[0])}, started_at = ${sqlText(RUN_DATES[0])}, ` +
        `finished_at = ${sqlText(RUN_DATES[1])} WHERE run_id = ${sqlText(scanRun.id)};`,
      // checked_at is now, so the stack's daily re-check leaves them alone.
      ...VERIFICATIONS.map(([, , status, error], index) =>
        status === "verified"
          ? `UPDATE ownership_verifications SET status = 'verified', checked_at = now(), ` +
            `verified_at = '2026-11-16T09:30:00Z', expires_at = '2026-12-16T09:30:00Z', ` +
            `addresses = '["192.0.2.10", "2001:db8::10"]'::jsonb, created_at = '2026-11-16T09:10:00Z' ` +
            `WHERE id = ${sqlText(verificationIds[index])};`
          : `UPDATE ownership_verifications SET status = ${sqlText(status)}, checked_at = now(), ` +
            `last_error = ${sqlText(error)}, created_at = '2026-11-17T15:00:00Z' ` +
            `WHERE id = ${sqlText(verificationIds[index])};`,
      ),
    ].join("\n"),
  );
  cypher(
    [
      ...ENTITIES.map(
        ([, value, firstSeen]) =>
          `MATCH (n:Entity {case_id: ${cypherText(demoId)}, key: ${cypherText(keys.get(value))}}) ` +
          `SET n.first_seen = datetime(${cypherText(at(firstSeen))}), ` +
          `n.last_seen = datetime(${cypherText(LAST_SEEN)});`,
      ),
      // A thread was first seen when the later of its two ends was.
      `MATCH (s:Entity {case_id: ${cypherText(demoId)}})-[r]->(t:Entity {case_id: ${cypherText(demoId)}}) ` +
        `SET r.first_seen = CASE WHEN s.first_seen > t.first_seen THEN s.first_seen ELSE t.first_seen END, ` +
        `r.last_seen = datetime(${cypherText(LAST_SEEN)});`,
    ].join("\n"),
  );

  // 4. The screens, on a fixed clock.
  const open = async (theme, url) => {
    const page = await newPage(browser, theme, storageState);
    await page.clock.setFixedTime(NOW);
    await page.goto(url);
    return page;
  };
  /** Captures start at the top of the page: the case header and all of the graph. */
  const showGraph = async (page) => {
    await page.evaluate(() => window.scrollTo(0, 0));
  };
  const graphReady = async (page) => {
    await page.getByTestId("graph-canvas").waitFor();
    await page.getByRole("button", { name: "Views" }).waitFor();
    await showGraph(page);
    await page.waitForTimeout(1500);
  };

  const cases = await open("light", "/cases");
  await cases.getByRole("list", { name: "Cases" }).waitFor();
  await shot(cases, "cases-light.png");
  await cases.context().close();

  for (const theme of ["dark", "light"]) {
    const graph = await open(theme, `/cases/${demoId}/graph`);
    await graphReady(graph);
    await shot(graph, `graph-2d-${theme}.png`);
    await graph.context().close();
  }

  const graph3d = await open("dark", `/cases/${demoId}/graph?mode=3d`);
  await graph3d.locator(".graph3d-canvas").waitFor({ timeout: 30_000 });
  await graph3d.getByRole("group", { name: "Depth" }).waitFor();
  await showGraph(graph3d);
  await graph3d.waitForTimeout(2500);
  await shot(graph3d, "graph-3d-time-dark.png");
  await graph3d.context().close();

  const views = await open("dark", `/cases/${demoId}/graph`);
  await graphReady(views);
  await views.getByRole("button", { name: "Views" }).click();
  await views.getByRole("menu", { name: "Saved views" }).waitFor();
  await views.getByRole("group", { name: VIEWS[0] }).waitFor();
  await shot(views, "saved-views-dark.png");
  await views.context().close();

  const jobs = await open("dark", `/cases/${demoId}/graph`);
  await graphReady(jobs);
  await jobs.getByRole("button", { name: "Jobs" }).click();
  await jobs.getByRole("list", { name: "Job history" }).waitFor();
  await jobs.getByRole("listitem", { name: `${PROFILE.name} on 3 entities` }).waitFor();
  await shot(jobs, "jobs-dark.png");
  await jobs.context().close();

  const settings = await open("light", `/cases/${demoId}/settings`);
  const verified = settings.getByRole("region", { name: "Verified domains" });
  await verified.getByRole("listitem", { name: "example.org" }).waitFor();
  await verified
    .getByRole("listitem", { name: "example.org" })
    .getByRole("button", { name: "Show proof" })
    .click();
  await verified.evaluate((element) => {
    element.scrollIntoView({ block: "start" });
  });
  await shot(settings, "verified-domains-light.png");
  await settings.context().close();

  const profiles = await open("dark", "/admin/profiles");
  await profiles.getByRole("heading", { name: "Scan profiles", level: 1 }).waitFor();
  await profiles.getByText(PROFILE.name).first().waitFor();
  await shot(profiles, "profiles-dark.png");
  await profiles.context().close();
} finally {
  await browser.close();
}
