import { defineConfig, devices } from "@playwright/test";

// End-to-end tests against the running Compose stack (`make up`, then
// `make e2e`). Override the address when WEB_HOST_PORT is changed in .env.
const baseURL = process.env.NIGHTLOOM_E2E_BASE_URL ?? "http://127.0.0.1:8080";
const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: ci,
  retries: ci ? 1 : 0,
  reporter: ci ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
