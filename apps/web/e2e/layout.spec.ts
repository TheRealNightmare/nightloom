import { expect, test, type Locator, type Page } from "@playwright/test";

import { MISSING_ACCOUNT, missingAccount, newCase, signIn, useTheme, type Theme } from "./support";

// Visual layout checks with bounding boxes (no pixel snapshots): the case
// title is on screen, the graph's overlays (legend, zoom controls, minimap)
// and the 3D depth ruler never overlap, and the sign-in page is one column.
// The account is created by `make e2e` from NIGHTLOOM_E2E_EMAIL /
// NIGHTLOOM_E2E_PASSWORD; never hard-code real credentials here.

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

const SIZES = [
  { width: 1600, height: 1000 },
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
] as const;
/** The narrowest stage the overlays must fit side by side. */
const NARROW = { width: 360, height: 740 } as const;
const THEMES: readonly Theme[] = ["dark", "light"];

function label(box: Box): string {
  return `(${box.x.toFixed(1)}, ${box.y.toFixed(1)}) ${box.width.toFixed(1)}×${box.height.toFixed(1)}`;
}

function intersects(a: Box, b: Box): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

function inside(inner: Box, outer: Box, slack = 0.5): boolean {
  return (
    inner.x >= outer.x - slack &&
    inner.y >= outer.y - slack &&
    inner.x + inner.width <= outer.x + outer.width + slack &&
    inner.y + inner.height <= outer.y + outer.height + slack
  );
}

async function boxOf(locator: Locator): Promise<Box> {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  if (!box) throw new Error("No bounding box (not rendered)");
  return box;
}

/** Fails if any two of the named boxes intersect. */
function expectApart(boxes: Record<string, Box>): void {
  const names = Object.keys(boxes);
  for (let i = 0; i < names.length; i += 1) {
    for (let j = i + 1; j < names.length; j += 1) {
      const a = boxes[names[i] ?? ""];
      const b = boxes[names[j] ?? ""];
      if (!a || !b) continue;
      expect(
        intersects(a, b),
        `${names[i] ?? ""} ${label(a)} overlaps ${names[j] ?? ""} ${label(b)}`,
      ).toBe(false);
    }
  }
}

// --- sign-in --------------------------------------------------------------------------------

test.describe("sign-in layout", () => {
  for (const theme of THEMES) {
    for (const size of SIZES) {
      test(`one column at ${String(size.width)}×${String(size.height)} (${theme})`, async ({
        page,
      }) => {
        await page.setViewportSize(size);
        await useTheme(page, theme);
        await page.goto("/");
        const panel = page.locator(".login__panel");
        const heading = page.getByRole("heading", { name: /Sign in/, level: 1 });
        const email = page.getByLabel("Email");
        const subtitle = page.locator(".login__subtitle");
        const submit = page.getByRole("button", { name: "Sign in" });
        const toggle = page.locator(".login__theme button");

        const headingBox = await boxOf(heading);
        const emailBox = await boxOf(email);
        const subtitleBox = await boxOf(subtitle);
        const submitBox = await boxOf(submit);
        const panelBox = await boxOf(panel);
        const toggleBox = await boxOf(toggle);

        // One left edge, one width (at most ~400 px).
        expect(Math.abs(headingBox.x - emailBox.x)).toBeLessThanOrEqual(1);
        expect(Math.abs(subtitleBox.x - emailBox.x)).toBeLessThanOrEqual(1);
        expect(Math.abs(submitBox.x - emailBox.x)).toBeLessThanOrEqual(1);
        expect(Math.abs(submitBox.width - emailBox.width)).toBeLessThanOrEqual(1);
        expect(emailBox.width).toBeLessThanOrEqual(401);
        // The column is centred in the panel, horizontally and vertically.
        const left = emailBox.x - panelBox.x;
        const right = panelBox.x + panelBox.width - (emailBox.x + emailBox.width);
        expect(Math.abs(left - right)).toBeLessThanOrEqual(2);
        const columnBox = await boxOf(page.locator(".login__column"));
        if (columnBox.height < size.height - 120) {
          const above = columnBox.y - panelBox.y;
          const below = panelBox.y + panelBox.height - (columnBox.y + columnBox.height);
          // The panel's top padding is larger (room for the theme toggle).
          expect(Math.abs(above - below)).toBeLessThanOrEqual(30);
        }
        // The theme toggle sits in the panel's top-right corner, clear of the column.
        expect(panelBox.x + panelBox.width - (toggleBox.x + toggleBox.width)).toBeLessThanOrEqual(
          24,
        );
        expect(toggleBox.y - panelBox.y).toBeLessThanOrEqual(24);
        expect(intersects(toggleBox, columnBox)).toBe(false);
        // Everything is on screen without scrolling.
        expect(inside(submitBox, { x: 0, y: 0, ...size })).toBe(true);
      });
    }
  }
});

