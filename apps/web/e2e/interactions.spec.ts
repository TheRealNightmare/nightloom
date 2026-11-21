import path from "node:path";

import { expect, test, type Locator, type Page } from "@playwright/test";

// The account is created by `make e2e` from these variables; never hard-code
// real credentials here.
const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";
// Screenshots are only taken when a directory outside the repository is given.
const screenshotDir = process.env.NIGHTLOOM_E2E_SCREENSHOT_DIR ?? "";

test.skip(
  email === "" || password === "",
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).",
);

interface Point {
  x: number;
  y: number;
}

async function screenshot(page: Page, name: string) {
  if (screenshotDir === "") return;
  await page.getByTestId("graph-canvas").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(screenshotDir, name) });
}

/** Calls the API as the signed-in user (session cookie and CSRF header). */
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

/** Signs in and opens the graph of a new case with example.org, www. and api. */
async function openCaseGraph(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // "/" opens the case list once the session cookie is set.
  await expect(page.getByRole("heading", { name: "Cases", level: 1 })).toBeVisible();

  const created = (await api(page, "POST", "/cases", {
    title: `E2E interactions ${Date.now().toString(36)}`,
    purpose: "Check the graph's drag, pin, selection and rotation against the real API.",
    legal_basis: "owned_assets",
    ethics_acknowledged: true,
  })) as { id: string };
  for (const value of ["example.org", "www.example.org", "api.example.org"]) {
    await api(page, "POST", `/cases/${created.id}/entities`, { type: "domain", value });
  }
  for (const sub of ["www.example.org", "api.example.org"]) {
    await api(page, "POST", `/cases/${created.id}/relationships`, {
      type: "HAS_SUBDOMAIN",
      source_key: "domain:example.org",
      target_key: `domain:${sub}`,
    });
  }
  await page.goto(`/cases/${created.id}/graph`);
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  // Let the first layout settle.
  await page.waitForTimeout(1500);
  return created.id;
}

function canvas(page: Page): Locator {
  return page.getByRole("application", { name: "Investigation graph" });
}

function announcer(page: Page): Locator {
  return page.getByTestId("graph-announcer");
}

/**
 * Moves the keyboard focus to the entity whose value is `label` and returns
 * where it is drawn (the center of its focus ring).
 */
async function focusEntity(page: Page, label: string): Promise<Point> {
  const graph = canvas(page);
  await graph.focus();
  // Tab cycles: past the last entity focus leaves the graph, and comes back to the first.
  for (let i = 0; i < 8; i += 1) {
    if (!(await graph.evaluate((element) => element === document.activeElement))) {
      await graph.focus();
    }
    await page.keyboard.press("Tab");
    const text = (await announcer(page).textContent()) ?? "";
    if (text.startsWith(`Domain ${label},`)) {
      // The camera may pan a node near an edge into view.
      await page.waitForTimeout(300);
      const box = await page.locator(".graph-focus-ring").boundingBox();
      if (!box) throw new Error("no focus ring");
      return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    }
  }
  throw new Error(`could not focus ${label}`);
}

async function savedLayout(page: Page, caseId: string) {
  return (await api(page, "GET", `/cases/${caseId}/layout`)) as {
    positions: Record<string, [number, number]>;
  };
}

function waitForSave(page: Page) {
  return page.waitForResponse(
    (response) =>
      response.url().endsWith("/layout") &&
      response.request().method() === "PUT" &&
      response.status() === 204,
  );
}

async function dragFrom(page: Page, from: Point, by: Point) {
  await page.mouse.move(from.x, from.y);
  await page.waitForTimeout(100);
  await page.mouse.down();
  await page.mouse.move(from.x + by.x, from.y + by.y, { steps: 12 });
  await page.mouse.up();
}

/** Where the focus ring is now (it follows its node; no refocus, which could pan). */
async function ringCenter(page: Page): Promise<Point> {
  const box = await page.locator(".graph-focus-ring").boundingBox();
  if (!box) throw new Error("no focus ring");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

test("a dragged entity stays pinned where it was dropped after a reload", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const caseId = await openCaseGraph(page);
  const details = page.getByRole("complementary", { name: "Details" });

  const start = await focusEntity(page, "www.example.org");
  const saved = waitForSave(page);
  await dragFrom(page, start, { x: 140, y: -70 });
  await saved;

  const layout = await savedLayout(page, caseId);
  expect(Object.keys(layout.positions)).toEqual(["domain:www.example.org"]);
  const where = layout.positions["domain:www.example.org"];

  // The node is drawn where it was dropped (it still has the keyboard focus).
  const dropped = await ringCenter(page);
  expect(Math.abs(dropped.x - (start.x + 140))).toBeLessThan(3);
  expect(Math.abs(dropped.y - (start.y - 70))).toBeLessThan(3);
  await expect(announcer(page)).toHaveText(/, pinned$/);
  await page.keyboard.press("Enter");
  await expect(details.getByRole("button", { name: "Unpin" })).toBeVisible();
  await screenshot(page, "nl-u3-pinned.png");

  await page.reload();
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await page.waitForTimeout(1500);
  await focusEntity(page, "www.example.org");
  await expect(announcer(page)).toHaveText(/, pinned$/);
  // Still at the same place in the graph.
  expect((await savedLayout(page, caseId)).positions["domain:www.example.org"]).toEqual(where);
  expect(pageErrors).toEqual([]);
});

