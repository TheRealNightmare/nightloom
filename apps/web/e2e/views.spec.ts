import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

// Saved views, end to end: an owner saves a view of a case graph; a viewer
// opens its link and sees the same camera, filters and pins; undo, rename
// and delete. The admin account is created by `make e2e` from these
// variables; never hard-code real credentials here. Each run creates one new
// viewer with a unique example.org address (users cannot be deleted).
const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";
// Screenshots are only taken when a directory outside the repository is given.
const screenshotDir = process.env.NIGHTLOOM_E2E_SCREENSHOT_DIR ?? "";

test.skip(
  email === "" || password === "",
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).",
);

test.use({ colorScheme: "dark" });

interface Point {
  x: number;
  y: number;
}

interface View {
  id: string;
  name: string;
  state: {
    camera: { x: number; y: number; ratio: number; angle: number };
    filters: { hidden_types: string[] };
    positions: Record<string, [number, number]>;
  };
}

const POSITIONS: Record<string, [number, number]> = {
  "domain:example.org": [0, 0],
  "domain:www.example.org": [120, 60],
  "domain:api.example.org": [-110, 70],
};

async function screenshot(page: Page, name: string) {
  if (screenshotDir === "") return;
  await page.screenshot({ path: path.join(screenshotDir, name) });
}

/** Calls the API as the signed-in user of `page` (session cookie and CSRF header). */
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

