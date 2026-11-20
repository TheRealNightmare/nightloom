import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  apiRequest,
  onPasswordChangeRequired,
  PasswordChangeRequiredError,
  type User,
} from "../api/client";
import { FORCED_CHANGE_TITLE } from "../components/ForcedPasswordChange";
import { clearCookies, FakeApi, json, noContent, READY_BODY, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

const PENDING: User = { ...TEST_USER, must_change_password: true };
const REQUIRED = {
  detail: "Choose a new password before you continue.",
  code: "password_change_required",
};
const NEW_PASSWORD = "a brand new passphrase";

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi().on("GET", "/ready", () => json(READY_BODY)).install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

const title = () => screen.findByRole("heading", { name: FORCED_CHANGE_TITLE });

async function fillAndSubmit(current = "Tmp1-Tmp2-Tmp3-Tmp4-Tmp5-Tmp6") {
  await userEvent.type(screen.getByLabelText("Temporary password"), current);
  await userEvent.type(screen.getByLabelText("New password"), NEW_PASSWORD);
  await userEvent.type(screen.getByLabelText("Confirm new password"), NEW_PASSWORD);
  await userEvent.click(screen.getByRole("button", { name: "Set new password" }));
}

describe("forced password change", () => {
  it("replaces the app when /auth/me reports a required change", async () => {
    api.on("GET", "/auth/me", () => json(PENDING));
    api.on("GET", "/cases", () => json({ items: [], next_cursor: null }));
    renderApp("/cases");

    expect(await title()).toBeVisible();
    expect(screen.getByText(/Signed in as/)).toHaveTextContent(TEST_USER.email);
    // No app chrome, and nothing else was asked for.
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(api.callsTo("GET", "/cases")).toHaveLength(0);
    // The form focuses its first field in an effect, after the heading shows.
    await waitFor(() => {
      expect(screen.getByLabelText("Temporary password")).toHaveFocus();
    });
  });

  it("cannot be dismissed with Escape", async () => {
    api.on("GET", "/auth/me", () => json(PENDING));
    renderApp("/cases");
    await title();

    await userEvent.keyboard("{Escape}");

    expect(screen.getByRole("heading", { name: FORCED_CHANGE_TITLE })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });

  it("checks the policy locally before calling the server", async () => {
    api.on("GET", "/auth/me", () => json(PENDING));
    renderApp("/cases");
    await title();

    await userEvent.type(screen.getByLabelText("Temporary password"), "temporary-one");
    await userEvent.type(screen.getByLabelText("New password"), "short");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "different");
    await userEvent.click(screen.getByRole("button", { name: "Set new password" }));

    expect(screen.getByText(/at least 12 characters/)).toBeVisible();
    expect(screen.getByText("Passwords do not match.")).toBeVisible();
    expect(api.callsTo("POST", "/auth/password")).toHaveLength(0);
  });

  it("shows a wrong temporary password next to its field", async () => {
    api.on("GET", "/auth/me", () => json(PENDING));
    api.on("POST", "/auth/password", () => json({ detail: "Current password is incorrect." }, 400));
    renderApp("/cases");
    await title();

    await fillAndSubmit();

    expect(await screen.findByText("Current password is incorrect.")).toBeVisible();
    expect(screen.getByRole("heading", { name: FORCED_CHANGE_TITLE })).toBeVisible();
  });

  it("continues to the page that was asked for after a successful change", async () => {
    api.on("GET", "/auth/me", () => json(PENDING));
    api.on("POST", "/auth/password", () => noContent());
    api.on("GET", "/cases?limit=25", () => json({ items: [], next_cursor: null }));
    const { router } = renderApp("/cases");
    await title();

    await fillAndSubmit();

    expect(await screen.findByRole("heading", { name: "Cases" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/cases");
    const [call] = api.callsTo("POST", "/auth/password");
    expect(JSON.parse(call?.init.body as string)).toEqual({
      current_password: "Tmp1-Tmp2-Tmp3-Tmp4-Tmp5-Tmp6",
      new_password: NEW_PASSWORD,
    });
    expect(call?.headers["X-CSRF-Token"]).toBe("csrf-token");
  });

  it("appears when a request mid-session answers password_change_required", async () => {
    api.on("GET", "/auth/me", () => json(TEST_USER));
    api.on("GET", "/cases?limit=25", () => json(REQUIRED, 403));
    const { router } = renderApp("/cases");

    expect(await title()).toBeVisible();
    expect(router.state.location.pathname).toBe("/cases");

    // After the change the list is asked for again, and now it answers.
    api.on("POST", "/auth/password", () => noContent());
    api.on("GET", "/cases?limit=25", () => json({ items: [], next_cursor: null }));
    await fillAndSubmit();
    expect(await screen.findByText("No cases yet")).toBeVisible();
    expect(api.callsTo("GET", "/cases?limit=25")).toHaveLength(2);
  });

  it("appears straight after a sign-in that reports a required change", async () => {
    api.on("GET", "/auth/me", () => json({ detail: "Not authenticated." }, 401));
    api.on("POST", "/auth/login", () => json({ user: PENDING }));
    renderApp("/cases");

    await userEvent.type(await screen.findByLabelText("Email"), TEST_USER.email);
    await userEvent.type(screen.getByLabelText("Password"), "Tmp1-Tmp2-Tmp3-Tmp4-Tmp5-Tmp6");
    await userEvent.click(screen.getByRole("button", { name: /Sign in/ }));

    expect(await title()).toBeVisible();
  });

  it("offers sign-out", async () => {
    api.on("GET", "/auth/me", () => json(PENDING));
    api.on("POST", "/auth/logout", () => noContent());
    renderApp("/cases");
    await title();

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
    expect(api.callsTo("POST", "/auth/logout")).toHaveLength(1);
  });
});

describe("password_change_required in the client", () => {
  it("throws a typed error and tells the listeners", async () => {
    let told = 0;
    const stop = onPasswordChangeRequired(() => {
      told += 1;
    });
    api.on("GET", "/cases", () => json(REQUIRED, 403));

    await expect(apiRequest("/cases")).rejects.toBeInstanceOf(PasswordChangeRequiredError);
    expect(told).toBe(1);

    stop();
    await expect(apiRequest("/cases")).rejects.toBeInstanceOf(PasswordChangeRequiredError);
    expect(told).toBe(1);
  });

  it("keeps an ordinary 403 a ForbiddenError", async () => {
    let told = 0;
    const stop = onPasswordChangeRequired(() => {
      told += 1;
    });
    api.on("GET", "/users", () => json({ detail: "You do not have permission to do this." }, 403));

    await expect(apiRequest("/users")).rejects.toMatchObject({ kind: "forbidden" });
    await waitFor(() => {
      expect(told).toBe(0);
    });
    stop();
  });
});