test("box-select three entities and move them together", async ({ page }) => {
  const caseId = await openCaseGraph(page);
  const details = page.getByRole("complementary", { name: "Details" });
  const box = await canvas(page).boundingBox();
  if (!box) throw new Error("no canvas");

  // Shift+drag across the whole canvas, from an empty corner.
  await page.keyboard.down("Shift");
  await page.mouse.move(box.x + 8, box.y + 8);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 120, box.y + box.height - 8, { steps: 10 });
  await expect(page.locator(".graph-selection")).toBeVisible();
  await screenshot(page, "nl-u3-box-select.png");
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await expect(details.getByRole("heading", { name: "3 entities selected" })).toBeVisible();

  // Drag one of them: all three move and are pinned.
  const apex = await focusEntity(page, "example.org");
  // www keeps the keyboard focus, so its ring shows where it is drawn.
  const www = await focusEntity(page, "www.example.org");
  const saved = waitForSave(page);
  await dragFrom(page, apex, { x: -60, y: 50 });
  await saved;
  await expect(details.getByText("3 pinned.", { exact: false })).toBeVisible();
  const layout = await savedLayout(page, caseId);
  expect(Object.keys(layout.positions).sort()).toEqual([
    "domain:api.example.org",
    "domain:example.org",
    "domain:www.example.org",
  ]);
  const movedWww = await ringCenter(page);
  expect(Math.abs(movedWww.x - (www.x - 60))).toBeLessThan(3);
  expect(Math.abs(movedWww.y - (www.y + 50))).toBeLessThan(3);
});

test("rotate with the buttons and reset the view", async ({ page }) => {
  await openCaseGraph(page);
  // The minimap first: Reset view frames the graph in what the overlays leave
  // free, the minimap included, so the picture to come back to is this one.
  await page.getByRole("button", { name: "View", exact: true }).click();
  await page.getByRole("menuitemcheckbox", { name: "Minimap" }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("graph-minimap")).toBeVisible();
  await page.getByRole("button", { name: "Reset view" }).click();
  await page.waitForTimeout(700);
  const before = await focusEntity(page, "www.example.org");

  await page.getByRole("button", { name: "Rotate right" }).click();
  await page.getByRole("button", { name: "Rotate right" }).click();
  await page.getByRole("button", { name: "Rotate right" }).click();
  await page.waitForTimeout(400);
  const rotated = await focusEntity(page, "www.example.org");
  expect(Math.hypot(rotated.x - before.x, rotated.y - before.y)).toBeGreaterThan(10);

  // Labels stay upright, and the minimap shows the turned viewport.
  await page.waitForTimeout(300);
  await screenshot(page, "nl-u3-rotated-minimap.png");

  await page.getByRole("button", { name: "Reset view" }).click();
  await page.waitForTimeout(700);
  const reset = await focusEntity(page, "www.example.org");
  expect(Math.abs(reset.x - before.x)).toBeLessThan(3);
  expect(Math.abs(reset.y - before.y)).toBeLessThan(3);
});

test("keyboard: Tab to an entity, pin it with P, hear it announced", async ({ page }) => {
  const caseId = await openCaseGraph(page);
  await focusEntity(page, "api.example.org");
  await expect(announcer(page)).toHaveText(/^Domain api\.example\.org, 1 neighbour$/);
  const saved = waitForSave(page);
  await page.keyboard.press("p");
  await expect(announcer(page)).toHaveText("Domain api.example.org, 1 neighbour, pinned");
  await saved;
  expect(Object.keys((await savedLayout(page, caseId)).positions)).toEqual([
    "domain:api.example.org",
  ]);
  await page.keyboard.press("p");
  await expect(announcer(page)).toHaveText("Domain api.example.org, 1 neighbour");

  await page.keyboard.press("?");
  await expect(page.getByRole("dialog", { name: "Graph controls" })).toBeVisible();
  await screenshot(page, "nl-u3-shortcuts.png");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Graph controls" })).toBeHidden();
});
