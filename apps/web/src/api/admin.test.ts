import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { describeAdminError, USER_NOT_FOUND_MESSAGE, VERIFY_BUSY_MESSAGE } from "../admin/errors";
import { clearCookies, FakeApi, json, TEST_USER } from "../test/fakeApi";
import { createUser, listAudit, listUsers, resetPassword, verifyAudit } from "./admin";
import {
  ConflictError,
  ForbiddenError,
  NetworkError,
  NotFoundError,
  RateLimitedError,
  RequestTimeoutError,
  UnexpectedResponseError,
  ValidationError,
} from "./client";

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

describe("admin API shapes", () => {
  it("returns a created user without the password field", async () => {
    api.on("POST", "/users", () => json({ ...TEST_USER, temporary_password: "Tmp" }, 201));
    const created = await createUser({ email: "a@example.org", display_name: "A", role: "viewer" });
    expect(created.temporaryPassword).toBe("Tmp");
    expect(created.user).toEqual(TEST_USER);
    expect("temporary_password" in created.user).toBe(false);
  });

  it.each([
    ["POST /users", () => createUser({ email: "a@b.cd", display_name: "A", role: "viewer" })],
    ["GET /users", () => listUsers()],
    ["POST /users/x/password-reset", () => resetPassword("x")],
    ["GET /audit", () => listAudit()],
    ["POST /audit/verify", () => verifyAudit()],
  ])("rejects an odd answer to %s", async (route, call) => {
    const [method, path] = route.split(" ") as [string, string];
    api.on(method, path, () => json({ unexpected: true }));
    await expect(call()).rejects.toBeInstanceOf(UnexpectedResponseError);
  });

  it("rejects a created user whose temporary password is not text", async () => {
    api.on("POST", "/users", () => json({ ...TEST_USER, temporary_password: 5 }, 201));
    await expect(
      createUser({ email: "a@example.org", display_name: "A", role: "viewer" }),
    ).rejects.toBeInstanceOf(UnexpectedResponseError);
  });
});

describe("describeAdminError", () => {
  it.each([
    [new ValidationError([{ field: "email", message: "Bad." }]), "Bad."],
    [
      new ConflictError("At least one active admin must remain."),
      "At least one active admin must remain.",
    ],
    [new ForbiddenError(), "You don't have permission to do this."],
    [new NotFoundError("User not found."), USER_NOT_FOUND_MESSAGE],
    [new RateLimitedError(10), VERIFY_BUSY_MESSAGE],
    [new NetworkError(), "Cannot reach Nightloom. Check your connection and try again."],
    [new RequestTimeoutError(), "Nightloom did not answer in time. Try again."],
    [new UnexpectedResponseError(500), "Something went wrong. Try again in a moment."],
  ])("%#", (error, message) => {
    expect(describeAdminError(error)).toBe(message);
  });
});
