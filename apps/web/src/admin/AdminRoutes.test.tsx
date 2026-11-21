import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { User } from "../api/client";
import { ADMIN_USER, VIEWER_USER } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, READY_BODY, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

let api: FakeApi;

function signInAs(user: User) {
  api.on("GET", "/auth/me", () => json(user));
}

beforeEach(() => {
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/users", () => json([ADMIN_USER]))
    .on("GET", "/audit?limit=50", () => json({ items: [], next_before_id: null }))
    .on("GET", "/cases?limit=25", () => json({ items: [], next_cursor: null }))
    .install();
});

afterEach(() => {
  clearCookies();
});

const nav = () => screen.getByRole("navigation", { name: "Main" });

describe("admin routes", () => {
  it.each([
    ["analyst", TEST_USER],
    ["viewer", VIEWER_USER],
  ])("show Page not found to an %s and ask the API for nothing", async (_role, user) => {
    signInAs(user);
    for (const path of [
      "/admin/users",
      "/admin/audit",
      "/admin/profiles",
      "/admin/result-cache",
      "/admin",
    ]) {
      const view = renderApp(path);
      expect(await screen.findByRole("heading", { name: "Page not found" })).toBeVisible();
      view.unmount();
    }
    expect(api.callsTo("GET", "/users")).toHaveLength(0);
    expect(api.callsTo("GET", "/audit?limit=50")).toHaveLength(0);
    expect(api.callsTo("GET", "/admin/result-cache")).toHaveLength(0);
  });

  it("hide the admin links from non-admins", async () => {
    signInAs(TEST_USER);
    renderApp("/cases");
    await screen.findByRole("heading", { name: "Cases" });
    expect(within(nav()).queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(within(nav()).queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
    expect(within(nav()).queryByRole("link", { name: "Cache" })).not.toBeInTheDocument();
    expect(within(nav()).queryByRole("link", { name: "Profiles" })).not.toBeInTheDocument();
  });

  it("are linked from the top bar for admins", async () => {
    signInAs(ADMIN_USER);
    const { router } = renderApp("/admin");
    expect(await screen.findByRole("heading", { name: "Users" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/admin/users");
    expect(within(nav()).getByRole("link", { name: "Users" })).toHaveAttribute(
      "href",
      "/admin/users",
    );
    expect(within(nav()).getByRole("link", { name: "Audit log" })).toHaveAttribute(
      "href",
      "/admin/audit",
    );
    expect(within(nav()).getByRole("link", { name: "Profiles" })).toHaveAttribute(
      "href",
      "/admin/profiles",
    );
    expect(within(nav()).getByRole("link", { name: "Cache" })).toHaveAttribute(
      "href",
      "/admin/result-cache",
    );
  });

  it("answer unknown admin paths with Page not found", async () => {
    signInAs(ADMIN_USER);
    renderApp("/admin/nothing-here");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeVisible();
  });
});
