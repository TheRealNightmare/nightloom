import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

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

async function screenshot(page: Page, name: string) {
  if (screenshotDir === "") return;
  await page.evaluate(() => {
    window.scrollTo(0, 0);
  });
  await page.screenshot({ path: path.join(screenshotDir, name), fullPage: true });
}

async function signIn(page: Page) {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function addEntity(page: Page, type: string, value: string) {
  const dialog = page.getByRole("dialog", { name: "Add entity" });
  await dialog.getByRole("combobox", { name: "Type" }).selectOption({ label: type });
  await dialog.getByRole("textbox", { name: "Value" }).fill(value);
  await dialog.getByRole("button", { name: "Add entity" }).click();
  await expect(dialog).toBeHidden();
}

async function addRelationship(page: Page, from: string, to: string, relation: string) {
  await page.getByRole("button", { name: "Add relationship" }).click();
  const dialog = page.getByRole("dialog", { name: "Add relationship" });
  for (const [label, value] of [
    ["From", from],
    ["To", to],
  ] as const) {
    await dialog.getByRole("combobox", { name: label }).fill(value);
    await dialog
      .getByRole("option", { name: new RegExp(`^${value.replaceAll(".", "\\.")}`) })
      .click();
  }
  await dialog.getByRole("combobox", { name: "Relationship" }).selectOption({ label: relation });
  await dialog.getByRole("button", { name: "Add relationship" }).click();
  await expect(dialog).toBeHidden();
}

async function selectEntity(page: Page, value: string) {
  await page.getByRole("combobox", { name: "Search entities" }).fill(value);
  await page.getByRole("option", { name: new RegExp(`^${value.replaceAll(".", "\\.")}`) }).click();
  await expect(
    page.getByRole("complementary", { name: "Details" }).getByRole("heading", { level: 2 }),
  ).toHaveText(value);
}

test("add, connect and delete entities in a case graph", async ({ page }) => {
  const title = `E2E graph ${Date.now().toString(36)}`;
  // The accessible summary above the graph ("2 entities, 1 relationship").
  const summary = page.getByRole("status").filter({ hasText: /^\d+ entit(y|ies), / });
  const details = page.getByRole("complementary", { name: "Details" });
  // Rendering errors that React recovers from are invisible otherwise.
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto("/cases/new");
  await signIn(page);
  await page.getByLabel("Title").fill(title);
  await page
    .getByLabel("Purpose")
    .fill("Check that the case graph works end to end against the real API.");
  await page.getByLabel("Legal basis", { exact: true }).selectOption("owned_assets");
  await page.getByRole("checkbox", { name: /I confirm this investigation/ }).check();
  await page.getByRole("button", { name: "Create case" }).click();
  await expect(page).toHaveURL(/\/cases\/[0-9a-f-]{36}\/graph$/);
  const caseUrl = new URL(page.url()).pathname.replace(/\/graph$/, "");

  // Empty state.
  await expect(page.getByRole("heading", { name: "No entities yet." })).toBeVisible();
  await expect(page.getByText(/Automated collection \(Shuttles\) comes next/)).toBeVisible();
  await expect(summary).toHaveText("0 entities, 0 relationships");
  await expect(page.getByText("Demo data")).toHaveCount(0);

  // The first entity: normalized by the server, then selected in the graph.
  await page.getByRole("button", { name: "Add your first entity" }).click();
  await addEntity(page, "Domain", "Example.ORG.");
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(details.getByRole("heading", { level: 2 })).toHaveText("example.org");
  await expect(details.getByText("domain:example.org")).toBeVisible();
  await expect(summary).toHaveText("1 entity, 0 relationships");

  await page.getByRole("button", { name: "Add entity" }).click();
  await addEntity(page, "Domain", "www.example.org");
  await addRelationship(page, "example.org", "www.example.org", "has subdomain");
  await expect(summary).toHaveText("2 entities, 1 relationship");

  await selectEntity(page, "example.org");
  await expect(details.getByTestId("neighbour-count")).toHaveText("1");
  await expect(details.getByRole("heading", { name: "Connections (1)" })).toBeVisible();
  await expect(details.getByRole("button", { name: "B2" })).toBeVisible();

  // A couple more for the screenshot.
  await page.getByRole("button", { name: "Add entity" }).click();
  await addEntity(page, "IP address", "192.0.2.1");
  await addRelationship(page, "www.example.org", "192.0.2.1", "resolves to");
  await expect(summary).toHaveText("3 entities, 2 relationships");
  await page.waitForTimeout(1000); // let the short layout settle
  await screenshot(page, "nightloom-case-graph.png");

  // Person-related types are disabled in a case that does not allow them.
  await page.getByRole("button", { name: "Add entity" }).click();
  const dialog = page.getByRole("dialog", { name: "Add entity" });
  const emailOption = dialog.getByRole("option", { name: /^Email/ });
  await expect(emailOption).toBeDisabled();
  await expect(emailOption).toHaveText("Email (not allowed in this case)");
  await expect(dialog.getByText(/Person-related types are disabled/)).toBeVisible();
  await dialog.getByRole("textbox", { name: "Value" }).fill("api.example.org");
  await screenshot(page, "nightloom-add-entity.png");
  await dialog.getByRole("button", { name: "Cancel" }).click();

  // Closing the case makes the graph read-only.
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Close case" }).click();
  await expect(page.getByRole("button", { name: "Reopen case" })).toBeVisible();
  await page.getByRole("link", { name: "Graph" }).click();
  await expect(page.getByText(/This case is closed, so its graph is read-only/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Add entity" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add relationship" })).toHaveCount(0);
  await selectEntity(page, "www.example.org");
  await expect(details.getByRole("button", { name: "Delete entity" })).toHaveCount(0);

  // Reopened, an entity can be deleted again.
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Reopen case" }).click();
  await expect(page.getByRole("button", { name: "Close case" })).toBeVisible();
  await page.getByRole("link", { name: "Graph" }).click();
  await expect(page.getByRole("button", { name: "Add entity" })).toBeVisible();
  await selectEntity(page, "www.example.org");
  await details.getByRole("button", { name: "Delete entity" }).click();
  const confirm = page.getByRole("dialog", { name: "Delete entity?" });
  await confirm.getByRole("button", { name: "Delete entity" }).click();
  await expect(confirm).toBeHidden();
  await expect(summary).toHaveText("2 entities, 0 relationships");

  // The graph survives a reload; then clean up.
  await page.reload();
  await expect(summary).toHaveText("2 entities, 0 relationships");
  await page.goto(`${caseUrl}/settings`);
  await page.getByLabel(/Type the case title/).fill(title);
  await page.getByRole("button", { name: "Delete case" }).click();
  await expect(page).toHaveURL(/\/cases$/);
  expect(pageErrors).toEqual([]);
});
