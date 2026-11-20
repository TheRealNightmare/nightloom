// Fails when the built bundle (bundle-report.json, written by `vite build`)
// is over the budgets in bundle-budget.json. Run after `pnpm build`.
import { readFileSync } from "node:fs";

const read = (name) => JSON.parse(readFileSync(new URL(`../${name}`, import.meta.url), "utf8"));
const report = read("bundle-report.json");
const budget = read("bundle-budget.json");
const kb = (bytes) => `${(bytes / 1024).toFixed(1)} KiB`;

let failed = false;
for (const part of ["graph2d", "graph3d"]) {
  const size = report[part].gzip;
  const limit = budget[part];
  const ok = size <= limit;
  failed ||= !ok;
  console.log(`${ok ? "ok  " : "FAIL"} ${part}: ${kb(size)} gzip (budget ${kb(limit)})`);
  for (const [file, bytes] of Object.entries(report[part].files)) {
    console.log(`       ${file}: ${kb(bytes)}`);
  }
}
if (failed) {
  console.error("The bundle is over budget (apps/web/bundle-budget.json).");
  process.exit(1);
}
