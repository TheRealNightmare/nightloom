import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { User } from "../api/client";
import { ADMIN_USER } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent, READY_BODY, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { COPIED_MESSAGE, ONE_TIME_WARNING } from "./TemporaryPasswordPanel";

const TEMPORARY = "Abcd-Efgh-Jkmn-Pqrs-Tuvw-Xyz2";
const ME: User = { ...ADMIN_USER, display_name: "Ada Admin", email: "ada@example.org" };
const BOB: User = {
  ...TEST_USER,
  id: "9d1f7e2a-3b4c-4d5e-8f60-718293a4b5c6",
  email: "bob@example.org",
  display_name: "Bob Builder",
  role: "analyst",
  last_login_at: "2026-11-18T10:00:00Z",
};
const CAROL: User = {
  ...TEST_USER,
  id: "3c1e9d6c-0b8f-4e9c-8a11-5f0c8a526a0e",
  email: "carol@example.org",
  display_name: "Carol Viewer",
  role: "viewer",
  is_active: false,
};

let api: FakeApi;
let writeText: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(ME))
    .on("GET", "/users", () => json([ME, BOB, CAROL]))
    .install();
  document.cookie = "nightloom_csrf=csrf-token";
  writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
});

afterEach(() => {
  clearCookies();
});

async function openUsers() {
  const view = renderApp("/admin/users");
  await screen.findByRole("table", { name: "Users" });
  return view;
}

const row = (name: string) =>
  screen.getByRole("cell", { name: new RegExp(name) }).closest("tr") as HTMLElement;
const names = () =>
  within(screen.getByRole("table", { name: "Users" }))
    .getAllByRole("row")
    .slice(1)
    .map((r) => r.querySelector("td")?.textContent);

describe("users table", () => {
  it("lists every user with role, status, last sign-in and creation", async () => {
    await openUsers();

    expect(names()).toEqual(["Ada Admin (you)", "Bob Builder", "Carol Viewer"]);
    const bob = within(row("Bob Builder"));
    expect(bob.getByText("bob@example.org")).toBeVisible();
    expect(bob.getByRole("combobox", { name: "Role of Bob Builder" })).toHaveValue("analyst");
    expect(bob.getByText("Active")).toBeVisible();
    expect(within(row("Carol Viewer")).getByText("Deactivated")).toBeVisible();
    expect(within(row("Carol Viewer")).getByText("Never")).toBeVisible();
    // Admins do not reset or deactivate themselves here.
    const me = within(row("Ada Admin"));
    expect(me.queryByRole("button", { name: /Reset password/ })).not.toBeInTheDocument();
    expect(me.queryByRole("button", { name: /Deactivate/ })).not.toBeInTheDocument();
  });

  it("filters by search, role and status", async () => {
    await openUsers();

    await userEvent.type(screen.getByLabelText("Search name or email"), "CAROL@");
    expect(names()).toEqual(["Carol Viewer"]);

    await userEvent.clear(screen.getByLabelText("Search name or email"));
    await userEvent.selectOptions(screen.getByLabelText("Role"), "analyst");
    expect(names()).toEqual(["Bob Builder"]);

    await userEvent.selectOptions(screen.getByLabelText("Role"), "");
    await userEvent.selectOptions(screen.getByLabelText("Status"), "inactive");
    expect(names()).toEqual(["Carol Viewer"]);

    await userEvent.selectOptions(screen.getByLabelText("Status"), "active");
    await userEvent.selectOptions(screen.getByLabelText("Role"), "viewer");
    expect(screen.getByText("No matching users")).toBeVisible();
  });

  it("shows a load error with a way to try again", async () => {
    let fail = true;
    api.on("GET", "/users", () => {
      if (fail) throw new TypeError("Failed to fetch");
      return json([ME, BOB, CAROL]);
    });
    renderApp("/admin/users");
    const message = await screen.findByText(/Cannot reach Nightloom/);
    const alert = message.closest<HTMLElement>("[role=alert]");
    if (!alert) throw new Error("The load error is not an alert");
    fail = false;
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: "Users" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });
});

