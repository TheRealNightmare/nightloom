import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

// The account is created by `make e2e` from these variables; never hard-code
// real credentials here.
const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";
// Screenshots are only taken when a directory outside the repository is given.
const screenshotDir = process.env.NIGHTLOOM_E2E_SCREENSHOT_DIR ?? "";
// The domain the DNS Shuttle runs on. Locally the worker asks the public
// resolvers, so a real domain works; CI starts the stack with the test-only
// DNS server (make up TEST_DNS=1) and sets nightloom-test.example.
const dnsDomain = process.env.NIGHTLOOM_E2E_DNS_DOMAIN ?? "example.org";
// example.org publishes a null MX (no mail), so MX exchangers are only checked
// when the domain has them.
const expectMx = process.env.NIGHTLOOM_E2E_DNS_EXPECT_MX === "1";

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

test("run the DNS Shuttle on a domain from the graph", async ({ page }) => {
  test.setTimeout(120_000);
  const title = `E2E DNS ${Date.now().toString(36)}`;
  const summary = page.getByRole("status").filter({ hasText: /^\d+ entit(y|ies), / });
  const details = page.getByRole("complementary", { name: "Details" });
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto("/cases/new");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByLabel("Title").fill(title);
  await page
    .getByLabel("Purpose")
    .fill("Check that running the DNS Shuttle from the graph works end to end.");
  await page.getByLabel("Legal basis", { exact: true }).selectOption("owned_assets");
  await page.getByRole("checkbox", { name: /I confirm this investigation/ }).check();
  await page.getByRole("button", { name: "Create case" }).click();
  await expect(page).toHaveURL(/\/cases\/[0-9a-f-]{36}\/graph$/);
  const caseUrl = new URL(page.url()).pathname.replace(/\/graph$/, "");

  await page.getByRole("button", { name: "Add your first entity" }).click();
  const dialog = page.getByRole("dialog", { name: "Add entity" });
  await dialog.getByRole("combobox", { name: "Type" }).selectOption({ label: "Domain" });
  await dialog.getByRole("textbox", { name: "Value" }).fill(dnsDomain);
  await dialog.getByRole("button", { name: "Add entity" }).click();
  await expect(dialog).toBeHidden();
  await expect(details.getByRole("heading", { level: 2 })).toHaveText(dnsDomain);

  // The side panel lists the Shuttles for domains; run DNS.
  const card = details.getByRole("listitem", { name: "DNS records" });
  await expect(card.getByText("Passive")).toBeVisible();
  await expect(card.getByText("Network")).toBeVisible();
  await expect(card.getByRole("link", { name: "Terms" })).toBeVisible();
  await card.getByRole("button", { name: "Run DNS records" }).click();

  const jobs = page.getByRole("region", { name: "Jobs" });
  const job = jobs.getByRole("listitem", { name: `dns on ${dnsDomain}` });
  await expect(job).toBeVisible();
  await screenshot(page, "nightloom-run-shuttle.png");

  await expect(job.getByText("Succeeded")).toBeVisible({ timeout: 60_000 });
  await expect(job.getByText(/^Created \d+ · Updated \d+ · Rejected 0$/)).toBeVisible();

  // The graph reloads with what the job found: addresses and name servers.
  await expect(summary).not.toHaveText(/^1 entity, /);
  await expect(page.getByRole("button", { name: /^IP address\s*\d+/ })).toBeVisible();
  await expect(details.getByText(/→ resolves to/).first()).toBeVisible();
  await expect(details.getByText(/→ has ns/).first()).toBeVisible();
  if (expectMx) await expect(details.getByText(/→ has mx/).first()).toBeVisible();
  await expect(details.getByText("dns soa primary ns")).toBeVisible();
  await page.waitForTimeout(1500); // let the short layout settle
  await screenshot(page, "nightloom-dns-results.png");

  // The job stays in the history after a reload.
  await page.reload();
  await page.getByRole("button", { name: /^Jobs/ }).click();
  await expect(job.getByText("Succeeded")).toBeVisible();

  await page.goto(`${caseUrl}/settings`);
  await page.getByLabel(/Type the case title/).fill(title);
  await page.getByRole("button", { name: "Delete case" }).click();
  await expect(page).toHaveURL(/\/cases$/);
  expect(pageErrors).toEqual([]);
});
