import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CASE_ID, makeCase, makeEvent, makeMember, OTHER_USER } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent, TEST_USER } from "../test/fakeApi";
import {
  can,
  canCreateCases,
  createCase,
  deleteCase,
  fieldOf,
  getCase,
  groupMessages,
  listCaseAudit,
  listCases,
  listMembers,
  putMember,
  removeMember,
  updateCase,
} from "./cases";
import {
  ConflictError,
  CSRF_HEADER,
  ForbiddenError,
  NotFoundError,
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

const casePath = `/cases/${CASE_ID}`;

describe("capabilities", () => {
  it.each([
    ["viewer", ["read"]],
    ["editor", ["read", "edit", "run_shuttles", "read_audit"]],
    ["owner", ["read", "edit", "run_shuttles", "read_audit", "manage"]],
  ] as const)("%s can %j and nothing else", (role, allowed) => {
    for (const capability of ["read", "edit", "run_shuttles", "read_audit", "manage"] as const) {
      expect(can(role, capability)).toBe((allowed as readonly string[]).includes(capability));
    }
  });

  it("only admins and analysts create cases", () => {
    expect(canCreateCases("admin")).toBe(true);
    expect(canCreateCases("analyst")).toBe(true);
    expect(canCreateCases("viewer")).toBe(false);
  });
});

describe("listCases", () => {
  it("sends filters and the cursor, and leaves out blank ones", async () => {
    api
      .on("GET", "/cases", () => json({ items: [makeCase()], next_cursor: "abc" }))
      .on("GET", "/cases?cursor=abc&limit=10&status=closed&q=exa+mple", () =>
        json({ items: [], next_cursor: null }),
      );
    expect(await listCases({ q: "   " })).toEqual({ items: [makeCase()], next_cursor: "abc" });
    expect(
      await listCases({ cursor: "abc", limit: 10, status: "closed", q: " exa mple " }),
    ).toEqual({ items: [], next_cursor: null });
  });

  it("rejects a malformed page", async () => {
    api.on("GET", "/cases", () => json({ items: [{ id: 1 }], next_cursor: null }));
    await expect(listCases()).rejects.toBeInstanceOf(UnexpectedResponseError);
  });
});

describe("single case calls", () => {
  it("gets a case", async () => {
    api.on("GET", casePath, () => json(makeCase()));
    expect(await getCase(CASE_ID)).toMatchObject({ id: CASE_ID, my_role: "owner" });
  });

  it("creates with POST and the CSRF header", async () => {
    api.on("POST", "/cases", () => json(makeCase(), 201));
    await createCase({
      title: "T",
      purpose: "p",
      legal_basis: "journalism",
      ethics_acknowledged: true,
    });
    const call = api.callsTo("POST", "/cases")[0]!;
    expect(call.headers[CSRF_HEADER]).toBe("csrf-token");
    expect(JSON.parse(call.init.body as string)).toMatchObject({ legal_basis: "journalism" });
  });

  it("updates with PATCH", async () => {
    api.on("PATCH", casePath, () => json(makeCase({ title: "New" })));
    expect((await updateCase(CASE_ID, { title: "New" })).title).toBe("New");
    expect(api.callsTo("PATCH", casePath)[0]?.headers[CSRF_HEADER]).toBe("csrf-token");
  });

  it("deletes with the confirmation title", async () => {
    api.on("DELETE", casePath, () => noContent());
    await deleteCase(CASE_ID, "Exact Title");
    expect(JSON.parse(api.callsTo("DELETE", casePath)[0]?.init.body as string)).toEqual({
      confirm_title: "Exact Title",
    });
  });
});

describe("members and audit", () => {
  it("lists, puts and removes members", async () => {
    const bob = makeMember(OTHER_USER, "editor");
    api
      .on("GET", `${casePath}/members`, () => json([makeMember(TEST_USER, "owner"), bob]))
      .on("PUT", `${casePath}/members`, () => json(bob, 201))
      .on("DELETE", `${casePath}/members/${OTHER_USER.id}`, () => noContent());
    expect(await listMembers(CASE_ID)).toHaveLength(2);
    expect(await putMember(CASE_ID, OTHER_USER.email, "editor")).toEqual(bob);
    expect(JSON.parse(api.callsTo("PUT", `${casePath}/members`)[0]?.init.body as string)).toEqual({
      email: OTHER_USER.email,
      role: "editor",
    });
    await removeMember(CASE_ID, OTHER_USER.id);
    expect(api.callsTo("DELETE", `${casePath}/members/${OTHER_USER.id}`)).toHaveLength(1);
  });

  it("pages the audit listing with before_id", async () => {
    api
      .on("GET", `${casePath}/audit?limit=2`, () =>
        json({ items: [makeEvent({ id: 9 }), makeEvent({ id: 8 })], next_before_id: 8 }),
      )
      .on("GET", `${casePath}/audit?before_id=8&limit=2`, () =>
        json({ items: [makeEvent({ id: 7 })], next_before_id: null }),
      );
    expect((await listCaseAudit(CASE_ID, { limit: 2 })).next_before_id).toBe(8);
    expect((await listCaseAudit(CASE_ID, { beforeId: 8, limit: 2 })).items[0]?.id).toBe(7);
  });
});

describe("errors", () => {
  it("maps 404 to NotFound, keeping a known detail", async () => {
    api
      .on("GET", casePath, () => json({ detail: "Case not found." }, 404))
      .on("PUT", `${casePath}/members`, () =>
        json({ detail: "No active user with that email." }, 404),
      );
    await expect(getCase(CASE_ID)).rejects.toBeInstanceOf(NotFoundError);
    await expect(putMember(CASE_ID, "x@example.org", "viewer")).rejects.toMatchObject({
      detail: "No active user with that email.",
    });
  });

  it("maps 403 to Forbidden without the server's text", async () => {
    api.on("PATCH", casePath, () => json({ detail: "internal" }, 403));
    const error = await updateCase(CASE_ID, { status: "closed" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ForbiddenError);
    expect((error as Error).message).not.toContain("internal");
  });

  it("maps 409 to Conflict with the message", async () => {
    api.on("DELETE", `${casePath}/members/${TEST_USER.id}`, () =>
      json({ detail: "A case must keep at least one owner." }, 409),
    );
    await expect(removeMember(CASE_ID, TEST_USER.id)).rejects.toEqual(
      new ConflictError("A case must keep at least one owner."),
    );
  });

  it("falls back to a fixed Conflict message without a detail", async () => {
    api.on("PATCH", casePath, () => json({}, 409));
    await expect(updateCase(CASE_ID, {})).rejects.toThrow(/conflicts/);
  });

  it("maps 422 to per-field messages, without pydantic's prefix", async () => {
    api.on("POST", "/cases", () =>
      json(
        {
          detail: [
            { loc: ["body", "title"], msg: "String should have at least 3 characters" },
            {
              loc: ["body"],
              msg: "Value error, You must acknowledge ETHICS.md (ethics_acknowledged: true).",
            },
          ],
        },
        422,
      ),
    );
    const error = (await createCase({
      title: "",
      purpose: "",
      legal_basis: "other",
      ethics_acknowledged: false,
    }).catch((e: unknown) => e)) as ValidationError;
    expect(error).toBeInstanceOf(ValidationError);
    expect(groupMessages(error.messages)).toEqual({
      title: "String should have at least 3 characters",
      ethics_acknowledged: "You must acknowledge ETHICS.md (ethics_acknowledged: true).",
    });
  });
});

describe("fieldOf", () => {
  it.each([
    ["legal_basis_note is required when legal_basis is …", "legal_basis_note"],
    ["person_entities_reason (at least 30 characters) is required …", "person_entities_reason"],
    ["change_reason (at least 10 characters) is required …", "change_reason"],
    ["Users with the global viewer role can only be case viewers.", null],
  ])("maps %s", (message, field) => {
    expect(fieldOf({ field: null, message })).toBe(field);
  });

  it("joins several messages for one field and collects the rest under _form", () => {
    expect(
      groupMessages([
        { field: "title", message: "A." },
        { field: "title", message: "B." },
        { field: null, message: "Something else." },
      ]),
    ).toEqual({ title: "A. B.", _form: "Something else." });
  });
});
