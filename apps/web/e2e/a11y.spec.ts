import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  MISSING_ACCOUNT,
  missingAccount,
  newCase,
  signIn,
  useTheme,
  watchForViolations,
  type Theme,
} from "./support";

// axe-core on every screen, in both themes: no serious or critical
// violations of WCAG 2.2 A/AA. A justified exception would be listed in
// EXCEPTIONS with its reason; there are none.
//
// Not violations, for the record: axe reports color contrast as "needs
// review" wherever text sits on the woven background texture (it cannot
// measure text over a background image); src/theme/contrast.test.ts checks
// every text and background token pair instead. The graph's "?" button is
// named "Keyboard and mouse shortcuts": the "?" is a symbol, not a text
// label, so WCAG 2.5.3 (label in name) does not apply.

test.skip(missingAccount, MISSING_ACCOUNT);

test.use({ viewport: { width: 1440, height: 1000 } });

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

/** Rule IDs ignored on a given screen, each with the reason. None so far. */
const EXCEPTIONS: Readonly<Record<string, readonly { rule: string; reason: string }[]>> = {};

async function check(page: Page, screen: string): Promise<string[]> {
  const ignored = new Set((EXCEPTIONS[screen] ?? []).map((exception) => exception.rule));
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .filter((violation) => !ignored.has(violation.id))
    .map(
      (violation) =>
        `${screen}: ${violation.id} (${violation.impact ?? "?"}) ${violation.help} — ` +
        violation.nodes
          .slice(0, 3)
          .map((node) => node.target.join(" "))
          .join("; "),
    );
}

for (const theme of ["dark", "light"] as const satisfies readonly Theme[]) {
  test(`every screen in the ${theme} theme`, async ({ page }) => {
    test.setTimeout(120_000);
    const problems = await watchForViolations(page);
    await useTheme(page, theme);
    const found: string[] = [];

    // Sign-in, with the still drawing (the canvas is decoration either way).
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.getByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
    found.push(...(await check(page, "sign-in")));

    await signIn(page);
    const caseId = await newCase(page, `Accessibility (${theme})`);

    await page.goto("/cases");
    await expect(page.getByRole("list", { name: "Cases" })).toBeVisible();
    found.push(...(await check(page, "cases list")));

    // The case overview: header, tabs and the members.
    await page.goto(`/cases/${caseId}/members`);
    await expect(page.getByRole("heading", { name: "Members" })).toBeVisible();
    found.push(...(await check(page, "case overview")));

    await page.goto(`/cases/${caseId}/graph`);
    await expect(page.getByTestId("graph-canvas")).toBeVisible();
    found.push(...(await check(page, "graph 2D")));

    await page.goto(`/cases/${caseId}/graph?mode=3d`);
    await expect(page.locator(".graph3d-canvas")).toBeVisible({ timeout: 20_000 });
    found.push(...(await check(page, "graph 3D")));

    await page.goto("/admin/users");
    await expect(page.getByRole("table", { name: "Users" })).toBeVisible();
    found.push(...(await check(page, "admin users")));

    await page.goto("/admin/audit");
    await expect(page.getByRole("table", { name: "Audit events, newest first" })).toBeVisible();
    found.push(...(await check(page, "admin audit")));

    await page.goto("/admin/profiles");
    await expect(page.getByRole("table", { name: "Scan profiles" })).toBeVisible();
    found.push(...(await check(page, "admin profiles")));

    await page.goto("/admin/result-cache");
    await expect(page.getByRole("heading", { name: "Result cache", level: 1 })).toBeVisible();
    found.push(...(await check(page, "admin result cache")));

    await page.goto("/no-such-page");
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
    await expect(page).toHaveTitle("Page not found · Nightloom");
    found.push(...(await check(page, "404")));

    expect(found).toEqual([]);
    expect(problems).toEqual([]);
  });
}
