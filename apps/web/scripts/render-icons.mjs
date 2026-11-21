// Draws the PNG app icons in public/icons/ from public/favicon.svg (the dark
// theme's colors) with Playwright's Chromium. Run after changing the icon:
//   node scripts/render-icons.mjs
// The maskable icon fills the whole square and keeps the mark inside the
// central 80% "safe zone", as the manifest's "maskable" purpose expects.
import { readFileSync } from "node:fs";

import { chromium } from "@playwright/test";

const svg = readFileSync(new URL("../public/favicon.svg", import.meta.url), "utf8");
const out = (name) => new URL(`../public/icons/${name}`, import.meta.url).pathname;

const icons = [
  { name: "favicon-32.png", size: 32, maskable: false },
  { name: "apple-touch-icon.png", size: 180, maskable: true },
  { name: "icon-192.png", size: 192, maskable: false },
  { name: "icon-512.png", size: 512, maskable: false },
  { name: "icon-maskable-512.png", size: 512, maskable: true },
];

const browser = await chromium.launch();
try {
  for (const { name, size, maskable } of icons) {
    const page = await browser.newPage({
      viewport: { width: size, height: size },
      colorScheme: "dark",
    });
    // Maskable: the background square fills the canvas, the mark is scaled
    // into the safe zone (the rounded corners would be cut off anyway).
    const mark = maskable
      ? svg
          .replace('rx="7"', 'rx="0"')
          .replace("<path", '<g transform="translate(3.2 3.2) scale(0.8)"><path')
          .replace("</svg>", "</g></svg>")
      : svg;
    await page.setContent(
      `<!doctype html><html><body style="margin:0;background:transparent">` +
        `<div style="width:${size}px;height:${size}px">${mark.replace("<svg ", `<svg width="${size}" height="${size}" `)}</div>` +
        `</body></html>`,
    );
    await page.locator("div").screenshot({ path: out(name), omitBackground: true });
    await page.close();
    console.log(`public/icons/${name}`);
  }
} finally {
  await browser.close();
}
