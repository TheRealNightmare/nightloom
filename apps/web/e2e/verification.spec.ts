import { expect, test } from "@playwright/test";

// The account is created by `make e2e` from these variables.
const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";
// The test-only DNS server's zone in CI: it has no _nightloom-challenge record.
const dnsDomain = process.env.NIGHTLOOM_E2E_DNS_DOMAIN ?? "example.org";

test.skip(
  email === "" || password === "",
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).",
);

test("an owner adds a domain to verify, checks it and removes it", async ({ page }) => {
  test.setTimeout(120_000);
  const title = `E2E verification ${Date.now().toString(36)}`;
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto("/cases/new");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Purpose").fill("Check that verifying a domain we own works end to end.");
  await page.getByLabel("Legal basis", { exact: true }).selectOption("owned_assets");
  await page.getByRole("checkbox", { name: /I confirm this investigation/ }).check();
  await page.getByRole("button", { name: "Create case" }).click();
  await expect(page).toHaveURL(/\/cases\/[0-9a-f-]{36}\/graph$/);
  const caseUrl = new URL(page.url()).pathname.replace(/\/graph$/, "");

  await page.goto(`${caseUrl}/settings`);
  const section = page.getByRole("region", { name: "Verified domains" });
  await section.getByLabel("Domain", { exact: true }).fill(dnsDomain);
  await section.getByRole("button", { name: "Add domain" }).click();
  const item = section.getByRole("listitem", { name: dnsDomain });
  await expect(item.getByText("Not verified yet")).toBeVisible();
  await expect(item.getByText(`_nightloom-challenge.${dnsDomain}`)).toBeVisible();
  await expect(item.getByText(/^nightloom-verification=/)).toBeVisible();

  // No such record: the worker's check reports it.
  await item.getByRole("button", { name: `Check ${dnsDomain} now` }).click();
  await expect(item.getByText("Not found")).toBeVisible({ timeout: 60_000 });
  await expect(item.getByText(/No TXT record with the token/)).toBeVisible();

  await item.getByRole("button", { name: `Remove ${dnsDomain}` }).click();
  await page
    .getByRole("dialog", { name: `Remove ${dnsDomain}?` })
    .getByRole("button", { name: "Remove" })
    .click();
  await expect(section.getByText(/No domains yet/)).toBeVisible();

  await page.getByLabel(/Type the case title/).fill(title);
  await page.getByRole("button", { name: "Delete case" }).click();
  await expect(page).toHaveURL(/\/cases$/);
  expect(pageErrors).toEqual([]);
});
