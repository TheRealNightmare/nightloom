import { expect, test } from "@playwright/test";

// The account (an admin) is created by `make e2e` from these variables.
const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";
// The DNS Shuttle's domain: the test-only DNS server's zone in CI.
const dnsDomain = process.env.NIGHTLOOM_E2E_DNS_DOMAIN ?? "example.org";

test.skip(
  email === "" || password === "",
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).",
);

test("an admin adds a scan profile and runs it on a domain", async ({ page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now().toString(36);
  const profileName = `E2E local ${stamp}`;
  const title = `E2E profile run ${stamp}`;
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto("/admin/profiles");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  // The built-in profiles are listed; add one that only asks local sources.
  const table = page.getByRole("table", { name: "Scan profiles" });
  await expect(table.getByText("Quick domain recon")).toBeVisible();
  await page.getByRole("button", { name: "New profile" }).click();
  const dialog = page.getByRole("dialog", { name: "New scan profile" });
  await dialog.getByLabel("Name").fill(profileName);
  await dialog.getByRole("checkbox", { name: /Registrable domain/ }).check();
  await dialog.getByRole("checkbox", { name: /DNS records/ }).check();
  await dialog.getByRole("button", { name: "Save profile" }).click();
  await expect(page.getByRole("status")).toHaveText(`Saved “${profileName}”.`);

  // A case with one domain.
  await page.goto("/cases/new");
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Purpose").fill("Check that running a scan profile works end to end.");
  await page.getByLabel("Legal basis", { exact: true }).selectOption("owned_assets");
  await page.getByRole("checkbox", { name: /I confirm this investigation/ }).check();
  await page.getByRole("button", { name: "Create case" }).click();
  await expect(page).toHaveURL(/\/cases\/[0-9a-f-]{36}\/graph$/);
  const caseUrl = new URL(page.url()).pathname.replace(/\/graph$/, "");
  await page.getByRole("button", { name: "Add your first entity" }).click();
  const add = page.getByRole("dialog", { name: "Add entity" });
  await add.getByRole("combobox", { name: "Type" }).selectOption({ label: "Domain" });
  await add.getByRole("textbox", { name: "Value" }).fill(dnsDomain);
  await add.getByRole("button", { name: "Add entity" }).click();
  await expect(add).toBeHidden();

  // Run the new profile on it.
  const details = page.getByRole("complementary", { name: "Details" });
  const section = details.getByRole("region", { name: "Run a profile" });
  await section.getByRole("combobox", { name: "Profile" }).selectOption({ label: profileName });
  await section.getByRole("button", { name: `Run on ${dnsDomain}` }).click();
  await expect(section.getByRole("status")).toHaveText(/^Queued 2 jobs\./);

  // The Jobs panel shows the run, then its two jobs done.
  const run = page
    .getByRole("region", { name: "Jobs" })
    .getByRole("listitem", { name: `${profileName} on 1 entity` });
  await expect(run.getByText("Finished")).toBeVisible({ timeout: 60_000 });
  await expect(run.getByTestId("run-progress")).toHaveText("2 of 2 done");
  await run.getByRole("button", { name: "Show its jobs (2)" }).click();
  await expect(run.getByRole("list", { name: /^Jobs of / }).getByText("Succeeded")).toHaveCount(2);

  // Clean up: the case, then the profile.
  await page.goto(`${caseUrl}/settings`);
  await page.getByLabel(/Type the case title/).fill(title);
  await page.getByRole("button", { name: "Delete case" }).click();
  await expect(page).toHaveURL(/\/cases$/);
  await page.goto("/admin/profiles");
  await page.getByRole("button", { name: `Delete ${profileName}` }).click();
  await page
    .getByRole("dialog", { name: `Delete “${profileName}”?` })
    .getByRole("button", { name: "Delete" })
    .click();
  await expect(page.getByRole("status")).toHaveText(`Deleted “${profileName}”.`);
  expect(pageErrors).toEqual([]);
});
