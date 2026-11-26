// README screenshots (docs/images/), run by `make screenshots` against a
// throwaway stack of its own (see the Makefile): it signs in as the demo
// admin, builds fictional demo cases through the API and captures each
// screen at 1600×1000.
//
// Fictional data only: example.org / example.com / example.net names, the
// RFC 5737 IPv4 and RFC 3849 IPv6 documentation ranges, AS64496–AS64511, an
// invented company (Example Ltd, its LEI made up with valid check digits, its
// Wikidata item the Wikidata Sandbox), no real people. Deterministic: every entity is pinned at a fixed position (no
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

// What the GLEIF, Wikidata, GitHub and HIBP Shuttles would have written.
const ORGANIZATION = {
  legal_name: "EXAMPLE LTD",
  lei: "9845001EXAMPLE0LTD03",
  jurisdiction: "GB",
  country: "GB",
  legal_form: "Private limited company",
  registered_as: "01234567",
  entity_status: "ACTIVE",
  lei_status: "ISSUED",
  gleif_checked_at: "2026-11-12T10:12:00Z",
  wikidata_id: "Q4115189",
  wikidata_checked_at: "2026-11-12T10:13:00Z",
  github_login: "example-ltd",
  github_checked_at: "2026-11-12T10:14:00Z",
};
const SHOP_API = {
  description: "Back end of the Example shop: orders, carts and payments.",
  language: "Python",
  topics: ["shop", "api"],
  license: "Apache-2.0",
  stars: 12,
  forks: 3,
  fork: false,
  archived: false,
  default_branch: "main",
  created_at: "2025-08-04T09:30:00Z",
  pushed_at: "2026-11-09T17:02:00Z",
  github_checked_at: "2026-11-12T10:14:00Z",
};
const WEBSITE = {
  description: "The www.example.org site.",
  language: "TypeScript",
  homepage: "https://www.example.org",
  stars: 4,
  forks: 1,
  fork: false,
  archived: false,
  default_branch: "main",
  created_at: "2024-02-20T11:00:00Z",
  pushed_at: "2026-10-28T08:45:00Z",
  github_checked_at: "2026-11-12T10:14:00Z",
};
const EXPOSURE_COMMIT = "3f9a1c2e7b4d5a6f8091a2b3c4d5e6f708192a3b";
const EXPOSURE = `github.com/example-ltd/shop-api@${EXPOSURE_COMMIT}/config/settings.py#L12`;
const EXPOSURE_PROPERTIES = {
  kind: "Stripe live secret key",
  commit_url: `https://github.com/example-ltd/shop-api/commit/${EXPOSURE_COMMIT}`,
  file_url: `https://github.com/example-ltd/shop-api/blob/${EXPOSURE_COMMIT}/config/settings.py#L12`,
  committed_at: "2026-11-09T16:58:00Z",
};
const BREACH = {
  title: "Example Shop",
  breach_date: "2025-03-14",
  record_count: 48211,
  data_classes: ["Email addresses", "Names", "Purchases"],
  added_at: "2025-04-02T08:00:00Z",
  verified: true,
  fabricated: false,
  spam_list: false,
  malware: false,
};

/** [type, value, first seen (UTC date), x, y, properties?]: the layout is fixed, as pins. */
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
  // A lookalike of example.org with a copy of its login page (typosquats, screenshots).
  ["domain", "examp1e.org", "2026-11-03", -900, 260],
  ["url", "https://examp1e.org/login", "2026-11-03", -780, 70],
  // The company behind the domains, its public code and a breach of its shop (M4 Shuttles).
  ["organization", "Example Ltd", "2026-11-12", 760, 430, ORGANIZATION],
  ["repository", "github.com/example-ltd/shop-api", "2026-11-12", 1020, 330, SHOP_API],
  ["repository", "github.com/example-ltd/website", "2026-11-12", 920, 640, WEBSITE],
  ["secret_exposure", EXPOSURE, "2026-11-13", 1260, 520, EXPOSURE_PROPERTIES],
  ["breach", "ExampleShop", "2026-10-20", 340, 250, BREACH],
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
  ["LOOKALIKE_OF", "examp1e.org", "example.org"],
  ["HOSTS", "examp1e.org", "https://examp1e.org/login"],
  ["OWNS", "Example Ltd", "example.org"],
  ["OWNS", "Example Ltd", "example.com"],
  ["OWNS", "Example Ltd", "example.net"],
  ["HAS_REPOSITORY", "Example Ltd", "github.com/example-ltd/shop-api"],
  ["HAS_REPOSITORY", "Example Ltd", "github.com/example-ltd/website"],
  ["HAS_EXPOSURE", "github.com/example-ltd/shop-api", EXPOSURE],
  ["BREACH_OF", "ExampleShop", "example.com"],
];

