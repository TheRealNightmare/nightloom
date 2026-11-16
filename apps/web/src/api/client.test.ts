import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  fetchReadiness,
  getJson,
  interpretReady,
  isReadyResponse,
  type ReadyResponse,
} from "./client";

const READY: ReadyResponse = {
  status: "ready",
  checks: {
    postgres: { ok: true, latency_ms: 1.2, error: null },
    neo4j: { ok: true, latency_ms: 3.4, error: null },
    valkey: { ok: true, latency_ms: 0.5, error: null },
    storage: { ok: true, latency_ms: 2.1, error: null },
  },
};

const NOT_READY: ReadyResponse = {
  status: "not_ready",
  checks: {
    ...READY.checks,
    neo4j: { ok: false, latency_ms: 2000, error: "timed out after 2s" },
    storage: { ok: false, latency_ms: 4.2, error: "ConnectError" },
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A fetch that never answers until its signal aborts. */
function hangingFetch(): typeof fetch {
  return vi.fn(
    (_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("The operation was aborted.", "AbortError"));
        });
      }),
  );
}

afterEach(() => {
  vi.useRealTimers();
});

describe("getJson", () => {
  it("requests a relative /api/v1 URL and returns status and body", async () => {
    const fetchImpl = vi.fn(() => Promise.resolve(jsonResponse({ hello: "world" }, 201)));
    const result = await getJson("/thing", { fetchImpl });
    expect(result).toEqual({ status: 201, body: { hello: "world" } });
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/thing",
      expect.objectContaining({ method: "GET", headers: { Accept: "application/json" } }),
    );
  });

  it("rejects a non-JSON answer such as a proxy error page", async () => {
    const fetchImpl = vi.fn(() =>
      Promise.resolve(
        new Response("<html>502 Bad Gateway</html>", {
          status: 502,
          headers: { "Content-Type": "text/html" },
        }),
      ),
    );
    await expect(getJson("/ready", { fetchImpl })).rejects.toMatchObject({
      kind: "http",
      status: 502,
    });
  });

  it("rejects a body that claims JSON but is not", async () => {
    const fetchImpl = vi.fn(() =>
      Promise.resolve(
        new Response("{not json", { headers: { "Content-Type": "application/json" } }),
      ),
    );
    await expect(getJson("/ready", { fetchImpl })).rejects.toMatchObject({
      kind: "invalid_body",
    });
  });

  it("reports network failures", async () => {
    const fetchImpl = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(getJson("/ready", { fetchImpl })).rejects.toMatchObject({
      kind: "network",
      message: "Failed to fetch",
    });
  });

  it("times out", async () => {
    vi.useFakeTimers();
    const promise = getJson("/ready", { fetchImpl: hangingFetch(), timeoutMs: 1000 });
    const assertion = expect(promise).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(1000);
    await assertion;
  });

  it("can be aborted by the caller", async () => {
    const controller = new AbortController();
    const promise = getJson("/ready", { fetchImpl: hangingFetch(), signal: controller.signal });
    controller.abort();
    await expect(promise).rejects.toBeInstanceOf(ApiError);
    await expect(promise).rejects.toMatchObject({ kind: "aborted" });
  });

  it("does not even start when the signal is already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      getJson("/ready", { fetchImpl: hangingFetch(), signal: controller.signal }),
    ).rejects.toMatchObject({ kind: "aborted" });
  });
});

describe("isReadyResponse", () => {
  it("accepts the backend's shapes", () => {
    expect(isReadyResponse(READY)).toBe(true);
    expect(isReadyResponse(NOT_READY)).toBe(true);
    expect(isReadyResponse({ status: "ready", checks: {} })).toBe(true);
  });

  it.each([
    null,
    [],
    "ready",
    { status: "ok", checks: {} },
    { status: "ready" },
    { status: "ready", checks: { postgres: { ok: "yes", latency_ms: 1 } } },
    { status: "ready", checks: { postgres: { ok: true, latency_ms: 1, error: 5 } } },
  ])("rejects %j", (value) => {
    expect(isReadyResponse(value)).toBe(false);
  });
});

describe("interpretReady", () => {
  it("maps 200 + ready to ready", () => {
    expect(interpretReady({ status: 200, body: READY })).toEqual({
      kind: "ready",
      checks: READY.checks,
    });
  });

  it("maps 503 + not_ready to degraded with the failing checks", () => {
    expect(interpretReady({ status: 503, body: NOT_READY })).toEqual({
      kind: "degraded",
      failed: [
        { name: "neo4j", error: "timed out after 2s" },
        { name: "storage", error: "ConnectError" },
      ],
    });
  });

  it("reports degraded even when no single check is marked failed", () => {
    expect(
      interpretReady({ status: 503, body: { status: "not_ready", checks: {} } }),
    ).toMatchObject({ kind: "degraded", failed: [{ name: "api" }] });
  });

  it("fills in a missing error message", () => {
    expect(
      interpretReady({
        status: 503,
        body: { status: "not_ready", checks: { valkey: { ok: false, latency_ms: 1 } } },
      }),
    ).toEqual({ kind: "degraded", failed: [{ name: "valkey", error: "failed" }] });
  });

  it("treats other statuses or bodies as offline", () => {
    expect(interpretReady({ status: 404, body: { detail: "Not Found" } })).toMatchObject({
      kind: "offline",
      reason: expect.stringContaining("HTTP 404") as unknown,
    });
    expect(interpretReady({ status: 200, body: { status: "weird" } }).kind).toBe("offline");
  });
});

describe("fetchReadiness", () => {
  it("returns ready", async () => {
    const fetchImpl = vi.fn(() => Promise.resolve(jsonResponse(READY)));
    await expect(fetchReadiness({ fetchImpl })).resolves.toMatchObject({ kind: "ready" });
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/ready", expect.anything());
  });

  it("returns degraded for a 503 readiness body", async () => {
    const fetchImpl = vi.fn(() => Promise.resolve(jsonResponse(NOT_READY, 503)));
    const result = await fetchReadiness({ fetchImpl });
    expect(result.kind).toBe("degraded");
  });

  it("returns offline when the API cannot be reached", async () => {
    const fetchImpl = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(fetchReadiness({ fetchImpl })).resolves.toEqual({
      kind: "offline",
      reason: "Failed to fetch",
    });
  });

  it("returns offline on timeout", async () => {
    vi.useFakeTimers();
    const promise = fetchReadiness({ fetchImpl: hangingFetch(), timeoutMs: 250 });
    await vi.advanceTimersByTimeAsync(250);
    await expect(promise).resolves.toEqual({
      kind: "offline",
      reason: "No answer within 250 ms",
    });
  });

  it("returns offline when a non-Error is thrown", async () => {
    const fetchImpl = vi.fn(() => Promise.reject(new Error("boom")));
    await expect(fetchReadiness({ fetchImpl })).resolves.toMatchObject({ kind: "offline" });
  });

  it("propagates aborts so callers can ignore them", async () => {
    const controller = new AbortController();
    const promise = fetchReadiness({ fetchImpl: hangingFetch(), signal: controller.signal });
    controller.abort();
    await expect(promise).rejects.toMatchObject({ kind: "aborted" });
  });

  it("uses the global fetch by default", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(READY)));
    vi.stubGlobal("fetch", fetchSpy);
    await expect(fetchReadiness()).resolves.toMatchObject({ kind: "ready" });
    expect(fetchSpy).toHaveBeenCalledOnce();
  });
});
