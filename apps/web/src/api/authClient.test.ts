import { afterEach, describe, expect, it, vi } from "vitest";

import { clearCookies, FakeApi, json, noContent, TEST_USER } from "../test/fakeApi";
import {
  apiRequest,
  changePassword,
  CSRF_HEADER,
  InvalidCredentialsError,
  isUser,
  login,
  logout,
  me,
  NetworkError,
  onSessionExpired,
  parseRetryAfter,
  RateLimitedError,
  readCookie,
  RequestAbortedError,
  RequestTimeoutError,
  SessionExpiredError,
  UnexpectedResponseError,
  ValidationError,
  WRONG_CURRENT_PASSWORD,
} from "./client";

afterEach(() => {
  clearCookies();
  vi.useRealTimers();
});

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

describe("readCookie", () => {
  it.each([
    ["nightloom_csrf=abc", "abc"],
    ["a=1; nightloom_csrf=abc; b=2", "abc"],
    ["  nightloom_csrf = abc  ", "abc"],
    ['nightloom_csrf="quoted"', "quoted"],
    ["nightloom_csrf=a%20b", "a b"],
    ["nightloom_csrf=%E0%A4%A", "%E0%A4%A"],
    ["nightloom_csrf=a=b=c", "a=b=c"],
    ["x_nightloom_csrf=no; nightloom_csrf=yes", "yes"],
  ])("reads %j", (cookies, expected) => {
    expect(readCookie("nightloom_csrf", cookies)).toBe(expected);
  });

  it.each([
    "",
    "other=1",
    "nightloom_csrf=",
    'nightloom_csrf=""',
    "nightloom_csrf",
    "x_nightloom_csrf=1",
  ])("returns null for %j", (cookies) => {
    expect(readCookie("nightloom_csrf", cookies)).toBeNull();
  });
});

describe("parseRetryAfter", () => {
  const now = Date.parse("2026-11-16T12:00:00Z");

  it.each([
    ["120", 120],
    [" 30 ", 30],
    ["0", 1],
    ["Mon, 16 Nov 2026 12:02:30 GMT", 150],
    ["Mon, 16 Nov 2026 11:00:00 GMT", 1],
  ])("parses %j", (value, expected) => {
    expect(parseRetryAfter(value, now)).toBe(expected);
  });

  it.each([null, "", "soon", "-5", "1.5"])("ignores %j", (value) => {
    expect(parseRetryAfter(value, now)).toBeNull();
  });

  it("defaults to the current time", () => {
    expect(parseRetryAfter("5")).toBe(5);
  });
});

