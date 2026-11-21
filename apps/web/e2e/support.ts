/**
 * Helpers shared by the sign-in loom and accessibility specs. The account is
 * created by `make e2e` from these variables; never hard-code real
 * credentials here.
 */
import { expect, type Page } from "@playwright/test";

export const email = process.env.NIGHTLOOM_E2E_EMAIL ?? "";
export const password = process.env.NIGHTLOOM_E2E_PASSWORD ?? "";
export const missingAccount = email === "" || password === "";
export const MISSING_ACCOUNT =
  "Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (make e2e does this check too).";

export type Theme = "dark" | "light";

/** Starts every page of the context in `theme` (the toggle's stored choice). */
export async function useTheme(page: Page, theme: Theme): Promise<void> {
  await page.addInitScript((value) => {
    try {
      window.localStorage.setItem("nightloom.theme", value);
    } catch {
      // Storage blocked: the system preference applies.
    }
  }, theme);
}

/** Records CSP violations (and page errors) from the first script on. */
export async function watchForViolations(page: Page): Promise<string[]> {
  const problems: string[] = [];
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (/Content.Security.Policy|Refused to/i.test(message.text())) {
      problems.push(`console: ${message.text()}`);
    }
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      console.error(`Refused to: CSP ${event.violatedDirective} ${event.blockedURI}`);
    });
  });
  return problems;
}

export async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Cases", level: 1 })).toBeVisible();
}

export async function api(
  page: Page,
  method: string,
  apiPath: string,
  data?: unknown,
): Promise<unknown> {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === "nightloom_csrf")?.value ?? "";
  const response = await page.request.fetch(`/api/v1${apiPath}`, {
    method,
    headers: { "X-CSRF-Token": csrf },
    ...(data === undefined ? {} : { data }),
  });
  expect(response.ok(), `${method} ${apiPath}: ${await response.text()}`).toBe(true);
  return response.status() === 204 ? null : ((await response.json()) as unknown);
}

/** A case with example.org, www.example.org and 192.0.2.10 (fictional data). */
export async function newCase(page: Page, title: string): Promise<string> {
  const created = (await api(page, "POST", "/cases", {
    title: `${title} ${Date.now().toString(36)}`,
    purpose: "Check the interface's accessibility against the real API.",
    legal_basis: "owned_assets",
    ethics_acknowledged: true,
  })) as { id: string };
  const id = created.id;
  const entity = async (type: string, value: string) =>
    ((await api(page, "POST", `/cases/${id}/entities`, { type, value })) as { key: string }).key;
  const domain = await entity("domain", "example.org");
  const www = await entity("domain", "www.example.org");
  const ip = await entity("ip_address", "192.0.2.10");
  await api(page, "POST", `/cases/${id}/relationships`, {
    type: "HAS_SUBDOMAIN",
    source_key: domain,
    target_key: www,
  });
  await api(page, "POST", `/cases/${id}/relationships`, {
    type: "RESOLVES_TO",
    source_key: www,
    target_key: ip,
  });
  return id;
}
