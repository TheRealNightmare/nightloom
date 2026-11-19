import { expect, test, type Page } from "@playwright/test";

// The admin account is created by `make e2e` from these variables; never
// hard-code real credentials here. Each run creates one new analyst with a
// unique example.org address (users cannot be deleted, only deactivated).
const adminEmail = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const adminPassword = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";

test.skip(
  adminEmail === "" || adminPassword === "",
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).",
);

async function signIn(page: Page, email: string, password: string) {
  await page.goto("/cases");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Signed in (to the app or to the forced password change) before moving on.
  await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeHidden();
}

async function signOut(page: Page) {
  // The account menu in the top bar (the Users table has "Admin" buttons too).
  await page.getByRole("banner").getByRole("button", { name: /Admin/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
}

/** Reads the one-time password from its panel, then closes the panel. */
async function takeTemporaryPassword(page: Page, title: string): Promise<string> {
  const panel = page.getByRole("dialog", { name: title });
  await expect(panel.getByText("It will not be shown again.", { exact: false })).toBeVisible();
  const temporary = (await panel.getByLabel("Temporary password").textContent())?.trim() ?? "";
  expect(temporary.length).toBeGreaterThanOrEqual(20);
  await panel.getByRole("button", { name: "Done" }).click();
  await expect(panel).toBeHidden();
  // Not left anywhere on the page.
  await expect(page.getByText(temporary)).toHaveCount(0);
  return temporary;
}

test("admins manage users with temporary passwords, and verify the audit chain", async ({
  page,
  browser,
}) => {
  // Several Argon2 sign-ins and a chain verification.
  test.setTimeout(120_000);
  const stamp = `${Date.now()}`;
  const analystEmail = `e2e-analyst-${stamp}@example.org`;
  const analystName = `E2E Analyst ${stamp}`;
  const analystPassword = `e2e analyst passphrase ${stamp}`;

  // --- The admin creates an analyst -------------------------------------------
  await signIn(page, adminEmail, adminPassword);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Users" }).click();
  await expect(page).toHaveURL(/\/admin\/users$/);
  await expect(page.getByRole("heading", { name: "Users", level: 1 })).toBeVisible();

  await page.getByRole("button", { name: "New user" }).click();
  const create = page.getByRole("dialog", { name: "New user" });
  await create.getByLabel("Email").fill(analystEmail);
  await create.getByLabel("Display name").fill(analystName);
  await create.getByLabel("Role").selectOption("analyst");
  await create.getByRole("button", { name: "Create user" }).click();
  const temporary = await takeTemporaryPassword(page, "User created");

  const row = page.getByRole("row").filter({ hasText: analystEmail });
  await expect(row.getByText("temporary password")).toBeVisible();
  await signOut(page);

  // --- The analyst signs in and must choose a new password ----------------------
  await signIn(page, analystEmail, temporary);
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  // Nothing else is reachable meanwhile, not even by URL.
  await page.goto("/cases/new");
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await page.getByLabel("Temporary password", { exact: true }).fill(temporary);
  await page.getByLabel("New password", { exact: true }).fill(analystPassword);
  await page.getByLabel("Confirm new password", { exact: true }).fill(analystPassword);
  await page.getByRole("button", { name: "Set new password" }).click();
  // On to the page that was asked for.
  await expect(page.getByRole("heading", { name: "New case", level: 1 })).toBeVisible();
  await expect(page).toHaveURL(/\/cases\/new$/);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Cases" }).click();
  await expect(page).toHaveURL(/\/cases$/);
  await expect(page.getByRole("heading", { name: "Cases", level: 1 })).toBeVisible();
  // Analysts see no admin pages.
  await expect(page.getByRole("link", { name: "Users" })).toHaveCount(0);
  await page.goto("/admin/users");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await page.goto("/cases");

  // --- The admin, in another browser, resets the analyst's password -----------
  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  await signIn(admin, adminEmail, adminPassword);
  await admin.goto("/admin/users");
  const analystRow = admin.getByRole("row").filter({ hasText: analystEmail });
  await expect(analystRow).toBeVisible();
  // The forced change cleared the flag.
  await expect(analystRow.getByText("temporary password")).toHaveCount(0);
  await analystRow.getByRole("button", { name: `Reset password of ${analystName}` }).click();
  await admin
    .getByRole("dialog", { name: `Reset the password of ${analystName}?` })
    .getByRole("button", { name: "Reset password" })
    .click();
  const second = await takeTemporaryPassword(admin, "Password reset");
  expect(second).not.toBe(temporary);

  // The analyst's session is gone.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
  await page.getByLabel("Email").fill(analystEmail);
  await page.getByLabel("Password", { exact: true }).fill(analystPassword);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("login-error")).toHaveText("Invalid email or password.");

  // --- The admin reads the audit log and verifies the chain --------------------
  await admin
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Audit log" })
    .click();
  await expect(admin.getByRole("heading", { name: "Audit log", level: 1 })).toBeVisible();
  const events = admin.getByRole("table", { name: "Audit events, newest first" });
  await expect(events.getByText(`reset the password of ${analystName}`)).toBeVisible();
  await admin.getByLabel("Action").selectOption("user.create");
  await expect(events.getByText(`created ${analystName} as analyst`)).toBeVisible();
  await events
    .getByRole("button", { name: /Details of event/ })
    .first()
    .click();
  const drawer = admin.getByRole("dialog", { name: /Event #/ });
  await expect(drawer.getByText('"credential": "generated"', { exact: false })).toBeVisible();
  await expect(drawer.getByText(temporary)).toHaveCount(0);
  await drawer.getByRole("button", { name: "Close" }).click();

  await admin.getByRole("button", { name: "Verify chain" }).click();
  await expect(admin.getByText(/^Chain intact: [\d,]+ events? checked\.$/)).toBeVisible({
    timeout: 60_000,
  });

  await adminContext.close();
});
