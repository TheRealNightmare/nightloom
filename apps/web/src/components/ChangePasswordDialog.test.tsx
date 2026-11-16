import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CSRF_HEADER } from "../api/client";
import { AuthGate } from "../auth/AuthGate";
import { AuthProvider } from "../auth/AuthProvider";
import { SESSION_ENDED_NOTICE } from "../auth/context";
import { validatePasswordChange } from "../auth/passwordPolicy";
import { clearCookies, FakeApi, json, noContent, READY_BODY, TEST_USER } from "../test/fakeApi";
import { FakeSigma } from "../test/sigmaMock";
import { PASSWORD_CHANGED_MESSAGE } from "./ChangePasswordDialog";

vi.mock("sigma", () => import("../test/sigmaMock"));

const CURRENT = "current-password-1";
const NEXT = "brand-new-password-2";

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(TEST_USER))
    .install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  FakeSigma.reset();
  clearCookies();
});

async function openDialog() {
  render(
    <AuthProvider>
      <AuthGate />
    </AuthProvider>,
  );
  const trigger = await screen.findByRole("button", { name: /Ada Analyst/ });
  await userEvent.click(trigger);
  await userEvent.click(screen.getByRole("menuitem", { name: "Change password" }));
  return { trigger, dialog: screen.getByRole("dialog", { name: "Change password" }) };
}

const current = () => screen.getByLabelText("Current password");
const next = () => screen.getByLabelText("New password");
const confirm = () => screen.getByLabelText("Confirm new password");
const save = () => screen.getByRole("button", { name: "Change password" });

async function fill(c: string, n: string, k: string) {
  if (c) await userEvent.type(current(), c);
  if (n) await userEvent.type(next(), n);
  if (k) await userEvent.type(confirm(), k);
}

describe("validatePasswordChange", () => {
  it("accepts a valid change", () => {
    expect(validatePasswordChange(CURRENT, NEXT, NEXT)).toEqual({});
  });

  it.each([
    ["", NEXT, NEXT, { current: "Enter your current password." }],
    [CURRENT, "short", "short", { next: "New password must be at least 12 characters long." }],
    [
      CURRENT,
      "x".repeat(129),
      "x".repeat(129),
      { next: "New password must be at most 128 characters long." },
    ],
    [CURRENT, CURRENT, CURRENT, { next: "New password must differ from the current one." }],
    [CURRENT, NEXT, "other", { confirm: "Passwords do not match." }],
  ])("rejects %j / %j / %j", (c, n, k, errors) => {
    expect(validatePasswordChange(c, n, k)).toEqual(errors);
  });

  it("counts characters, not UTF-16 units", () => {
    // 12 emoji are 24 UTF-16 units but 12 characters.
    expect(validatePasswordChange(CURRENT, "🌙".repeat(11), "🌙".repeat(11)).next).toMatch(
      /at least/,
    );
    expect(validatePasswordChange(CURRENT, "🌙".repeat(12), "🌙".repeat(12))).toEqual({});
    expect(validatePasswordChange(CURRENT, "🌙".repeat(128), "🌙".repeat(128))).toEqual({});
  });
});