describe("apiRequest", () => {
  it("sends the CSRF cookie as a header on POST, with same-origin credentials", async () => {
    document.cookie = "nightloom_csrf=token-123";
    const api = new FakeApi().on("POST", "/thing", () => json({ ok: true }));
    await expect(
      apiRequest("/thing", { method: "POST", body: { a: 1 }, fetchImpl: api.fetch }),
    ).resolves.toEqual({
      ok: true,
    });
    const [call] = api.calls;
    expect(call?.headers[CSRF_HEADER]).toBe("token-123");
    expect(call?.headers["Content-Type"]).toBe("application/json");
    expect(call?.init.credentials).toBe("same-origin");
    expect(call?.init.body).toBe('{"a":1}');
  });

  it.each(["PUT", "PATCH", "DELETE"] as const)("sends the CSRF header on %s", async (method) => {
    const api = new FakeApi().on(method, "/thing", () => noContent());
    await apiRequest("/thing", { method, cookies: "nightloom_csrf=t", fetchImpl: api.fetch });
    expect(api.calls[0]?.headers[CSRF_HEADER]).toBe("t");
  });

  it("does not send the CSRF header on GET", async () => {
    document.cookie = "nightloom_csrf=token-123";
    const api = new FakeApi().on("GET", "/thing", () => json({}));
    await apiRequest("/thing", { fetchImpl: api.fetch });
    expect(api.calls[0]?.headers).not.toHaveProperty(CSRF_HEADER);
    expect(api.calls[0]?.headers).not.toHaveProperty("Content-Type");
    expect(api.calls[0]?.init.credentials).toBe("same-origin");
  });

  it("omits the CSRF header when there is no cookie", async () => {
    const api = new FakeApi().on("POST", "/thing", () => noContent());
    await expect(
      apiRequest("/thing", { method: "POST", fetchImpl: api.fetch }),
    ).resolves.toBeUndefined();
    expect(api.calls[0]?.headers).not.toHaveProperty(CSRF_HEADER);
  });

  it("maps 401 to SessionExpired and notifies listeners", async () => {
    const listener = vi.fn();
    const unsubscribe = onSessionExpired(listener);
    const api = new FakeApi().on("GET", "/thing", () =>
      json({ detail: "Not authenticated." }, 401),
    );
    await expect(apiRequest("/thing", { fetchImpl: api.fetch })).rejects.toBeInstanceOf(
      SessionExpiredError,
    );
    expect(listener).toHaveBeenCalledOnce();

    await expect(
      apiRequest("/thing", { fetchImpl: api.fetch, notifySessionExpired: false }),
    ).rejects.toBeInstanceOf(SessionExpiredError);
    expect(listener).toHaveBeenCalledOnce();

    unsubscribe();
    await expect(apiRequest("/thing", { fetchImpl: api.fetch })).rejects.toBeInstanceOf(
      SessionExpiredError,
    );
    expect(listener).toHaveBeenCalledOnce();
  });

  it("maps 429 to RateLimited with Retry-After", async () => {
    const api = new FakeApi().on("GET", "/thing", () =>
      json({ detail: "Too many failed attempts. Try again later." }, 429, { "Retry-After": "600" }),
    );
    const error = await apiRequest("/thing", { fetchImpl: api.fetch }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(RateLimitedError);
    expect(error).toMatchObject({ kind: "rate_limited", retryAfterSeconds: 600 });
  });

  it("maps 429 without Retry-After to a null delay", async () => {
    const api = new FakeApi().on("GET", "/thing", () => json({}, 429));
    await expect(apiRequest("/thing", { fetchImpl: api.fetch })).rejects.toMatchObject({
      retryAfterSeconds: null,
    });
  });

  it("maps a 422 with a string detail to one general message", async () => {
    const api = new FakeApi().on("POST", "/thing", () =>
      json({ detail: "Password must be at least 12 characters long." }, 422),
    );
    const error = await apiRequest("/thing", { method: "POST", fetchImpl: api.fetch }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ValidationError);
    expect((error as ValidationError).messages).toEqual([
      { field: null, message: "Password must be at least 12 characters long." },
    ]);
    expect((error as ValidationError).message).toBe(
      "Password must be at least 12 characters long.",
    );
  });

  it("maps FastAPI's 422 list to field messages", async () => {
    const api = new FakeApi().on("POST", "/thing", () =>
      json(
        {
          detail: [
            { loc: ["body", "new_password"], msg: "Field required", type: "missing" },
            { loc: ["body"], msg: "Bad body", type: "x" },
            { loc: "weird", msg: "No location" },
            { msg: 5 },
            "junk",
          ],
        },
        422,
      ),
    );
    await expect(
      apiRequest("/thing", { method: "POST", fetchImpl: api.fetch }),
    ).rejects.toMatchObject({
      messages: [
        { field: "new_password", message: "Field required" },
        { field: null, message: "Bad body" },
        { field: null, message: "No location" },
      ],
    });
  });

  it.each([{ detail: [] }, { detail: 5 }, ["x"]])(
    "maps an unusable 422 body (%j) to Unexpected",
    async (body) => {
      const api = new FakeApi().on("POST", "/thing", () => json(body, 422));
      await expect(
        apiRequest("/thing", { method: "POST", fetchImpl: api.fetch }),
      ).rejects.toMatchObject({
        kind: "unexpected",
        status: 422,
      });
    },
  );

  it("maps other statuses to Unexpected without the server's text", async () => {
    const api = new FakeApi().on("GET", "/thing", () => json({ detail: "secret internals" }, 500));
    const error = await apiRequest("/thing", { fetchImpl: api.fetch }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(UnexpectedResponseError);
    expect((error as Error).message).not.toContain("secret");
    expect(error).toMatchObject({ status: 500 });
  });

  it("maps a non-JSON answer to Unexpected", async () => {
    const api = new FakeApi().on(
      "GET",
      "/thing",
      () =>
        new Response("<html>502</html>", { status: 502, headers: { "Content-Type": "text/html" } }),
    );
    await expect(apiRequest("/thing", { fetchImpl: api.fetch })).rejects.toMatchObject({
      status: 502,
    });
  });

  it("maps a 200 that is not JSON to Unexpected", async () => {
    const api = new FakeApi().on("GET", "/thing", () => new Response("ok", { status: 200 }));
    await expect(apiRequest("/thing", { fetchImpl: api.fetch })).rejects.toBeInstanceOf(
      UnexpectedResponseError,
    );
  });

  it("maps invalid JSON to Unexpected", async () => {
    const api = new FakeApi().on(
      "GET",
      "/thing",
      () => new Response("{nope", { headers: { "Content-Type": "application/json" } }),
    );
    await expect(apiRequest("/thing", { fetchImpl: api.fetch })).rejects.toBeInstanceOf(
      UnexpectedResponseError,
    );
  });

  it("maps a failed fetch to Network", async () => {
    const fetchImpl = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));
    const error = await apiRequest("/thing", { fetchImpl }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(NetworkError);
    expect((error as Error).message).not.toContain("Failed to fetch");
  });

  it("maps a slow answer to Timeout", async () => {
    vi.useFakeTimers();
    const promise = apiRequest("/thing", { fetchImpl: hangingFetch(), timeoutMs: 1000 });
    const assertion = expect(promise).rejects.toBeInstanceOf(RequestTimeoutError);
    await vi.advanceTimersByTimeAsync(1000);
    await assertion;
  });

  it("maps a timeout while reading the body to Timeout", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn(() =>
      Promise.resolve({
        status: 200,
        headers: new Headers({ "Content-Type": "application/json" }),
        json: () =>
          new Promise((_resolve, reject) => {
            setTimeout(() => {
              reject(new DOMException("aborted", "AbortError"));
            }, 2000);
          }),
      } as unknown as Response),
    );
    const promise = apiRequest("/thing", { fetchImpl, timeoutMs: 1000 });
    const assertion = expect(promise).rejects.toBeInstanceOf(RequestTimeoutError);
    await vi.advanceTimersByTimeAsync(2000);
    await assertion;
  });

  it("can be aborted by the caller, before or during the request", async () => {
    const before = new AbortController();
    before.abort();
    await expect(
      apiRequest("/thing", { fetchImpl: hangingFetch(), signal: before.signal }),
    ).rejects.toBeInstanceOf(RequestAbortedError);

    const during = new AbortController();
    const promise = apiRequest("/thing", { fetchImpl: hangingFetch(), signal: during.signal });
    during.abort();
    await expect(promise).rejects.toBeInstanceOf(RequestAbortedError);
  });

  it("uses the global fetch and document.cookie by default", async () => {
    document.cookie = "nightloom_csrf=global";
    const api = new FakeApi().on("POST", "/thing", () => noContent()).install();
    await apiRequest("/thing", { method: "POST" });
    expect(api.calls[0]?.headers[CSRF_HEADER]).toBe("global");
  });
});

