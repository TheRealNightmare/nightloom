import { describe, expect, it } from "vitest";

import { AUDIT_ACTIONS } from "../api/admin";
import type { AuditEvent } from "../api/cases";
import type { User } from "../api/client";
import { TEST_USER } from "../test/fakeApi";
import {
  CASE_ID_INVALID,
  DATE_RANGE_INVALID,
  NO_FILTERS,
  toAuditQuery,
  verifyMessage,
} from "./auditFilters";
import { ACTION_LABELS, auditActor, describeAuditEvent } from "./auditLabels";
import { matchesUser } from "./userFilters";

const BOB: User = { ...TEST_USER, id: "u-bob", display_name: "Bob Builder" };

function ev(
  action: string,
  details: Record<string, unknown> = {},
  extra: Partial<AuditEvent> = {},
) {
  return {
    id: 1,
    occurred_at: "2026-11-19T10:00:00Z",
    actor_user_id: "u-admin",
    actor_label: "admin@example.org",
    actor_ip: null,
    request_id: "r",
    action,
    outcome: "success",
    target_type: "user",
    target_id: "u-bob",
    case_id: null,
    details,
    ...extra,
  } satisfies AuditEvent;
}

describe("describeAuditEvent", () => {
  it.each([
    [ev("auth.login"), "signed in"],
    [
      ev("auth.login", { reason: "inactive_user" }, { outcome: "failure" }),
      "failed to sign in (deactivated account)",
    ],
    [ev("auth.logout"), "signed out"],
    [
      ev("auth.password_change", { revoked_sessions: 1, change_required: true }),
      "replaced their temporary password, 1 session ended",
    ],
    [
      ev("auth.password_change", { revoked_sessions: 0 }),
      "changed their password, 0 sessions ended",
    ],
    [
      ev("auth.password_change", { reason: "wrong_current_password" }, { outcome: "failure" }),
      "failed to change their password (wrong current password)",
    ],
    [ev("auth.rate_limited", {}, { outcome: "denied" }), "was rate limited"],
    [
      ev("user.create", { email: "bob@example.org", role: "analyst", credential: "generated" }),
      "created Bob Builder as analyst with a temporary password",
    ],
    [
      ev("user.create", { role: "viewer", credential: "supplied" }, { target_id: "gone" }),
      "created a user as viewer",
    ],
    [
      ev("user.create", { reason: "email_taken" }, { outcome: "failure" }),
      "tried to create a user (email already in use)",
    ],
    [
      ev("user.update", { changes: { role: {}, is_active: {} }, revoked_sessions: 3 }),
      "changed role and active of Bob Builder, 3 sessions ended",
    ],
    [ev("user.update", { changes: {} }), "changed Bob Builder"],
    [
      ev("user.update", { reason: "conflict" }, { outcome: "failure" }),
      "tried to change Bob Builder (not allowed)",
    ],
    [
      ev("user.sessions_revoke", { revoked_sessions: 2 }),
      "signed out Bob Builder everywhere, 2 sessions ended",
    ],
    [
      ev("user.sessions_revoke", { reason: "not_found" }, { outcome: "failure" }),
      "tried to sign out Bob Builder (no such user)",
    ],
    [
      ev("user.password_reset", { revoked_sessions: 0 }),
      "reset the password of Bob Builder, 0 sessions ended",
    ],
    [
      ev("user.password_reset", { reason: "self_reset" }, { outcome: "failure" }),
      "tried to reset the password of Bob Builder (own account)",
    ],
    [
      ev("authz.denied", { path: "/api/v1/users" }, { outcome: "denied" }),
      "was refused access to /api/v1/users",
    ],
    [ev("authz.denied", {}, { outcome: "denied" }), "was refused access"],
    [
      ev("authz.denied", { reason: "not_a_member" }, { outcome: "denied", case_id: "c" }),
      "was refused access (not a member)",
    ],
    [ev("audit.verify", { ok: true, checked: 5 }), "verified the audit chain (5 events, intact)"],
    [
      ev("audit.verify", { ok: false, checked: 5, first_bad_id: 3 }, { outcome: "failure" }),
      "verified the audit chain: broken at event #3",
    ],
    [ev("audit.verify", {}), "verified the audit chain"],
    [ev("case.graph_delete", {}), "removed a deleted case's graph"],
    [
      ev("case.graph_delete", {}, { outcome: "failure" }),
      "failed to remove a deleted case's graph",
    ],
    [ev("case.export", { format: "graphml" }), "exported the graph as GRAPHML"],
    [
      ev("case.export", { error: "too_large" }, { outcome: "failure" }),
      "tried to export the graph (too large)",
    ],
    [ev("entity.create", { type: "domain" }), "added an entity (domain)"],
    [ev("entity.delete", {}), "deleted an entity"],
    [ev("relationship.create", { type: "RESOLVES_TO" }), "added a relationship (RESOLVES_TO)"],
    [ev("job.create", { shuttle: "dns" }), "ran dns"],
    [
      ev("job.create", { reason: "too_many_jobs" }, { outcome: "failure" }),
      "tried to run a Shuttle (too many jobs)",
    ],
    [ev("view.create"), "saved a graph view"],
    [ev("view.delete"), "deleted a saved graph view"],
    [ev("job.cancel"), "cancelled a Shuttle job"],
    [ev("job.finish", { status: "succeeded" }), "finished a Shuttle job (succeeded)"],
    [ev("job.finish", {}), "finished a Shuttle job (success)"],
    [
      ev("result_cache.clear", { shuttle: "all", deleted: 3 }),
      "cleared the result cache (3 entries)",
    ],
    [
      ev("result_cache.clear", { shuttle: "crtsh", deleted: 1 }),
      "cleared the cached results of crtsh (1 entry)",
    ],
    [ev("result_cache.clear", {}), "cleared the result cache"],
    [ev("scan_profile.create", { name: "Mail check" }), "created the scan profile Mail check"],
    [ev("scan_profile.update", { name: "Mail" }), "changed the scan profile Mail"],
    [ev("scan_profile.delete", {}), "deleted the scan profile"],
    [
      ev("scan_run.create", { profile_name: "Exposure check", jobs: 4 }, { case_id: "c-1" }),
      "ran Exposure check (4 jobs)",
    ],
    [
      ev("scan_run.create", { profile_name: "Quick", jobs: 1, pivot: true }, { case_id: "c-1" }),
      "started a pivot with Quick (1 job)",
    ],
    [
      ev(
        "scan_run.create",
        { profile: "quick", reason: "auto_pivot_disabled" },
        { case_id: "c-1", outcome: "denied" },
      ),
      "tried to run a profile (auto-pivot is off for this case)",
    ],
    [ev("case.update", { fields: ["allow_auto_pivot"] }), "changed auto-pivot setting"],
    [ev("scan_run.cancel", {}, { case_id: "c-1" }), "cancelled a profile run"],
    [ev("ownership.create", { domain: "example.org" }), "added example.org to verify"],
    [ev("ownership.delete", { domain: "example.org" }), "removed the verified domain example.org"],
    [ev("ownership.check", { domain: "example.org" }), "verified example.org"],
    [
      ev("ownership.check", { domain: "example.org" }, { outcome: "failure" }),
      "did not find the proof for example.org",
    ],
    [ev("ownership.revoke", { domain: "example.org" }), "revoked example.org: its proof is gone"],
    [
      ev("ownership.expire", { domain: "example.org" }),
      "let the verification of example.org expire",
    ],
    [
      ev("ct_watch.create", { domain: "example.org" }),
      "started watching example.org for new certificates",
    ],
    [
      ev("ct_watch.delete", { domain: "example.org" }),
      "stopped watching example.org for new certificates",
    ],
    [
      ev("ct_alert.create", { domain: "example.org", certificates: 2, names: 1 }),
      "found 2 new certificates and 1 new name for example.org",
    ],
    [ev("ct_alert.acknowledge", { alerts: 3 }), "marked 3 certificate alerts seen"],
    [
      ev("result_cache.clear", { shuttle: "all", deleted: 2 }, { case_id: "c-1" }),
      "cleared the case's cached Shuttle results",
    ],
    [ev("case.status_change", { to: "closed" }), "closed the case"],
    [ev("something.new"), "something.new"],
  ])("%#: %s", (event, expected) => {
    expect(describeAuditEvent(event, [BOB])).toBe(expected);
  });

  it("names the actor, or Someone for an anonymous attempt", () => {
    expect(auditActor(ev("auth.login"))).toBe("admin@example.org");
    expect(auditActor(ev("auth.login", {}, { actor_label: null }))).toBe("Someone");
  });

  it("labels every action", () => {
    for (const action of AUDIT_ACTIONS) expect(ACTION_LABELS[action]).toMatch(/\w/);
  });
});

