import type { AuditEvent, Case, CaseRole, Member } from "../api/cases";
import type { User } from "../api/client";
import { json, READY_BODY, TEST_USER, type FakeApi } from "./fakeApi";

export const CASE_ID = "5f0c8a52-6a0e-4c1e-9d6c-0b8f6e9c2a11";

export function makeCase(overrides: Partial<Case> = {}): Case {
  return {
    id: CASE_ID,
    title: "Example.org exposure review",
    description: null,
    purpose: "Map the public attack surface of example.org for the owner.",
    legal_basis: "owned_assets",
    legal_basis_note: null,
    ethics_ack_version: "2026-11",
    ethics_ack_at: "2026-11-10T10:00:00Z",
    allow_person_entities: false,
    status: "open",
    retention_days: null,
    created_by: TEST_USER.id,
    created_at: "2026-11-10T10:00:00Z",
    updated_at: "2026-11-10T10:00:00Z",
    closed_at: null,
    my_role: "owner",
    ...overrides,
  };
}

export function makeMember(user: Pick<User, "id" | "email" | "display_name">, role: CaseRole) {
  return {
    user_id: user.id,
    email: user.email,
    display_name: user.display_name,
    global_role: "analyst",
    is_active: true,
    role,
    added_by: null,
    added_at: "2026-11-10T10:00:00Z",
  } satisfies Member;
}

export const OTHER_USER = {
  id: "9d1f7e2a-3b4c-4d5e-8f60-718293a4b5c6",
  email: "bob@example.org",
  display_name: "Bob Builder",
};

export function makeEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: 1,
    occurred_at: "2026-11-10T10:00:00Z",
    actor_user_id: TEST_USER.id,
    actor_label: TEST_USER.email,
    actor_ip: "127.0.0.1",
    request_id: "req-00000001",
    action: "case.create",
    outcome: "success",
    target_type: "case",
    target_id: CASE_ID,
    case_id: CASE_ID,
    details: {},
    ...overrides,
  };
}

export const ADMIN_USER: User = { ...TEST_USER, role: "admin" };
export const VIEWER_USER: User = { ...TEST_USER, role: "viewer" };

export const EMPTY_PAGE = { items: [], next_cursor: null };

/** Registers a signed-in `user` and one case where they have `role`. */
export function serveCase(
  api: FakeApi,
  {
    user = TEST_USER,
    role = "owner",
    overrides = {},
    members,
    events = [],
  }: {
    user?: User;
    role?: CaseRole;
    overrides?: Partial<Case>;
    members?: Member[];
    events?: AuditEvent[];
  } = {},
): void {
  const path = `/cases/${CASE_ID}`;
  api
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(user))
    .on("GET", path, () => json(makeCase({ my_role: role, ...overrides })))
    .on("GET", `${path}/members`, () => json(members ?? [makeMember(user, role)]))
    .on("GET", `${path}/audit?limit=50`, () => json({ items: events, next_before_id: null }))
    .on("GET", "/cases?limit=25", () => json(EMPTY_PAGE));
}