async function signIn(page: Page, address: string, secret: string) {
  await page.goto("/cases");
  await page.getByLabel("Email").fill(address);
  await page.getByLabel("Password", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeHidden();
}

/** Waits until sigma has drawn the graph (the canvas says so), and the first layout settled. */
async function waitForGraph(page: Page) {
  await expect(page.getByTestId("graph-canvas")).toHaveAttribute("data-ready", "true");
  await page.waitForTimeout(800);
}

/** Where `label` is drawn: the center of its keyboard focus ring. */
async function entityOnScreen(page: Page, label: string): Promise<Point> {
  const graph = page.getByRole("application", { name: "Investigation graph" });
  for (let i = 0; i < 8; i += 1) {
    if (!(await graph.evaluate((element) => element === document.activeElement))) {
      await graph.focus();
    }
    await page.keyboard.press("Tab");
    const text = (await page.getByTestId("graph-announcer").textContent()) ?? "";
    if (text.startsWith(`Domain ${label},`)) {
      await page.waitForTimeout(300);
      const box = await page.locator(".graph-focus-ring").boundingBox();
      if (!box) throw new Error("no focus ring");
      // Leave the entity again (Escape), so nothing stays highlighted.
      await page.keyboard.press("Escape");
      return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    }
  }
  throw new Error(`could not focus ${label}`);
}

function viewsMenu(page: Page) {
  return page.getByRole("menu", { name: "Saved views" });
}

test("an owner saves a view; a viewer opens its link and sees the same graph", async ({
  page,
  browser,
}) => {
  // Two sign-ins with Argon2, a password change and a few layouts.
  test.setTimeout(120_000);
  const stamp = Date.now().toString(36);
  const viewerEmail = `e2e-viewer-${stamp}@example.org`;
  const viewerPassword = `e2e viewer passphrase ${stamp}`;

  // --- The owner's case, a viewer, and the owner's pins ---------------------------
  await signIn(page, email, password);
  const created = (await api(page, "POST", "/cases", {
    title: `E2E saved views ${stamp}`,
    purpose: "Check that saved views of the graph are shared with the case's members.",
    legal_basis: "owned_assets",
    ethics_acknowledged: true,
  })) as { id: string };
  const caseId = created.id;
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
  const viewer = (await api(page, "POST", "/users", {
    email: viewerEmail,
    display_name: `E2E Viewer ${stamp}`,
    role: "viewer",
  })) as { temporary_password: string };
  await api(page, "PUT", `/cases/${caseId}/members`, { email: viewerEmail, role: "viewer" });
  await api(page, "PUT", `/cases/${caseId}/layout`, { positions: POSITIONS });

  // --- The owner arranges the graph and saves the view ----------------------------
  await page.goto(`/cases/${caseId}/graph?hide=ip_address`);
  await waitForGraph(page);
  // One step at a time: a click during the animation turns from where it is.
  await page.getByRole("button", { name: "Rotate right" }).click();
  await page.waitForTimeout(400);
  await page.getByRole("button", { name: "Rotate right" }).click();
  await page.waitForTimeout(400);
  const ownerApex = await entityOnScreen(page, "example.org");
  const ownerWww = await entityOnScreen(page, "www.example.org");

  await page.getByRole("button", { name: "Views" }).click();
  await expect(viewsMenu(page)).toBeVisible();
  await expect(
    page.getByText(
      "No saved views yet. Save the current view to share it with this case's members.",
    ),
  ).toBeVisible();
  await page.getByRole("menuitem", { name: "Save current view…" }).click();
  const dialog = page.getByRole("dialog", { name: "Save current view" });
  await expect(dialog.getByRole("textbox", { name: "Name" })).toBeFocused();
  await dialog.getByRole("button", { name: "Save view" }).click();
  await expect(dialog.getByText("Enter a name.")).toBeVisible();
  await dialog.getByRole("textbox", { name: "Name" }).fill("Subdomains");
  await screenshot(page, "nl-u4-save-dialog.png");
  await dialog.getByRole("button", { name: "Save view" }).click();
  await expect(dialog).toBeHidden();

  const [saved] = (await api(page, "GET", `/cases/${caseId}/views`)) as View[];
  if (!saved) throw new Error("the view was not saved");
  expect(saved.name).toBe("Subdomains");
  const view = (await api(page, "GET", `/cases/${caseId}/views/${saved.id}`)) as View;
  expect(view.state.positions).toEqual(POSITIONS);
  expect(view.state.filters.hidden_types).toEqual(["ip_address"]);
  // Two steps of 15°.
  expect(Math.abs(view.state.camera.angle)).toBeCloseTo(Math.PI / 6, 2);

  // The same name again is refused, with the server's message.
  await page.getByRole("button", { name: "Views" }).click();
  await expect(page.getByRole("menuitem", { name: "Apply Subdomains" })).toBeVisible();
  await screenshot(page, "nl-u4-views-menu.png");
  await page.getByRole("menuitem", { name: "Save current view…" }).click();
  await dialog.getByRole("textbox", { name: "Name" }).fill("SUBDOMAINS");
  await dialog.getByRole("button", { name: "Save view" }).click();
  await expect(dialog.getByText(/already exists in this case/)).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();

  // --- The viewer opens the link ---------------------------------------------------
  const viewerContext = await browser.newContext({ colorScheme: "dark" });
  const viewerPage = await viewerContext.newPage();
  await signIn(viewerPage, viewerEmail, viewer.temporary_password);
  await viewerPage
    .getByLabel("Temporary password", { exact: true })
    .fill(viewer.temporary_password);
  await viewerPage.getByLabel("New password", { exact: true }).fill(viewerPassword);
  await viewerPage.getByLabel("Confirm new password", { exact: true }).fill(viewerPassword);
  await viewerPage.getByRole("button", { name: "Set new password" }).click();
  await expect(viewerPage.getByRole("heading", { name: "Cases", level: 1 })).toBeVisible();

  await viewerPage.goto(`/cases/${caseId}/graph?view=${view.id}`);
  await expect(viewerPage.getByTestId("graph-canvas")).toHaveAttribute("data-ready", "true");
  // Applied, and the link left the URL (the view's filters are there instead).
  await expect(viewerPage.getByText("Applied “Subdomains”")).toBeVisible();
  await expect(viewerPage).toHaveURL(new RegExp(`/cases/${caseId}/graph\\?hide=ip_address$`));
  await viewerPage.waitForTimeout(600);
  await screenshot(viewerPage, "nl-u4-applied-toast.png");
  // The viewer's own pins are now the view's (saved through their layout).
  await expect
    .poll(
      async () =>
        ((await api(viewerPage, "GET", `/cases/${caseId}/layout`)) as View["state"]).positions,
    )
    .toEqual(POSITIONS);
  // Same pins and the same camera: the entities are where the owner saw them.
  const viewerApex = await entityOnScreen(viewerPage, "example.org");
  const viewerWww = await entityOnScreen(viewerPage, "www.example.org");
  expect(Math.abs(viewerApex.x - ownerApex.x)).toBeLessThan(4);
  expect(Math.abs(viewerApex.y - ownerApex.y)).toBeLessThan(4);
  expect(Math.abs(viewerWww.x - ownerWww.x)).toBeLessThan(4);
  expect(Math.abs(viewerWww.y - ownerWww.y)).toBeLessThan(4);

  // Viewers may apply, nothing else.
  await viewerPage.getByRole("button", { name: "Views" }).click();
  await expect(viewerPage.getByRole("menuitem", { name: "Apply Subdomains" })).toBeVisible();
  await expect(viewerPage.getByRole("menuitem", { name: /More actions/ })).toHaveCount(0);
  await expect(viewerPage.getByRole("menuitem", { name: "Save current view…" })).toHaveCount(0);
  await viewerPage.keyboard.press("Escape");
  await expect(viewerPage.getByRole("button", { name: "Views" })).toBeFocused();

  // --- Undo brings back what the viewer had -----------------------------------------
  await viewerPage.getByRole("button", { name: "Undo" }).click();
  await expect(viewerPage).toHaveURL(new RegExp(`/cases/${caseId}/graph$`));
  await expect
    .poll(
      async () =>
        ((await api(viewerPage, "GET", `/cases/${caseId}/layout`)) as View["state"]).positions,
    )
    .toEqual({});
  await viewerContext.close();

  // --- The owner renames and deletes it -----------------------------------------------
  await page.getByRole("button", { name: "Views" }).click();
  await page.getByRole("menuitem", { name: "More actions for Subdomains" }).click();
  await page.getByRole("menuitem", { name: "Rename…" }).click();
  const rename = page.getByRole("dialog", { name: "Rename view" });
  await rename.getByRole("textbox", { name: "Name" }).fill("Apex and subdomains");
  await rename.getByRole("button", { name: "Rename" }).click();
  await expect(rename).toBeHidden();

  // The light theme, for the record.
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  // Let the colors finish their transition.
  await page.waitForTimeout(800);
  await page.getByRole("button", { name: "Views" }).click();
  await expect(page.getByRole("menuitem", { name: "Apply Apex and subdomains" })).toBeVisible();
  await screenshot(page, "nl-u4-views-menu-light.png");

  await page.getByRole("menuitem", { name: "More actions for Apex and subdomains" }).click();
  await page.getByRole("menuitem", { name: "Delete…" }).click();
  const confirm = page.getByRole("dialog", { name: "Delete “Apex and subdomains”?" });
  await confirm.getByRole("button", { name: "Delete view" }).click();
  await expect(confirm).toBeHidden();
  expect(await api(page, "GET", `/cases/${caseId}/views`)).toEqual([]);
  await page.getByRole("button", { name: "Views" }).click();
  await expect(page.getByText(/^No saved views yet\./)).toBeVisible();
});
