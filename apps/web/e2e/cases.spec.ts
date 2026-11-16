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
  // Full-page captures of a scrolled page repeat the sticky top bar mid-image.
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

test("create, use, rename, close, reopen and delete a case", async ({ page }) => {
  // The e2e admin is reused between runs, so make this run's case unique.
  const title = `E2E case ${Date.now().toString(36)}`;
  const renamed = `${title} (renamed)`;

  // Signing in from a deep link lands on it.
  await page.goto("/cases/new");
  await signIn(page);
  await expect(page.getByRole("heading", { name: "New case" })).toBeVisible();

  // The full form: validation first, then every field.
  await page.getByRole("button", { name: "Create case" }).click();
  await expect(page.getByText("Purpose must be at least 30 characters.")).toBeVisible();

  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Description (optional)").fill("Created by the end-to-end tests.");
  await page
    .getByLabel("Purpose")
    .fill("Check that the cases screens work end to end against the real API.");
  await page.getByLabel("Legal basis", { exact: true }).selectOption("authorized_engagement");
  await page.getByLabel("Legal basis note").fill("Internal QA engagement for this deployment.");
  await page.getByRole("switch", { name: "Allow person entities" }).check();
  await expect(page.getByRole("note")).toContainText("Collecting data about people");
  await page
    .getByLabel("Why does this case need person entities?")
    .fill("The test exercises the reason field; no real people are involved.");
  await page.getByLabel("Retention (days, optional)").fill("30");
  await page.getByRole("checkbox", { name: /I confirm this investigation/ }).check();
  await screenshot(page, "nightloom-case-new.png");
  await page.getByRole("button", { name: "Create case" }).click();

  // The new case opens on its (empty) graph.
  await expect(page).toHaveURL(/\/cases\/[0-9a-f-]{36}\/graph$/);
  const caseUrl = new URL(page.url()).pathname.replace(/\/graph$/, "");
  await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
  await expect(page.getByText(/No entities yet/)).toBeVisible();
  await expect(page.getByTestId("graph-canvas")).toHaveCount(0);

  // It appears in the list.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Cases" }).click();
  await page.getByRole("searchbox", { name: "Search titles" }).fill(title);
  const row = page.getByRole("listitem").filter({ hasText: title });
  await expect(row).toBeVisible();
  await expect(row).toContainText("Authorized engagement");
  await expect(row).toContainText("Owner");
  await screenshot(page, "nightloom-cases-list.png");

  // Open and rename it.
  await row.getByRole("link", { name: title }).click();
  await page.getByRole("button", { name: "Rename" }).click();
  await page.getByRole("textbox", { name: "Case title" }).fill(renamed);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: renamed, level: 1 })).toBeVisible();

  // Close and reopen from the settings.
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Close case" }).click();
  await expect(page.getByRole("button", { name: "Reopen case" })).toBeVisible();
  await page.getByRole("button", { name: "Reopen case" }).click();
  await expect(page.getByRole("button", { name: "Close case" })).toBeVisible();

  // The activity shows what happened, newest first.
  await page.getByRole("link", { name: "Activity" }).click();
  const activity = page.getByRole("list", { name: /Case activity/ });
  await expect(activity.getByRole("listitem")).toHaveCount(4);
  await expect(activity.getByRole("listitem").nth(0)).toContainText("reopened the case");
  await expect(activity.getByRole("listitem").nth(1)).toContainText("closed the case");
  await expect(activity.getByRole("listitem").nth(2)).toContainText("changed title");
  await expect(activity.getByRole("listitem").nth(3)).toContainText(
    "created the case with person entities allowed — reason: The test exercises the reason field",
  );
  await screenshot(page, "nightloom-case-activity.png");

  // A deep link survives a full reload (nginx falls back to index.html).
  await page.goto(`${caseUrl}/members`);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Members" })).toBeVisible();
  await expect(page.getByRole("cell", { name: email })).toBeVisible();

  // Delete it, confirming with the exact title.
  await page.getByRole("link", { name: "Settings" }).click();
  const remove = page.getByRole("button", { name: "Delete case" });
  await page.getByLabel(/Type the case title/).fill(title);
  await expect(remove).toBeDisabled();
  await page.getByLabel(/Type the case title/).fill(renamed);
  await remove.click();
  await expect(page).toHaveURL(/\/cases$/);
  await expect(page.getByText(`Deleted “${renamed}”.`)).toBeVisible();

  // Gone from the list, and its address now says "Case not found".
  await page.getByRole("searchbox", { name: "Search titles" }).fill(title);
  await expect(page.getByRole("heading", { name: "No matching cases" })).toBeVisible();
  await page.goto(caseUrl);
  await expect(page.getByRole("heading", { name: "Case not found" })).toBeVisible();
});
