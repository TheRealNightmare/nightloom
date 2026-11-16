import { expect, test, type Page } from "@playwright/test";

// The account is created by `make e2e` from these variables; never hard-code
// real credentials here.
const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";

test.skip(
  email === "" || password === "",
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).",
);

async function signIn(page: Page, withPassword: string) {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(withPassword);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("sign in, keep the session across a reload, sign out", async ({ page, context }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();

  // A wrong password shows the generic error.
  await signIn(page, `${password}-wrong`);
  await expect(page.getByTestId("login-error")).toHaveText("Invalid email or password.");

  // The right one opens the app.
  await signIn(page, password);
  const userMenu = page.getByRole("button", { name: /Admin/ });
  await expect(userMenu).toBeVisible();
  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByText("Demo data")).toBeVisible();

  // The session cookie is out of reach of page scripts; the CSRF cookie is not.
  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name === "nightloom_session");
  const csrf = cookies.find((c) => c.name === "nightloom_csrf");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Strict");
  expect(csrf?.httpOnly).toBe(false);
  expect(await page.evaluate(() => document.cookie)).not.toContain("nightloom_session");

  // A reload keeps the session.
  await page.reload();
  await expect(userMenu).toBeVisible();
  await expect(page.getByTestId("graph-canvas")).toBeVisible();

  // Sign out returns to the sign-in screen and drops the session.
  await userMenu.click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
  expect((await context.cookies()).some((c) => c.name === "nightloom_session")).toBe(false);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
});