/** The login page and its copy on the lookalike: [URL, brand shown on the page]. */
const SCREENSHOTS = [
  ["https://www.example.org/login", "Example"],
  ["https://examp1e.org/login", "Examp1e"],
];
const SCREENSHOT_AT = "2026-11-17T16:20:00+00:00";

/** A certificate alert for the watched example.org: new names seen in CT logs. */
const CT_ALERT = {
  names: ["staging.example.org", "vpn.example.org", "examp1e.org"],
  certificates: 2,
  created: "2026-11-18T09:05:00Z",
};

/** A fake sign-in page, as a site would draw it in the browser service. */
function loginPage(brand) {
  return `<!doctype html><html><head><style>
    body { margin: 0; font: 16px system-ui, sans-serif; background: #eef2f7; color: #1d2733; }
    header { background: #1f4e8c; color: #fff; padding: 18px 40px; font-size: 26px; font-weight: 700; }
    main { width: 380px; margin: 90px auto; background: #fff; padding: 36px; border-radius: 10px;
           box-shadow: 0 6px 24px rgba(0, 0, 0, 0.12); }
    h1 { margin: 0 0 24px; font-size: 24px; }
    label { display: block; margin: 14px 0 6px; font-size: 14px; color: #4a5868; }
    input { width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #c4ccd6;
            border-radius: 6px; font-size: 15px; }
    button { margin-top: 24px; width: 100%; padding: 12px; border: 0; border-radius: 6px;
             background: #1f4e8c; color: #fff; font-size: 16px; }
    footer { text-align: center; color: #7a8694; font-size: 13px; }
  </style></head><body>
    <header>${brand}</header>
    <main><h1>Sign in to your account</h1>
      <label>Email</label><input value="">
      <label>Password</label><input type="password" value="">
      <button>Sign in</button></main>
    <footer>© 2026 ${brand} Ltd · Privacy · Help</footer>
  </body></html>`;
}