describe("auth endpoints", () => {
  it("login posts the credentials and returns the user", async () => {
    const api = new FakeApi().on("POST", "/auth/login", () => json({ user: TEST_USER }));
    await expect(login("a@example.org", "pw", { fetchImpl: api.fetch })).resolves.toEqual(
      TEST_USER,
    );
    expect(JSON.parse(api.calls[0]?.init.body as string)).toEqual({
      email: "a@example.org",
      password: "pw",
    });
  });

  it("login maps 401 to InvalidCredentials and does not report an ended session", async () => {
    const listener = vi.fn();
    const unsubscribe = onSessionExpired(listener);
    const api = new FakeApi().on("POST", "/auth/login", () =>
      json({ detail: "Invalid email or password." }, 401),
    );
    const error = await login("a@example.org", "bad", { fetchImpl: api.fetch }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(InvalidCredentialsError);
    expect((error as Error).message).toBe("Invalid email or password.");
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it.each([{}, { user: { id: 1 } }, [], { user: { ...TEST_USER, role: "root" } }])(
    "login rejects a malformed body %j",
    async (body) => {
      const api = new FakeApi().on("POST", "/auth/login", () => json(body));
      await expect(login("a@example.org", "pw", { fetchImpl: api.fetch })).rejects.toBeInstanceOf(
        UnexpectedResponseError,
      );
    },
  );

  it("me returns the user, or SessionExpired on 401", async () => {
    const api = new FakeApi().on("GET", "/auth/me", () => json(TEST_USER));
    await expect(me({ fetchImpl: api.fetch })).resolves.toEqual(TEST_USER);
    api.on("GET", "/auth/me", () => json({ detail: "Not authenticated." }, 401));
    await expect(me({ fetchImpl: api.fetch, notifySessionExpired: false })).rejects.toBeInstanceOf(
      SessionExpiredError,
    );
  });

  it("logout posts with the CSRF header", async () => {
    const api = new FakeApi().on("POST", "/auth/logout", () => noContent());
    await logout({ fetchImpl: api.fetch, cookies: "nightloom_csrf=t" });
    expect(api.calls[0]?.headers[CSRF_HEADER]).toBe("t");
  });

  it("changePassword posts both passwords", async () => {
    const api = new FakeApi().on("POST", "/auth/password", () => noContent());
    await changePassword("old-password-1", "new-password-1", { fetchImpl: api.fetch });
    expect(JSON.parse(api.calls[0]?.init.body as string)).toEqual({
      current_password: "old-password-1",
      new_password: "new-password-1",
    });
  });

  it("changePassword maps 400 to a field message on the current password", async () => {
    const api = new FakeApi().on("POST", "/auth/password", () =>
      json({ detail: "Current password is incorrect." }, 400),
    );
    await expect(
      changePassword("x", "new-password-1", { fetchImpl: api.fetch }),
    ).rejects.toMatchObject({
      kind: "validation",
      messages: [{ field: "current_password", message: WRONG_CURRENT_PASSWORD }],
    });
  });

  it("changePassword passes other errors through", async () => {
    const api = new FakeApi().on("POST", "/auth/password", () => json({}, 500));
    await expect(changePassword("x", "y", { fetchImpl: api.fetch })).rejects.toMatchObject({
      status: 500,
    });
  });

  it("isUser checks the fields the UI uses", () => {
    expect(isUser(TEST_USER)).toBe(true);
    expect(isUser(null)).toBe(false);
    expect(isUser({ ...TEST_USER, is_active: "yes" })).toBe(false);
  });
});
