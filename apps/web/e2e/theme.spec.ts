import { expect, test } from "@playwright/test";

// The light/dark theme: toggled on the sign-in page and in the top bar,
// remembered across a reload, and applied before the first paint (by
// public/theme-init.js, which the CSP must allow as a same-origin script).

const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";

test.skip(
  email === "" || password === "",
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).",
);

test.use({ colorScheme: "dark" });

test("toggle the theme, and keep it across a reload", async ({ page }) => {
  const cspErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && /Content Security Policy/i.test(message.text())) {
      cspErrors.push(message.text());
    }
  });
  const html = page.locator("html");
  const pageBackground = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);

  // No stored choice: the system preference (dark here).
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
  await expect(html).toHaveAttribute("data-theme", "dark");
  const darkBackground = await pageBackground();

  // The toggle switches to light and says what it will do next.
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(html).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();
  await expect.poll(pageBackground).not.toBe(darkBackground);
  expect(await page.evaluate(() => localStorage.getItem("nightloom.theme"))).toBe("light");

  // A reload starts in light, even though the system prefers dark.
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();

  // Signed in, the top bar has the same toggle.
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Cases", level: 1 })).toBeVisible();
  await expect(html).toHaveAttribute("data-theme", "light");
  await page.getByRole("banner").getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Cases", level: 1 })).toBeVisible();
  await expect(html).toHaveAttribute("data-theme", "dark");

  // The self-hosted fonts load under the CSP (a blocked font rejects this load).
  for (const font of [
    '500 32px "Fraunces Variable"',
    '400 15px "Source Sans 3 Variable"',
    '400 13px "JetBrains Mono Variable"',
  ]) {
    const loaded = await page.evaluate(
      async (spec) => (await document.fonts.load(spec, "Cases")).length,
      font,
    );
    expect(loaded, font).toBeGreaterThan(0);
  }
  expect(cspErrors).toEqual([]);
});
