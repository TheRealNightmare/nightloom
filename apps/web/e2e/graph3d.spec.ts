import path from "node:path";

import { expect, test, type Locator, type Page } from "@playwright/test";

// The 3D view (U5) in real Chromium with WebGL (SwiftShader when there is no
// GPU; see playwright.config.ts). The account is created by `make e2e` from
// these variables; never hard-code real credentials here.
const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";
// Screenshots are only taken when a directory outside the repository is given.
const screenshotDir = process.env.NIGHTLOOM_E2E_SCREENSHOT_DIR ?? "";
// The frame-rate run builds a 2,000-entity case through the API (a minute or two).
const measureFps = process.env.NIGHTLOOM_E2E_FPS === "1";

test.skip(
  email === "" || password === "",
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).",
);

// Tall enough to show the whole graph stage below the case header.
test.use({ colorScheme: "dark", viewport: { width: 1280, height: 1100 } });

interface Point {
  x: number;
  y: number;
}

async function screenshot(page: Page, name: string) {
  if (screenshotDir === "") return;
  await page.screenshot({ path: path.join(screenshotDir, name) });
}

async function api(page: Page, method: string, apiPath: string, data?: unknown) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === "nightloom_csrf")?.value ?? "";
  const response = await page.request.fetch(`/api/v1${apiPath}`, {
    method,
    headers: { "X-CSRF-Token": csrf },
    ...(data === undefined ? {} : { data }),
  });
  expect(response.ok(), `${method} ${apiPath}: ${await response.text()}`).toBe(true);
  return response.status() === 204 ? null : ((await response.json()) as unknown);
}

/** Records CSP violations (and page errors) from the first script on. */
async function watchForViolations(page: Page): Promise<string[]> {
  const problems: string[] = [];
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (/Content.Security.Policy|Refused to/i.test(message.text())) {
      problems.push(`console: ${message.text()}`);
    }
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      console.error(`Refused to: CSP ${event.violatedDirective} ${event.blockedURI}`);
    });
  });
  return problems;
}

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Cases", level: 1 })).toBeVisible();
}

async function newCase(page: Page, title: string): Promise<string> {
  const created = (await api(page, "POST", "/cases", {
    title: `${title} ${Date.now().toString(36)}`,
    purpose: "Check the 3D graph view against the real API.",
    legal_basis: "owned_assets",
    ethics_acknowledged: true,
  })) as { id: string };
  return created.id;
}

/** A case with example.org, www. and api., signed in, its graph open in 2D. */
async function openSmallCase(page: Page): Promise<string> {
  await signIn(page);
  const caseId = await newCase(page, "E2E 3D");
  for (const value of ["example.org", "www.example.org", "api.example.org"]) {
    await api(page, "POST", `/cases/${caseId}/entities`, { type: "domain", value });
  }
  for (const sub of ["www.example.org", "api.example.org"]) {
    await api(page, "POST", `/cases/${caseId}/relationships`, {
      type: "HAS_SUBDOMAIN",
      source_key: "domain:example.org",
      target_key: `domain:${sub}`,
    });
  }
  await page.goto(`/cases/${caseId}/graph?mode=2d`);
  await expect(page.getByTestId("graph-canvas")).toHaveAttribute("data-ready", "true");
  await page.waitForTimeout(1200);
  return caseId;
}

function canvas(page: Page): Locator {
  return page.getByRole("application", { name: "Investigation graph" });
}

async function switchTo3d(page: Page) {
  await page.getByRole("button", { name: "3D" }).click();
  await expect(canvas(page)).toHaveAttribute("data-mode", "3d");
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  // The first frames frame the graph.
  await page.waitForTimeout(600);
}

/** Tabs to the entity `label` and returns the center of its focus ring (page pixels). */
async function focusEntity(page: Page, label: string): Promise<Point> {
  const graph = canvas(page);
  for (let i = 0; i < 8; i += 1) {
    if (!(await graph.evaluate((element) => element === document.activeElement))) {
      await graph.focus();
    }
    await page.keyboard.press("Tab");
    const text = (await page.getByTestId("graph-announcer").textContent()) ?? "";
    if (text.startsWith(`Domain ${label},`)) {
      await page.waitForTimeout(350);
      const box = await page.locator(".graph-focus-ring").boundingBox();
      if (!box) throw new Error("no focus ring");
      return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    }
  }
  throw new Error(`could not focus ${label}`);
}

async function frame(page: Page): Promise<number> {
  return Number((await canvas(page).getAttribute("data-frame")) ?? "0");
}

