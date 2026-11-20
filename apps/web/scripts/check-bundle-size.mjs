// Fails when the built bundle (bundle-report.json, written by `vite build`)
// is over the budgets in bundle-budget.json. Run after `pnpm build`.
import { readFileSync } from "node:fs";

const read = (name) => JSON.parse(readFileSync(new URL(`../${name}`, import.meta.url), "utf8"));
const report = read("bundle-report.json");
const budget = read("bundle-budget.json");
const kb = (bytes) => `${(bytes / 1024).toFixed(1)} KiB`;

let failed = false;
for (const part of ["signin", "loom", "graph2d", "graph3d"]) {
  const size = report[part].gzip;
  const limit = budget[part];
  const ok = size <= limit;
  failed ||= !ok;
  console.log(`${ok ? "ok  " : "FAIL"} ${part}: ${kb(size)} gzip (budget ${kb(limit)})`);
  for (const [file, bytes] of Object.entries(report[part].files)) {
    console.log(`       ${file}: ${kb(bytes)}`);
  }
}
// The sign-in page's initial JavaScript never includes three.js: the loom
// scene is fetched when the browser is idle (src/auth/loom/LoomBackdrop.tsx).
const signinThree = report.signin.threeChunks ?? [];
if (signinThree.length > 0) {
  failed = true;
  console.log(`FAIL signin: three.js is in the initial JavaScript (${signinThree.join(", ")})`);
} else {
  console.log("ok   signin: no three.js in the initial JavaScript");
}
// The loom reuses the 3D view's three-vendor chunk instead of bundling its own copy.
const loomThree = report.loom.threeChunks ?? [];
const sharesVendor =
  loomThree.length > 0 && loomThree.every((name) => name.includes("three-vendor"));
if (!sharesVendor) {
  failed = true;
  console.log(
    `FAIL loom: expected three.js only in the three-vendor chunk, got ${loomThree.join(", ") || "none"}`,
  );
} else {
  console.log(`ok   loom: three.js from ${loomThree.join(", ")}`);
}
if (failed) {
  console.error("The bundle is over budget (apps/web/bundle-budget.json).");
  process.exit(1);
}
