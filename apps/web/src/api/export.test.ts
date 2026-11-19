import { afterEach, describe, expect, it, vi } from "vitest";

import { FakeApi, json } from "../test/fakeApi";
import { ContentTooLargeError, RateLimitedError, UnexpectedResponseError } from "./client";
import { checkExport, downloader, exportUrl } from "./export";

const CASE = "5f0c8a52-6a0e-4c1e-9d6c-0b8f6e9c2a11";
const COUNTS = {
  entities: 3,
  person_entities: 1,
  relationships: 2,
  max_entities: 100000,
  include_person_data: false,
  exports_remaining: 10,
};

afterEach(() => {
  document.body.innerHTML = "";
});

describe("export URLs", () => {
  it("asks for redacted data unless person data is included, with the trimmed reason", () => {
    expect(exportUrl(CASE, { format: "json", includePersonData: false, reason: "ignored" })).toBe(
      `/api/v1/cases/${CASE}/export?format=json`,
    );
    expect(
      exportUrl(CASE, { format: "csv", includePersonData: true, reason: "  a & b = c  " }),
    ).toBe(`/api/v1/cases/${CASE}/export?format=csv&include_person_data=true&reason=a+%26+b+%3D+c`);
  });
});

describe("checkExport", () => {
  it("returns the counts", async () => {
    const api = new FakeApi().on("GET", `/cases/${CASE}/export/check?format=json`, () =>
      json(COUNTS),
    );
    await expect(
      checkExport(CASE, { format: "json", includePersonData: false }, { fetchImpl: api.fetch }),
    ).resolves.toEqual(COUNTS);
  });

  it("turns 413 and 429 into typed errors with the server's detail", async () => {
    const api = new FakeApi()
      .on("GET", `/cases/${CASE}/export/check?format=json`, () =>
        json({ detail: "Too large." }, 413),
      )
      .on("GET", `/cases/${CASE}/export/check?format=csv`, () =>
        json({ detail: "Slow down." }, 429, { "Retry-After": "30" }),
      )
      .on("GET", `/cases/${CASE}/export/check?format=graphml`, () => json({ entities: "3" }));
    const call = (format: "json" | "csv" | "graphml") =>
      checkExport(CASE, { format, includePersonData: false }, { fetchImpl: api.fetch });

    const tooLarge = await call("json").catch((error: unknown) => error);
    expect(tooLarge).toBeInstanceOf(ContentTooLargeError);
    expect((tooLarge as ContentTooLargeError).detail).toBe("Too large.");
    expect((tooLarge as ContentTooLargeError).message).toBe("Too large.");
    const limited = await call("csv").catch((error: unknown) => error);
    expect(limited).toBeInstanceOf(RateLimitedError);
    expect((limited as RateLimitedError).retryAfterSeconds).toBe(30);
    await expect(call("graphml")).rejects.toBeInstanceOf(UnexpectedResponseError);
  });
});

describe("downloader", () => {
  it("follows a temporary link and removes it", () => {
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    downloader.start("/api/v1/cases/x/export?format=json");
    expect(click).toHaveBeenCalledTimes(1);
    const link = click.mock.contexts[0] as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/api/v1/cases/x/export?format=json");
    expect(link.isConnected).toBe(false);
  });
});

it("has a default message for a 413 without detail", () => {
  expect(new ContentTooLargeError().message).toBe("This is too large to do in one request.");
});