test("switch to 3D, orbit and zoom, with no CSP violation", async ({ page }) => {
  const problems = await watchForViolations(page);
  await openSmallCase(page);
  await switchTo3d(page);
  expect(page.url()).toContain("mode=3d");
  // The ruler shows dates in Time depth (and the "Undated" plane only if needed).
  await expect(page.locator(".graph3d-ruler__mark:not([hidden])").first()).toBeVisible();
  await expect(
    page.locator(".graph3d-label:not([hidden])", { hasText: "example.org" }).first(),
  ).toBeVisible();

  const box = await canvas(page).boundingBox();
  if (!box) throw new Error("no canvas");
  const before = await frame(page);
  // Orbit: drag on empty space (the top-left corner has no entity).
  await page.mouse.move(box.x + 30, box.y + 30);
  await page.mouse.down();
  await page.mouse.move(box.x + 230, box.y + 80, { steps: 15 });
  await page.mouse.up();
  // Zoom toward the pointer.
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -240);
  await page.waitForTimeout(400);
  expect(await frame(page)).toBeGreaterThan(before);

  // Screenshots: the whole graph (0 frames it), in both themes, then from the side.
  await canvas(page).focus();
  await page.keyboard.press("0");
  await page.waitForTimeout(700);
  await screenshot(page, "nl-u5-3d-dark.png");
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.waitForTimeout(400);
  await screenshot(page, "nl-u5-3d-light.png");
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await canvas(page).focus();
  for (let i = 0; i < 4; i += 1) await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("0");
  for (let i = 0; i < 4; i += 1) await page.keyboard.press("ArrowLeft");
  await page.waitForTimeout(700);
  await screenshot(page, "nl-u5-time-depth.png");
  expect(problems).toEqual([]);
});

test("drag an entity in 3D: it stays pinned after a reload, in 3D and in 2D", async ({ page }) => {
  const caseId = await openSmallCase(page);
  await switchTo3d(page);
  const start = await focusEntity(page, "www.example.org");
  const saved = page.waitForResponse(
    (response) => response.url().endsWith("/layout") && response.request().method() === "PUT",
  );
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(start.x + 120, start.y - 60, { steps: 12 });
  await page.mouse.up();
  await saved;
  const layout = (await api(page, "GET", `/cases/${caseId}/layout`)) as {
    positions: Record<string, [number, number]>;
  };
  expect(Object.keys(layout.positions)).toEqual(["domain:www.example.org"]);
  await focusEntity(page, "www.example.org");
  await expect(page.getByTestId("graph-announcer")).toHaveText(/, pinned$/);
  await screenshot(page, "nl-u5-drag-pin.png");

  await page.reload();
  await expect(canvas(page)).toHaveAttribute("data-mode", "3d");
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await page.waitForTimeout(800);
  await focusEntity(page, "www.example.org");
  await expect(page.getByTestId("graph-announcer")).toHaveText(/, pinned$/);

  await page.getByRole("button", { name: "2D" }).click();
  await expect(canvas(page)).not.toHaveAttribute("data-mode", "3d");
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await page.waitForTimeout(800);
  await focusEntity(page, "www.example.org");
  await expect(page.getByTestId("graph-announcer")).toHaveText(/, pinned$/);
});

test("keyboard in 3D: Tab to an entity and pin it with P", async ({ page }) => {
  await openSmallCase(page);
  await switchTo3d(page);
  await focusEntity(page, "api.example.org");
  await expect(page.getByTestId("graph-announcer")).toHaveText(
    "Domain api.example.org, 1 neighbour",
  );
  await page.keyboard.press("p");
  await expect(page.getByTestId("graph-announcer")).toHaveText(
    "Domain api.example.org, 1 neighbour, pinned",
  );
  await page.keyboard.press("?");
  await expect(
    page.getByRole("heading", { name: "Keyboard (with the 3D graph focused)" }),
  ).toBeVisible();
});

