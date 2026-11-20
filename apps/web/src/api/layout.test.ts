import { afterEach, describe, expect, it } from "vitest";

import { FakeApi, json, noContent } from "../test/fakeApi";
import { NotFoundError, UnexpectedResponseError } from "./client";
import { getLayout, isApiLayout, KEEPALIVE_MAX_BYTES, saveLayout } from "./layout";

const CASE = "6f1c0d7e-3b0a-4f7e-9a51-1d2e3f4a5b6c";

afterEach(() => {
  document.cookie = "nightloom_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("layout API", () => {
  it("reads the caller's pins", async () => {
    const api = new FakeApi()
      .on("GET", `/cases/${CASE}/layout`, () =>
        json({ positions: { "domain:example.org": [1, 2] }, updated_at: null }),
      )
      .install();
    expect(await getLayout(CASE)).toEqual({
      positions: { "domain:example.org": [1, 2] },
      updated_at: null,
    });
    expect(api.callsTo("GET", `/cases/${CASE}/layout`)).toHaveLength(1);
  });

  it("refuses a malformed answer", async () => {
    new FakeApi()
      .on("GET", `/cases/${CASE}/layout`, () => json({ positions: { a: [1, "x"] } }))
      .install();
    await expect(getLayout(CASE)).rejects.toBeInstanceOf(UnexpectedResponseError);
    expect(isApiLayout({ positions: [], updated_at: null })).toBe(false);
    expect(isApiLayout(null)).toBe(false);
  });

  it("replaces the pins with PUT, with the CSRF header, keepalive when asked", async () => {
    document.cookie = "nightloom_csrf=token-1";
    const api = new FakeApi().on("PUT", `/cases/${CASE}/layout`, () => noContent()).install();
    await saveLayout(CASE, { "domain:example.org": [3, 4] }, { keepalive: true });
    const [call] = api.callsTo("PUT", `/cases/${CASE}/layout`);
    expect(JSON.parse(call?.init.body as string)).toEqual({
      positions: { "domain:example.org": [3, 4] },
    });
    expect(call?.headers["X-CSRF-Token"]).toBe("token-1");
    expect(call?.init.keepalive).toBe(true);
  });

  it("drops keepalive for bodies browsers would refuse", async () => {
    const api = new FakeApi().on("PUT", `/cases/${CASE}/layout`, () => noContent()).install();
    const positions: Record<string, [number, number]> = {};
    for (let i = 0; JSON.stringify({ positions }).length <= KEEPALIVE_MAX_BYTES; i += 1) {
      positions[`domain:host-${String(i)}.example.org`] = [i, i];
    }
    await saveLayout(CASE, positions, { keepalive: true });
    expect(api.callsTo("PUT", `/cases/${CASE}/layout`)[0]?.init.keepalive).toBeUndefined();
  });

  it("reports a case that is gone", async () => {
    new FakeApi()
      .on("PUT", `/cases/${CASE}/layout`, () => json({ detail: "Case not found." }, 404))
      .install();
    await expect(saveLayout(CASE, {})).rejects.toBeInstanceOf(NotFoundError);
  });
});
