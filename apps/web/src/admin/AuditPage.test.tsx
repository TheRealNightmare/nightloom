import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { AuditEvent } from "../api/cases";
import type { User } from "../api/client";
import { ADMIN_USER } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, READY_BODY, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { CASE_ID_INVALID, DATE_RANGE_INVALID } from "./auditFilters";

const ME: User = { ...ADMIN_USER, display_name: "Ada Admin", email: "ada@example.org" };
const BOB: User = {
  ...TEST_USER,
  id: "9d1f7e2a-3b4c-4d5e-8f60-718293a4b5c6",
  email: "bob@example.org",
  display_name: "Bob Builder",
};
const CASE_ID = "7f8e9d0c-1b2a-4c3d-8e4f-5a6b7c8d9e0f";

function event(id: number, overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id,
    occurred_at: "2026-11-19T10:00:00Z",
    actor_user_id: ME.id,
    actor_label: ME.email,
    actor_ip: "192.0.2.10",
    request_id: `req-${id}-abcdef`,
    action: "auth.login",
    outcome: "success",
    target_type: "user",
    target_id: ME.id,
    case_id: null,
    details: {},
    ...overrides,
  };
}

const RESET = event(3, {
  action: "user.password_reset",
  target_id: BOB.id,
  details: { revoked_sessions: 2 },
});
const FAILED_LOGIN = event(2, {
  actor_user_id: null,
  actor_label: null,
  outcome: "failure",
  target_type: null,
  target_id: null,
  details: { email_sha256: "ab".repeat(32), reason: "wrong_password" },
});
const CASE_EVENT = event(1, {
  action: "case.create",
  target_type: "case",
  target_id: CASE_ID,
  case_id: CASE_ID,
  details: { legal_basis: "owned_assets", allow_person_entities: false },
});

/** The URL the page asks for, with the same encoding it uses. */
function auditPath(params: Record<string, string | number>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) search.set(key, String(value));
  return `/audit?${search.toString()}`;
}

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(ME))
    .on("GET", "/users", () => json([ME, BOB]))
    .on("GET", auditPath({ limit: 50 }), () =>
      json({ items: [RESET, FAILED_LOGIN, CASE_EVENT], next_before_id: null }),
    )
    .install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

async function openAudit() {
  const view = renderApp("/admin/audit");
  await screen.findByRole("table", { name: "Audit events, newest first" });
  return view;
}

const rows = () =>
  within(screen.getByRole("table", { name: "Audit events, newest first" }))
    .getAllByRole("row")
    .slice(1);