test("save a 3D view in Type depth and apply it from 2D", async ({ page }) => {
  const caseId = await openSmallCase(page);
  await switchTo3d(page);
  await page.getByRole("button", { name: "Type" }).click();
  await expect(page.locator(".graph3d-ruler__mark", { hasText: "Domains" })).toBeAttached();
  await page.getByRole("button", { name: "Views" }).click();
  await page.getByRole("menuitem", { name: "Save current view…" }).click();
  const dialog = page.getByRole("dialog", { name: "Save current view" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("Depth by type");
  await dialog.getByRole("button", { name: "Save view" }).click();
  await expect(dialog).toBeHidden();
  const [saved] = (await api(page, "GET", `/cases/${caseId}/views`)) as { id: string }[];
  if (!saved) throw new Error("the view was not saved");
  const view = (await api(page, "GET", `/cases/${caseId}/views/${saved.id}`)) as {
    state: { version: number; mode: string; depth: string; camera3d?: unknown };
  };
  expect(view.state).toMatchObject({ version: 2, mode: "3d", depth: "type" });
  expect(view.state.camera3d).toBeTruthy();

  await page.getByRole("button", { name: "2D" }).click();
  await expect(canvas(page)).not.toHaveAttribute("data-mode", "3d");
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await page.getByRole("button", { name: "Views" }).click();
  await page.getByRole("menuitem", { name: "Apply Depth by type" }).click();
  await expect(canvas(page)).toHaveAttribute("data-mode", "3d");
  await expect(page.getByRole("button", { name: "Type" })).toHaveAttribute("aria-pressed", "true");
});

test("without WebGL, 3D falls back to 2D with a notice", async ({ page }) => {
  await page.addInitScript(() => {
    // No WebGL context for the 3D view (its support check uses a detached
    // canvas; three draws on .graph3d-canvas); the 2D view's sigma keeps its.
    const prototype = HTMLCanvasElement.prototype as unknown as {
      getContext: (this: HTMLCanvasElement, type: string, ...rest: unknown[]) => unknown;
    };
    const original = prototype.getContext;
    prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
      const for3d = !this.isConnected || this.classList.contains("graph3d-canvas");
      return for3d && (type === "webgl" || type === "webgl2")
        ? null
        : original.call(this, type, ...rest);
    };
  });
  await openSmallCase(page);
  await page.getByRole("button", { name: "3D" }).click();
  await expect(
    page.getByText("3D needs WebGL, which isn’t available in this browser — showing 2D."),
  ).toBeVisible();
  await expect(canvas(page)).not.toHaveAttribute("data-mode", "3d");
  await screenshot(page, "nl-u5-no-webgl.png");
});

test("frame rate with 2,000 entities and 4,000 relationships", async ({ page }) => {
  test.skip(!measureFps, "Set NIGHTLOOM_E2E_FPS=1 to build the large case and measure.");
  test.setTimeout(15 * 60_000);
  await signIn(page);
  const caseId = await newCase(page, "E2E 3D frame rate");
  const post = (apiPath: string, data: unknown) =>
    api(page, "POST", `/cases/${caseId}${apiPath}`, data);
  const batches = async (count: number, make: (i: number) => Promise<unknown>) => {
    for (let start = 0; start < count; start += 25) {
      await Promise.all(
        Array.from({ length: Math.min(25, count - start) }, (_, k) => make(start + k)),
      );
    }
  };
  // 1,000 domains, each resolving to 4 of 1,000 documentation addresses.
  await batches(1000, (i) =>
    post("/entities", { type: "domain", value: `d${String(i)}.example.org` }),
  );
  const ip = (i: number) => `198.51.${String(Math.floor(i / 250))}.${String((i % 250) + 1)}`;
  await batches(1000, (i) => post("/entities", { type: "ip_address", value: ip(i) }));
  await batches(4000, (i) =>
    post("/relationships", {
      type: "RESOLVES_TO",
      source_key: `domain:d${String(Math.floor(i / 4))}.example.org`,
      target_key: `ip_address:${ip((Math.floor(i / 4) + (i % 4) * 7) % 1000)}`,
    }),
  );

  await page.goto(`/cases/${caseId}/graph?mode=3d`);
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await expect(page.getByText("2,000 entities, 4,000 relationships")).toBeVisible();
  await page.waitForTimeout(6000);
  const box = await canvas(page).boundingBox();
  if (!box) throw new Error("no canvas");
  await page.mouse.move(box.x + 20, box.y + 20);
  await page.mouse.down();
  const startFrame = await frame(page);
  const started = Date.now();
  for (let i = 0; i < 120; i += 1) {
    await page.mouse.move(box.x + 20 + (i % 60) * 6, box.y + 20 + Math.abs(30 - (i % 60)) * 2);
  }
  const seconds = (Date.now() - started) / 1000;
  await page.mouse.up();
  const fps = ((await frame(page)) - startFrame) / seconds;
  console.info(`3D orbit with 2,000 / 4,000: ${fps.toFixed(1)} frames per second`);
  expect(fps).toBeGreaterThan(5);
});