describe("new user", () => {
  async function submitNewUser() {
    await userEvent.click(screen.getByRole("button", { name: "New user" }));
    const dialog = screen.getByRole("dialog", { name: "New user" });
    await userEvent.type(within(dialog).getByLabelText("Email"), " dan@example.org ");
    await userEvent.type(within(dialog).getByLabelText("Display name"), "Dan Analyst");
    await userEvent.selectOptions(within(dialog).getByLabelText("Role"), "viewer");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create user" }));
  }

  it("creates the user and shows the temporary password once", async () => {
    const dan: User = {
      ...TEST_USER,
      id: "5e0a0c4f-71a6-4d1c-9f3a-0b1c2d3e4f50",
      email: "dan@example.org",
      display_name: "Dan Analyst",
      role: "viewer",
      must_change_password: true,
    };
    api.on("POST", "/users", () => json({ ...dan, temporary_password: TEMPORARY }, 201));
    await openUsers();

    await submitNewUser();

    const panel = await screen.findByRole("dialog", { name: "User created" });
    expect(within(panel).getByLabelText("Temporary password")).toHaveTextContent(TEMPORARY);
    expect(within(panel).getByText(ONE_TIME_WARNING)).toBeVisible();
    expect(within(panel).getByText(/Dan Analyst \(dan@example.org\)/)).toBeVisible();
    const [call] = api.callsTo("POST", "/users");
    expect(JSON.parse(call?.init.body as string)).toEqual({
      email: "dan@example.org",
      display_name: "Dan Analyst",
      role: "viewer",
      generate_password: true,
    });

    await userEvent.click(within(panel).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(TEMPORARY);
    expect(await within(panel).findByText(COPIED_MESSAGE)).toBeVisible();

    await userEvent.click(within(panel).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // Gone from the page once the panel is closed.
    expect(document.body.textContent).not.toContain(TEMPORARY);
    expect(within(row("Dan Analyst")).getByText(/temporary password/)).toBeVisible();
  });

  it("says when copying fails", async () => {
    writeText.mockRejectedValueOnce(new Error("denied"));
    api.on("POST", "/users", () =>
      json({ ...BOB, id: "x", email: "dan@example.org", temporary_password: TEMPORARY }, 201),
    );
    await openUsers();
    await submitNewUser();

    const panel = await screen.findByRole("dialog", { name: "User created" });
    await userEvent.click(within(panel).getByRole("button", { name: "Copy" }));
    expect(await within(panel).findByText(/Could not copy/)).toBeVisible();
  });

  it("checks the fields locally", async () => {
    await openUsers();
    await userEvent.click(screen.getByRole("button", { name: "New user" }));
    const dialog = screen.getByRole("dialog", { name: "New user" });
    expect(within(dialog).getByLabelText("Email")).toHaveFocus();

    await userEvent.type(within(dialog).getByLabelText("Email"), "not-an-email");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create user" }));

    expect(within(dialog).getByText("Enter a valid email address.")).toBeVisible();
    expect(within(dialog).getByText("Enter a display name.")).toBeVisible();
    expect(api.callsTo("POST", "/users")).toHaveLength(0);
  });

  it("shows the server's 422 and 409 messages on the form", async () => {
    api.on("POST", "/users", () =>
      json(
        {
          detail: [
            {
              loc: ["body", "display_name"],
              msg: "String should have at most 100 characters",
              type: "x",
            },
          ],
        },
        422,
      ),
    );
    await openUsers();
    await submitNewUser();
    const dialog = screen.getByRole("dialog", { name: "New user" });
    expect(
      await within(dialog).findByText("String should have at most 100 characters"),
    ).toBeVisible();

    api.on("POST", "/users", () => json({ detail: "A user with this email already exists." }, 409));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create user" }));
    expect(await within(dialog).findByText("A user with this email already exists.")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "User created" })).not.toBeInTheDocument();
  });

  it("can be cancelled", async () => {
    await openUsers();
    await userEvent.click(screen.getByRole("button", { name: "New user" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("row actions", () => {
  it("resets a password after confirmation and shows it once", async () => {
    api.on("POST", `/users/${BOB.id}/password-reset`, () =>
      json({
        user: { ...BOB, must_change_password: true },
        temporary_password: TEMPORARY,
        revoked_sessions: 2,
      }),
    );
    await openUsers();

    await userEvent.click(screen.getByRole("button", { name: "Reset password of Bob Builder" }));
    const confirm = screen.getByRole("dialog", { name: "Reset the password of Bob Builder?" });
    expect(api.callsTo("POST", `/users/${BOB.id}/password-reset`)).toHaveLength(0);
    await userEvent.click(within(confirm).getByRole("button", { name: "Reset password" }));

    const panel = await screen.findByRole("dialog", { name: "Password reset" });
    expect(within(panel).getByLabelText("Temporary password")).toHaveTextContent(TEMPORARY);
    expect(within(panel).getByText(ONE_TIME_WARNING)).toBeVisible();
    expect(api.callsTo("POST", `/users/${BOB.id}/password-reset`)[0]?.headers["X-CSRF-Token"]).toBe(
      "csrf-token",
    );

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(TEMPORARY);
    expect(screen.getByRole("status")).toHaveTextContent("password was reset");
  });

  it("does nothing when the reset is cancelled", async () => {
    await openUsers();
    await userEvent.click(screen.getByRole("button", { name: "Reset password of Bob Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.callsTo("POST", `/users/${BOB.id}/password-reset`)).toHaveLength(0);
  });

  it("changes a role", async () => {
    api.on("PATCH", `/users/${BOB.id}`, () => json({ ...BOB, role: "viewer" }));
    await openUsers();

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Role of Bob Builder" }),
      "viewer",
    );

    expect(await screen.findByText("Bob Builder is now viewer.")).toBeVisible();
    expect(JSON.parse(api.callsTo("PATCH", `/users/${BOB.id}`)[0]?.init.body as string)).toEqual({
      role: "viewer",
    });
    expect(screen.getByRole("combobox", { name: "Role of Bob Builder" })).toHaveValue("viewer");
  });

  it("shows the last-admin conflict inline", async () => {
    api.on("PATCH", `/users/${ME.id}`, () =>
      json({ detail: "You cannot remove your own admin role or deactivate yourself." }, 409),
    );
    await openUsers();

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Role of Ada Admin" }),
      "analyst",
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ada Admin: You cannot remove your own admin role or deactivate yourself.",
    );
    expect(screen.getByRole("combobox", { name: "Role of Ada Admin" })).toHaveValue("admin");
  });

  it("deactivates after confirmation and activates directly", async () => {
    api.on("PATCH", `/users/${BOB.id}`, () => json({ ...BOB, is_active: false }));
    api.on("PATCH", `/users/${CAROL.id}`, () => json({ ...CAROL, is_active: true }));
    await openUsers();

    await userEvent.click(screen.getByRole("button", { name: "Deactivate Bob Builder" }));
    await userEvent.click(
      within(screen.getByRole("dialog", { name: "Deactivate Bob Builder?" })).getByRole("button", {
        name: "Deactivate",
      }),
    );
    expect(await screen.findByText(/Bob Builder is deactivated/)).toBeVisible();
    expect(within(row("Bob Builder")).getByText("Deactivated")).toBeVisible();
    expect(JSON.parse(api.callsTo("PATCH", `/users/${BOB.id}`)[0]?.init.body as string)).toEqual({
      is_active: false,
    });

    await userEvent.click(screen.getByRole("button", { name: "Activate Carol Viewer" }));
    expect(await screen.findByText("Carol Viewer can sign in again.")).toBeVisible();
    expect(within(row("Carol Viewer")).getByText("Active")).toBeVisible();
  });

  it("revokes sessions, and shows errors", async () => {
    api.on("DELETE", `/users/${BOB.id}/sessions`, () => noContent());
    api.on("DELETE", `/users/${CAROL.id}/sessions`, () => json({ detail: "User not found." }, 404));
    await openUsers();

    await userEvent.click(screen.getByRole("button", { name: "Revoke sessions of Bob Builder" }));
    await userEvent.click(
      within(screen.getByRole("dialog", { name: /Sign Bob Builder out/ })).getByRole("button", {
        name: "Revoke sessions",
      }),
    );
    expect(await screen.findByText("Bob Builder is signed out everywhere.")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Revoke sessions of Carol Viewer" }));
    await userEvent.click(
      within(screen.getByRole("dialog", { name: /Sign Carol Viewer out/ })).getByRole("button", {
        name: "Revoke sessions",
      }),
    );
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Carol Viewer: User not found.");
    });
  });

  it("explains a refused self-reset", async () => {
    // The UI does not offer it, but a stale page could still get the 400.
    const { describeAdminError, SELF_RESET_MESSAGE } = await import("./errors");
    const { UnexpectedResponseError } = await import("../api/client");
    expect(describeAdminError(new UnexpectedResponseError(400))).toBe(SELF_RESET_MESSAGE);
  });
});
