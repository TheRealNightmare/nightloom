import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { clearCookies, FakeApi, json } from "../test/fakeApi";
import { ForbiddenError, UnexpectedResponseError } from "./client";
import {
  clearCaseResultCache,
  clearResultCache,
  formatBytes,
  getResultCache,
  isResultCacheInfo,
  type ResultCacheInfo,
} from "./resultCache";

let api: FakeApi;

const CACHE_INFO: ResultCacheInfo = {
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
      shuttle: "retired",
      cache_ttl_seconds: null,
      entries: 1,
      size_bytes: 1024,
      hits: 0,
      jobs: 0,
      jobs_from_cache: 0,
    },
  ],
};

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

describe("result cache calls", () => {
  it("reads the statistics and refuses a malformed answer", async () => {
    api.on("GET", "/admin/result-cache", () => json(CACHE_INFO));
    expect(await getResultCache()).toEqual(CACHE_INFO);
    api.on("GET", "/admin/result-cache", () => json({ ...CACHE_INFO, scope: "everyone" }));
    await expect(getResultCache()).rejects.toBeInstanceOf(UnexpectedResponseError);
  });

  it("checks every field", () => {
    expect(isResultCacheInfo(CACHE_INFO)).toBe(true);
    expect(isResultCacheInfo({ ...CACHE_INFO, entries: -1 })).toBe(false);
    expect(isResultCacheInfo({ ...CACHE_INFO, shuttles: [{ shuttle: "x" }] })).toBe(false);
    expect(isResultCacheInfo([])).toBe(false);
  });

  it("clears everything, one Shuttle's, or a case's, with the CSRF token", async () => {
    api.on("DELETE", "/admin/result-cache", () => json({ deleted: 3 }));
    api.on("DELETE", "/admin/result-cache?shuttle=crtsh", () => json({ deleted: 2 }));
    api.on("DELETE", "/cases/c%201/result-cache", () => json({ deleted: 1 }));
    expect(await clearResultCache()).toBe(3);
    expect(await clearResultCache("crtsh")).toBe(2);
    expect(await clearCaseResultCache("c 1")).toBe(1);
    const call = api.callsTo("DELETE", "/admin/result-cache")[0];
    expect(new Headers(call?.init.headers).get("X-CSRF-Token")).toBe("csrf-token");
  });

  it("refuses a malformed count and passes on refusals", async () => {
    api.on("DELETE", "/admin/result-cache", () => json({ deleted: "all" }));
    await expect(clearResultCache()).rejects.toBeInstanceOf(UnexpectedResponseError);
    api.on("DELETE", "/cases/c1/result-cache", () => json({ detail: "Forbidden" }, 403));
    await expect(clearCaseResultCache("c1")).rejects.toBeInstanceOf(ForbiddenError);
  });

  it.each([
    [0, "0 B"],
    [980, "980 B"],
    [1024, "1.0 KB"],
    [12_595, "12 KB"],
    [5 * 1024 * 1024, "5.0 MB"],
    [512 * 1024 * 1024, "512 MB"],
    [3 * 1024 ** 3, "3.0 GB"],
    [2 * 1024 ** 5, "2048 TB"],
  ])("formats %i bytes as %s", (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });
});