describe("ChangePasswordDialog", () => {
  it("is an accessible modal with labelled fields and focus on the first one", async () => {
    const { dialog } = await openDialog();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(current()).toHaveFocus();
    expect(current()).toHaveAttribute("autocomplete", "current-password");
    expect(next()).toHaveAttribute("autocomplete", "new-password");
    expect(confirm()).toHaveAttribute("autocomplete", "new-password");
    expect(next()).toHaveAccessibleDescription("12–128 characters.");
    expect(dialog.querySelector('[aria-live="polite"]')).not.toBeNull();
  });

  it("traps focus with Tab and Shift+Tab", async () => {
    await openDialog();
    expect(current()).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(save()).toHaveFocus();
    await userEvent.tab();
    expect(current()).toHaveFocus();
    // Walk the whole dialog: it never leaves.
    for (let i = 0; i < 8; i++) await userEvent.tab();
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
  });

  it("pulls focus back in when it is outside", async () => {
    await openDialog();
    (document.activeElement as HTMLElement).blur();
    await userEvent.tab();
    expect(current()).toHaveFocus();
    (document.activeElement as HTMLElement).blur();
    await userEvent.tab({ shift: true });
    expect(save()).toHaveFocus();
  });

  it("closes on Escape and returns focus to the menu button", async () => {
    const { trigger } = await openDialog();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes on Cancel", async () => {
    const { trigger } = await openDialog();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("checks the fields before calling the API", async () => {
    await openDialog();
    await fill(CURRENT, "short", "different");
    await userEvent.click(save());
    expect(next()).toHaveAttribute("aria-invalid", "true");
    expect(next()).toHaveAccessibleDescription(/at least 12 characters/);
    expect(confirm()).toHaveAccessibleDescription("Passwords do not match.");
    expect(current()).not.toHaveAttribute("aria-invalid");
    expect(api.callsTo("POST", "/auth/password")).toHaveLength(0);
  });

  it("changes the password and says other sessions were signed out", async () => {
    api.on("POST", "/auth/password", () => noContent());
    const { trigger } = await openDialog();
    await fill(CURRENT, NEXT, NEXT);
    await userEvent.click(save());
    const done = await screen.findByRole("button", { name: "Done" });
    expect(screen.getByRole("status")).toHaveTextContent(PASSWORD_CHANGED_MESSAGE);
    await waitFor(() => {
      expect(done).toHaveFocus();
    });
    const [call] = api.callsTo("POST", "/auth/password");
    expect(call?.headers[CSRF_HEADER]).toBe("csrf-token");
    expect(JSON.parse(call?.init.body as string)).toEqual({
      current_password: CURRENT,
      new_password: NEXT,
    });
    await userEvent.click(done);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("shows the server's validation message", async () => {
    api.on("POST", "/auth/password", () =>
      json({ detail: "Password must not contain your email address." }, 422),
    );
    await openDialog();
    await fill(CURRENT, NEXT, NEXT);
    await userEvent.click(save());
    await waitFor(() => {
      expect(next()).toHaveAccessibleDescription(/Password must not contain your email address\./);
    });
    expect(save()).toBeEnabled();
  });

  it("shows field-less server messages in the form error", async () => {
    api.on("POST", "/auth/password", () =>
      json({ detail: [{ loc: ["body", "other"], msg: "Something odd." }] }, 422),
    );
    const { dialog } = await openDialog();
    await fill(CURRENT, NEXT, NEXT);
    await userEvent.click(save());
    await waitFor(() => {
      expect(dialog.querySelector('[aria-live="polite"]')).toHaveTextContent("Something odd.");
    });
  });

  it("marks a wrong current password", async () => {
    api.on("POST", "/auth/password", () => json({ detail: "Current password is incorrect." }, 400));
    await openDialog();
    await fill(CURRENT, NEXT, NEXT);
    await userEvent.click(save());
    await waitFor(() => {
      expect(current()).toHaveAccessibleDescription("Current password is incorrect.");
    });
  });

  it("shows rate limiting in the form error", async () => {
    api.on("POST", "/auth/password", () => json({}, 429, { "Retry-After": "900" }));
    const { dialog } = await openDialog();
    await fill(CURRENT, NEXT, NEXT);
    await userEvent.click(save());
    await waitFor(() => {
      expect(dialog.querySelector('[aria-live="polite"]')).toHaveTextContent(
        "Too many attempts. Try again in 15 minutes.",
      );
    });
  });

  it("returns to sign-in when the session has ended", async () => {
    api.on("POST", "/auth/password", () => json({ detail: "Not authenticated." }, 401));
    await openDialog();
    await fill(CURRENT, NEXT, NEXT);
    await userEvent.click(save());
    expect(await screen.findByText(SESSION_ENDED_NOTICE)).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("disables the button while saving", async () => {
    api.on("POST", "/auth/password", () => new Promise<Response>(() => {}));
    await openDialog();
    await fill(CURRENT, NEXT, NEXT);
    await userEvent.click(save());
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  });
});