describe("toAuditQuery", () => {
  it("maps empty filters to no parameters", () => {
    expect(toAuditQuery(NO_FILTERS)).toEqual({
      error: null,
      query: {
        action: null,
        outcome: null,
        actorUserId: null,
        caseId: null,
        since: null,
        until: null,
      },
    });
  });

  it("turns whole UTC days into an inclusive start and exclusive end", () => {
    const result = toAuditQuery({ ...NO_FILTERS, from: "2026-12-31", to: "2026-12-31" });
    expect(result.query).toMatchObject({
      since: "2026-12-31T00:00:00.000Z",
      until: "2027-01-01T00:00:00.000Z",
    });
  });

  it("refuses partial case IDs and reversed ranges", () => {
    expect(toAuditQuery({ ...NO_FILTERS, caseId: "abc" })).toEqual({
      query: null,
      error: CASE_ID_INVALID,
    });
    expect(toAuditQuery({ ...NO_FILTERS, from: "2026-02-02", to: "2026-02-01" })).toEqual({
      query: null,
      error: DATE_RANGE_INVALID,
    });
  });
});

describe("verifyMessage", () => {
  it("covers intact, broken and unknown reasons", () => {
    expect(verifyMessage({ ok: true, checked: 1, first_bad_id: null, reason: null })).toBe(
      "Chain intact: 1 event checked.",
    );
    expect(
      verifyMessage({
        ok: false,
        checked: 9,
        first_bad_id: 4,
        reason: "prev_hash does not match the previous event's hash",
      }),
    ).toBe("Chain broken at event #4 (an event is missing or out of order): 9 events checked.");
    expect(verifyMessage({ ok: false, checked: 2, first_bad_id: null, reason: "odd" })).toBe(
      "Chain broken at event #? (odd): 2 events checked.",
    );
    expect(verifyMessage({ ok: false, checked: 2, first_bad_id: 1, reason: null })).toBe(
      "Chain broken at event #1: 2 events checked.",
    );
  });
});

describe("matchesUser", () => {
  it("searches name and email and filters role and status", () => {
    expect(matchesUser(BOB, " bob ", "", "")).toBe(true);
    expect(matchesUser(BOB, "ANALYST@", "", "")).toBe(true);
    expect(matchesUser(BOB, "zed", "", "")).toBe(false);
    expect(matchesUser(BOB, "", "analyst", "active")).toBe(true);
    expect(matchesUser(BOB, "", "admin", "")).toBe(false);
    expect(matchesUser(BOB, "", "", "inactive")).toBe(false);
    expect(matchesUser({ ...BOB, is_active: false }, "", "", "active")).toBe(false);
  });
});
