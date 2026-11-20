import { expect, test, type Page, type Request } from "@playwright/test";

import { MISSING_ACCOUNT, missingAccount, signIn, watchForViolations } from "./support";

// The sign-in page's loom (U6): the 3D scene must never get in the way of
// signing in. It is fetched only after the page has loaded and the browser
// is idle, from the 3D view's three-vendor chunk; the still drawing stays
// with reduced motion, Save-Data or no WebGL.

test.skip(missingAccount, MISSING_ACCOUNT);

/** Every script the page requests. */
function recordScripts(page: Page): string[] {
  const scripts: string[] = [];
  page.on("request", (request: Request) => {
    if (request.resourceType() === "script") scripts.push(request.url());
  });
  return scripts;
}

const isThree = (url: string) => /three-vendor|loomScene/.test(url);

test.describe("with motion and WebGL", () => {
  test.use({ colorScheme: "dark", viewport: { width: 1440, height: 900 } });

  test("loads the scene after the form is usable, and never in its way", async ({ page }) => {
    const problems = await watchForViolations(page);
    const scripts = recordScripts(page);
    const response = await page.goto("/");
    // The page itself names no three.js (no script or modulepreload).
    expect(isThree((await response?.text()) ?? "")).toBe(false);
    await expect(page.getByLabel("Email")).toBeFocused();

    const loom = page.locator(".loom");
    await expect(loom).toHaveAttribute("data-loom", "scene", { timeout: 20_000 });
    await expect(page.getByTestId("loom-canvas")).toBeVisible();

    // three.js came from the shared chunk, and only after the page had loaded.
    expect(scripts.some((url) => url.includes("three-vendor"))).toBe(true);
    const timing = await page.evaluate(() => {
      const navigation = performance.getEntriesByType("navigation")[0] as
        PerformanceNavigationTiming | undefined;
      const three = performance
        .getEntriesByType("resource")
        .filter((entry) => /three-vendor|loomScene/.test(entry.name))
        .map((entry) => entry.startTime);
      return { loaded: navigation?.loadEventEnd ?? Infinity, three };
    });
    expect(timing.three.length).toBeGreaterThan(0);
    for (const start of timing.three) expect(start).toBeGreaterThanOrEqual(timing.loaded);

    // Decoration only: hidden from assistive technology, nothing to focus or click.
    await expect(loom).toHaveAttribute("aria-hidden", "true");
    expect(await loom.evaluate((node) => getComputedStyle(node).pointerEvents)).toBe("none");
    expect(await loom.locator("a, button, input, [tabindex]").count()).toBe(0);
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Password", { exact: true })).toBeFocused();

    // The theme changes under a running scene without trouble.
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await expect(loom).toHaveAttribute("data-loom", "scene");

    // Signing in works while it runs, and the scene goes away with the page.
    await signIn(page);
    await expect(page.locator(".loom")).toHaveCount(0);
    expect(problems).toEqual([]);
  });

  test("keeps the loom behind the form on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const problems = await watchForViolations(page);
    await page.goto("/");
    const art = page.locator(".login__art");
    await expect(art).toBeAttached();
    expect(Number(await art.evaluate((node) => getComputedStyle(node).opacity))).toBeLessThan(0.3);
    // The form is on top and usable: a click on the field reaches it.
    await page.getByLabel("Email").click();
    await expect(page.getByLabel("Email")).toBeFocused();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeInViewport();
    expect(problems).toEqual([]);
  });
});

test.describe("the still drawing", () => {
  const stays = async (page: Page, reason: string) => {
    const scripts = recordScripts(page);
    await page.goto("/");
    await expect(page.getByLabel("Email")).toBeFocused();
    const loom = page.locator(".loom");
    await expect(loom).toHaveAttribute("data-loom", "still");
    await expect(loom).toHaveAttribute("data-loom-reason", reason);
    await expect(page.getByTestId("loom-static")).toBeVisible();
    // Give an idle callback every chance to (wrongly) fetch the scene.
    await page.waitForTimeout(1500);
    await expect(page.getByTestId("loom-canvas")).toHaveCount(0);
    expect(scripts.filter(isThree)).toEqual([]);
  };

  test.describe("with reduced motion", () => {
    test.use({ reducedMotion: "reduce" });
    test("is shown and nothing is fetched", async ({ page }) => {
      await stays(page, "reduced-motion");
    });
  });

  test("is shown with Save-Data", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "connection", {
        configurable: true,
        value: { saveData: true },
      });
    });
    await stays(page, "save-data");
  });

  test("is shown without WebGL", async ({ page }) => {
    await page.addInitScript(() => {
      // No WebGL: every WebGL context request fails, as on a machine without it.
      const prototype = HTMLCanvasElement.prototype as unknown as {
        getContext: (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) => unknown;
      };
      const original = prototype.getContext;
      prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
        if (kind.startsWith("webgl")) return null;
        return original.call(this, kind, ...rest);
      };
    });
    await stays(page, "no-webgl");
  });

  test("comes back when the scene cannot be fetched", async ({ page }) => {
    await page.route(/loomScene-.*\.js$/, (route) => route.abort());
    await page.goto("/");
    await expect(page.locator(".loom")).toHaveAttribute("data-loom-reason", "failed", {
      timeout: 20_000,
    });
    await expect(page.getByTestId("loom-static")).toBeVisible();
    await expect(page.getByLabel("Email")).toBeEditable();
  });
});