/** Stores the pictures as the demo case's evidence (in the api container) and hashes them. */
const STORE_EVIDENCE = `
import asyncio, base64, json, sys, uuid
from nightloom_api.config import get_settings
from nightloom_api.evidence import S3EvidenceStore
from nightloom_api.infra.storage import Storage
from nightloom_sdk.evidence import evidence_name
from nightloom_shuttle_screenshot.picture import read_picture

async def main():
    data = json.load(sys.stdin)
    storage = Storage(get_settings())
    store = S3EvidenceStore(storage, uuid.UUID(data["case"]))
    found = []
    for picture in data["pictures"]:
        png = base64.b64decode(picture)
        name = evidence_name(png, "image/png")
        await store.save(name, png, "image/png")
        read = read_picture(png)
        found.append({"name": name, "phash": read.phash, "width": read.width, "height": read.height})
    await storage.aclose()
    print(json.dumps(found))

asyncio.run(main())
`;

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
  for (const [type, value, , , , properties] of ENTITIES) {
    const entity = await apiCall(setup, "POST", `/cases/${demoId}/entities`, {
      type,
      value,
      ...(properties === undefined ? {} : { properties }),
    });
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

  // Screenshots of the login page and its copy, kept as evidence like the Shuttle does.
  const pictures = [];
  for (const [, brand] of SCREENSHOTS) {
    const drawing = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await drawing.setContent(loginPage(brand));
    pictures.push((await drawing.screenshot({ type: "png" })).toString("base64"));
    await drawing.close();
  }
  const code = Buffer.from(STORE_EVIDENCE).toString("base64");
  const stored = JSON.parse(
    run(
      `${compose} exec -T api python -c "import base64; exec(base64.b64decode('${code}'))"`,
      JSON.stringify({ case: demoId, pictures }),
    )
      .trim()
      .split("\n")
      .at(-1),
  );
  cypher(
    SCREENSHOTS.map(([url], index) => {
      const item = stored[index];
      return (
        `MATCH (n:Entity {case_id: ${cypherText(demoId)}, key: ${cypherText(keys.get(url))}}) ` +
        `SET n.prop_screenshot = ${cypherText(item.name)}, ` +
        `n.prop_screenshot_phash = ${cypherText(item.phash)}, ` +
        `n.prop_screenshot_width = ${String(item.width)}, ` +
        `n.prop_screenshot_height = ${String(item.height)}, ` +
        `n.prop_screenshot_at = ${cypherText(SCREENSHOT_AT)}, ` +
        `n.prop_title = 'Sign in';`
      );
    }).join("\n"),
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

  // A page's screenshot and the case's pages that look like it.
  const looks = await open("light", `/cases/${demoId}/graph`);
  await graphReady(looks);
  await looks.getByRole("combobox", { name: "Search entities" }).fill("www.example.org/login");
  await looks.getByRole("option", { name: /^https:\/\/www\.example\.org\/login/ }).click();
  const details = looks.getByRole("complementary", { name: "Details" });
  const screenshotSection = details.getByRole("region", { name: "Screenshot" });
  await screenshotSection.getByRole("img").waitFor();
  await screenshotSection.getByRole("list", { name: "Pages that look alike" }).waitFor();
  await screenshotSection.evaluate((element) => {
    element.scrollIntoView({ block: "start" });
  });
  await shot(looks, "screenshot-light.png");
  await looks.context().close();

  // The company: its records at GLEIF and Wikidata, then a secret its code exposed.
  const select = async (page, search, text) => {
    await page.getByRole("combobox", { name: "Search entities" }).fill(search);
    await page.getByRole("option").filter({ hasText: text }).first().click();
    return page.getByRole("complementary", { name: "Details" });
  };
  const company = await open("light", `/cases/${demoId}/graph`);
  await graphReady(company);
  const companyDetails = await select(company, "Example Ltd", "Example Ltd");
  const records = companyDetails.getByRole("heading", { name: "Public records" });
  await records.waitFor();
  await records.evaluate((element) => {
    element.scrollIntoView({ block: "center" });
  });
  await shot(company, "organization-light.png");
  await company.context().close();

  const leak = await open("dark", `/cases/${demoId}/graph`);
  await graphReady(leak);
  const leakDetails = await select(leak, "settings.py", "settings.py");
  await leakDetails.getByText(EXPOSURE_PROPERTIES.kind).first().waitFor();
  await showGraph(leak);
  await shot(leak, "exposed-secret-dark.png");
  await leak.context().close();

  // Certificate monitoring: example.org is watched, and its last check found new names.
  // (Added last: the alert shows above the graph in every capture after this.)
  sql(
    [
      `INSERT INTO ct_watches (id, case_id, domain, created_by, created_at, next_check_at, ` +
        `checked_at, baseline_at) SELECT gen_random_uuid(), ${sqlText(demoId)}, 'example.org', ` +
        `id, '2026-11-10T08:00:00Z', now() + interval '1 hour', '2026-11-18T09:05:00Z', ` +
        `'2026-11-10T08:01:00Z' FROM users WHERE email = ${sqlText(email)};`,
      `INSERT INTO ct_alerts (id, case_id, watch_id, domain, created_at, certificates, names, ` +
        `names_total, keys) SELECT gen_random_uuid(), ${sqlText(demoId)}, id, 'example.org', ` +
        `${sqlText(CT_ALERT.created)}, ${String(CT_ALERT.certificates)}, ` +
        `${sqlText(JSON.stringify(CT_ALERT.names))}::jsonb, ${String(CT_ALERT.names.length)}, ` +
        `${sqlText(JSON.stringify([keys.get("examp1e.org")]))}::jsonb ` +
        `FROM ct_watches WHERE case_id = ${sqlText(demoId)};`,
    ].join("\n"),
  );
  const alerts = await open("dark", `/cases/${demoId}/graph`);
  await graphReady(alerts);
  await alerts.getByRole("region", { name: "New certificates" }).waitFor();
  await showGraph(alerts);
  await shot(alerts, "certificate-alerts-dark.png");
  await alerts.context().close();
} finally {
  await browser.close();
}
