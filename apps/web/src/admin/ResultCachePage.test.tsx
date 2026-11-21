import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ResultCacheInfo } from "../api/resultCache";
import { ADMIN_USER } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, READY_BODY } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { SCOPE_TEXT } from "./resultCacheText";

let api: FakeApi;
let info: ResultCacheInfo;

const INFO: ResultCacheInfo = {
  scope: "case",
  max_bytes: 512 * 1024 * 1024,
  max_entry_bytes: 5 * 1024 * 1024,
  entries: 3,
  size_bytes: 4096,
  shuttles: [
    {
      shuttle: "crtsh",
      cache_ttl_seconds: 86_400,
      entries: 2,
      size_bytes: 3072,
      hits: 4,
      jobs: 6,
      jobs_from_cache: 4,
    },
    {
      shuttle: "dns",
      cache_ttl_seconds: 3600,
      entries: 0,
      size_bytes: 0,
      hits: 0,
      jobs: 0,
      jobs_from_cache: 0,
    },
    {
      shuttle: "retired",
      cache_ttl_seconds: null,
      entries: 1,
      size_bytes: 1024,
      hits: 1,
      jobs: 1,
      jobs_from_cache: 0,
    },
  ],
};

beforeEach(() => {
  info = INFO;
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(ADMIN_USER))
    .on("GET", "/admin/result-cache", () => json(info))
    .install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

async function openPage() {
  renderApp("/admin/result-cache");
  return screen.findByRole("table", { name: "Cached results by Shuttle" });
}

const rows = (table: HTMLElement) => within(table).getAllByRole("row").slice(1);

describe("result cache page", () => {
  it("shows the scope, the totals and each Shuttle", async () => {
    const table = await openPage();
    expect(screen.getByRole("heading", { name: "Result cache", level: 1 })).toBeVisible();
    expect(document.title).toMatch(/^Result cache/);
    expect(screen.getByText(SCOPE_TEXT.case)).toBeVisible();
    expect(screen.getByTestId("result-cache-summary")).toHaveTextContent(
      "3 entries · 4.0 KB of 512 MB · entries over 5.0 MB are not kept",
    );
    expect(rows(table).map((row) => row.textContent)).toEqual([
      "crtsh24 hours23.0 KB4 times4 of 6 jobsClear",
      "dns1 hour00 B0 timesNo jobs",
      "retiredNot installed11.0 KB1 time0 of 1 jobClear",
    ]);
  });

  it("clears one Shuttle's results after confirming, then reloads", async () => {
    api.on("DELETE", "/admin/result-cache?shuttle=crtsh", () => {
      info = { ...INFO, entries: 1, shuttles: INFO.shuttles.slice(1) };
      return json({ deleted: 2 });
    });
    await openPage();
    await userEvent.click(screen.getByRole("button", { name: "Clear cached results of crtsh" }));
    const dialog = screen.getByRole("dialog", { name: "Clear the cached results of crtsh?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Clear" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Cleared 2 cached results of crtsh.",
    );
    expect(api.callsTo("DELETE", "/admin/result-cache?shuttle=crtsh")).toHaveLength(1);
    expect(await screen.findByTestId("result-cache-summary")).toHaveTextContent(/^1 entry/);
    expect(api.callsTo("GET", "/admin/result-cache")).toHaveLength(2);
  });

  it("clears everything, and can be cancelled", async () => {
    api.on("DELETE", "/admin/result-cache", () => {
      info = { ...INFO, entries: 0, size_bytes: 0, shuttles: [] };
      return json({ deleted: 3 });
    });
    await openPage();
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.callsTo("DELETE", "/admin/result-cache")).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    const dialog = screen.getByRole("dialog", { name: "Clear the whole result cache?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Clear" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Cleared 3 cached results.");
    expect(await screen.findByRole("heading", { name: "Nothing is cached yet" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Clear all" })).toBeNull();
  });

  it("shows a refused clear", async () => {
    api.on("DELETE", "/admin/result-cache", () => json({ detail: "Not allowed." }, 403));
    await openPage();
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Clear" }),
    );
    expect(await screen.findByRole("alert")).not.toBeEmptyDOMElement();
  });

  it("explains instance-wide and disabled caches", async () => {
    info = { ...INFO, scope: "off", entries: 0, size_bytes: 0, shuttles: [] };
    renderApp("/admin/result-cache");
    expect(await screen.findByText(SCOPE_TEXT.off)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Nothing is cached yet" })).toBeVisible();
    expect(SCOPE_TEXT.instance).toMatch(/shared by every case/);
  });

  it("offers to try again when the statistics cannot load", async () => {
    let fail = true;
    api.on("GET", "/admin/result-cache", () =>
      fail ? json({ detail: "Down." }, 503) : json(INFO),
    );
    renderApp("/admin/result-cache");
    const retry = await screen.findByRole("button", { name: "Try again" });
    fail = false;
    await userEvent.click(retry);
    expect(await screen.findByRole("table", { name: "Cached results by Shuttle" })).toBeVisible();
  });
});