describe("audit log", () => {
  it("lists events with readable labels", async () => {
    await openAudit();

    const [reset, failed, created] = rows();
    expect(reset).toHaveTextContent("ada@example.org");
    expect(reset).toHaveTextContent("reset the password of Bob Builder, 2 sessions ended");
    expect(failed).toHaveTextContent("Someone");
    expect(failed).toHaveTextContent("failed to sign in (wrong password)");
    expect(within(failed!).getByText("Failed")).toBeVisible();
    // Case events read like the case Activity tab.
    expect(created).toHaveTextContent("created the case");
  });

  it("sends each filter to the API", async () => {
    await openAudit();
    const filtered = {
      limit: 50,
      action: "user.password_reset",
      outcome: "success",
      actor_user_id: ME.id,
      case_id: CASE_ID,
      since: "2026-11-01T00:00:00.000Z",
      until: "2026-11-20T00:00:00.000Z",
    };
    api.on("GET", auditPath(filtered), () => json({ items: [RESET], next_before_id: null }));

    await userEvent.selectOptions(screen.getByLabelText("Action"), "user.password_reset");
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "success");
    await userEvent.selectOptions(screen.getByLabelText("Actor"), ME.id);
    await userEvent.type(screen.getByLabelText("Case ID"), CASE_ID.toUpperCase());
    await userEvent.type(screen.getByLabelText("From (UTC)"), "2026-11-01");
    await userEvent.type(screen.getByLabelText("To (UTC)"), "2026-11-19");

    expect(await screen.findByText(/reset the password/)).toBeVisible();
    expect(api.callsTo("GET", auditPath(filtered))).toHaveLength(1);
    expect(rows()).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText(/failed to sign in/)).toBeVisible();
  });

  it("does not send a partial case ID or a reversed date range", async () => {
    await openAudit();
    const before = api.calls.length;

    await userEvent.type(screen.getByLabelText("Case ID"), "7f8e9d0c");
    expect(screen.getByRole("alert")).toHaveTextContent(CASE_ID_INVALID);
    await userEvent.clear(screen.getByLabelText("Case ID"));

    await userEvent.type(screen.getByLabelText("From (UTC)"), "2026-11-10");
    api.on("GET", auditPath({ limit: 50, since: "2026-11-10T00:00:00.000Z" }), () =>
      json({ items: [], next_before_id: null }),
    );
    await userEvent.type(screen.getByLabelText("To (UTC)"), "2026-11-01");
    expect(screen.getByRole("alert")).toHaveTextContent(DATE_RANGE_INVALID);

    const sent = api.calls.slice(before).map((c) => c.url);
    expect(sent.some((url) => url.includes("case_id"))).toBe(false);
    expect(sent.some((url) => url.includes("until"))).toBe(false);
  });

  it("pages with Load more", async () => {
    api.on("GET", auditPath({ limit: 50 }), () => json({ items: [RESET], next_before_id: 3 }));
    api.on("GET", auditPath({ before_id: 3, limit: 50 }), () =>
      json({ items: [FAILED_LOGIN], next_before_id: null }),
    );
    await openAudit();
    expect(rows()).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText(/failed to sign in/)).toBeVisible();
    expect(rows()).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("opens an event's details", async () => {
    await openAudit();

    await userEvent.click(screen.getByRole("button", { name: "Details of event 3" }));

    const drawer = screen.getByRole("dialog", { name: "Event #3" });
    expect(within(drawer).getByText("req-3-abcdef")).toBeVisible();
    expect(within(drawer).getByText("192.0.2.10")).toBeVisible();
    expect(within(drawer).getByText("Password reset (user.password_reset)")).toBeVisible();
    expect(within(drawer).getByLabelText("Event details as JSON")).toHaveTextContent(
      '"revoked_sessions": 2',
    );
    await userEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("verifies the chain", async () => {
    api.on("POST", "/audit/verify", () =>
      json({ ok: true, checked: 1204, first_bad_id: null, reason: null }),
    );
    await openAudit();

    await userEvent.click(screen.getByRole("button", { name: "Verify chain" }));

    expect(await screen.findByText("Chain intact: 1,204 events checked.")).toBeVisible();
    expect(api.callsTo("POST", "/audit/verify")[0]?.headers["X-CSRF-Token"]).toBe("csrf-token");
  });

  it("reports a broken chain and a busy verification", async () => {
    api.on("POST", "/audit/verify", () =>
      json({
        ok: false,
        checked: 41,
        first_bad_id: 17,
        reason: "hash does not match the event's contents",
      }),
    );
    await openAudit();

    await userEvent.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(
      await screen.findByText(
        "Chain broken at event #17 (an event was changed): 41 events checked.",
      ),
    ).toBeVisible();

    api.on("POST", "/audit/verify", () =>
      json({ detail: "A verification is already running." }, 429, { "Retry-After": "10" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(await screen.findByText(/already running/)).toBeVisible();
  });

  it("shows a load error with a way to try again", async () => {
    let fail = true;
    api.on("GET", auditPath({ limit: 50 }), () =>
      fail ? json({}, 500) : json({ items: [], next_before_id: null }),
    );
    renderApp("/admin/audit");
    const message = await screen.findByText(/Something went wrong/);
    expect(document.title).toBe("Audit log · Admin · Nightloom");
    fail = false;
    const alert = message.closest<HTMLElement>("[role=alert]");
    if (!alert) throw new Error("The load error is not an alert");
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No events")).toBeVisible();
  });

  it("shows an empty log", async () => {
    api.on("GET", auditPath({ limit: 50 }), () => json({ items: [], next_before_id: null }));
    renderApp("/admin/audit");
    expect(await screen.findByText("No events")).toBeVisible();
  });
});