// --- the case graph -------------------------------------------------------------------------

test.describe("case graph layout", () => {
  test.skip(missingAccount, MISSING_ACCOUNT);

  let caseId = "";

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await signIn(page);
    caseId = await newCase(page, "Layout check");
    await page.close();
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      try {
        window.localStorage.setItem("nightloom.graph.minimap", "on");
      } catch {
        // Storage blocked: the minimap stays off, the other checks still run.
      }
    });
    await useTheme(page, "dark");
    await signIn(page);
  });

  async function openGraph(page: Page, mode: "2d" | "3d") {
    await page.goto(`/cases/${caseId}/graph?mode=${mode}`);
    const canvas = page.getByTestId("graph-canvas");
    await expect(canvas).toBeVisible();
    if (mode === "3d") {
      await expect(page.locator(".graph3d-canvas")).toBeVisible({ timeout: 30_000 });
      await expect
        .poll(async () => Number((await canvas.getAttribute("data-frame")) ?? "0"))
        .toBeGreaterThan(2);
      await expect(page.locator(".graph3d-ruler__mark:not([hidden])").first()).toBeVisible();
    }
    await expect(page.locator(".graph-minimap")).toBeVisible();
    // Let the framing animation and the minimap settle.
    await page.waitForTimeout(800);
  }

  async function checkOverlays(page: Page, mode: "2d" | "3d") {
    const stage = await boxOf(page.locator(".stage__canvas"));
    const legend = await boxOf(page.locator(".legend"));
    const controls = await boxOf(page.getByRole("group", { name: "Zoom and rotation" }));
    const minimap = await boxOf(page.locator(".graph-minimap"));
    expectApart({ legend, controls, minimap });
    for (const [name, box] of Object.entries({ legend, controls, minimap })) {
      expect(inside(box, stage), `${name} ${label(box)} is outside the stage ${label(stage)}`).toBe(
        true,
      );
    }
    if (mode === "3d") {
      // The depth ruler: every mark on screen, inside the stage, under no overlay.
      const marks = page.locator(".graph3d-ruler__mark:not([hidden])");
      const count = await marks.count();
      expect(count).toBeGreaterThan(0);
      for (let n = 0; n < count; n += 1) {
        const mark = await boxOf(marks.nth(n));
        const name = `ruler mark "${(await marks.nth(n).textContent()) ?? ""}" ${label(mark)}`;
        expect(inside(mark, stage), `${name} is outside the stage`).toBe(true);
        expect(intersects(mark, legend), `${name} is under the legend`).toBe(false);
        expect(intersects(mark, controls), `${name} is under the controls`).toBe(false);
      }
    }
  }

  for (const mode of ["2d", "3d"] as const) {
    for (const size of [...SIZES, NARROW]) {
      test(`${mode.toUpperCase()} at ${String(size.width)}×${String(size.height)}`, async ({
        page,
      }) => {
        await page.setViewportSize(size);
        await openGraph(page, mode);

        // The case title is in the viewport, below the sticky top bar, unscrolled.
        expect(await page.evaluate(() => window.scrollY)).toBe(0);
        const title = await boxOf(page.getByRole("heading", { level: 1 }));
        const topbar = await boxOf(page.locator(".topbar"));
        expect(title.y).toBeGreaterThanOrEqual(topbar.y + topbar.height);
        expect(inside(title, { x: 0, y: 0, ...size })).toBe(true);

        // Nothing is wider than the window.
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
          size.width,
        );

        // At 1600×1000 the graph fills the rest of the window (the page does not
        // scroll); on a lower one it keeps its 520 px minimum and the page scrolls.
        if (size.height >= 1000) {
          const workspace = await boxOf(page.locator(".workspace--case"));
          expect(workspace.height).toBeGreaterThanOrEqual(519);
          expect(workspace.y + workspace.height).toBeLessThanOrEqual(size.height);
        }

        await checkOverlays(page, mode);

        // A narrow stage folds the legend into a toggle; open, it still fits.
        const toggle = page.locator(".legend__toggle");
        if (await toggle.isVisible()) {
          await expect(page.locator(".legend__body")).toBeHidden();
          await toggle.click();
          await expect(page.locator(".legend__body")).toBeVisible();
          await checkOverlays(page, mode);
        }
      });
    }
  }
});
