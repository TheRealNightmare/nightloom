import { describe, expect, it } from "vitest";

import { makeEvent, makeMember, OTHER_USER } from "../test/caseFixtures";
import { TEST_USER } from "../test/fakeApi";
import { actorName, describeEvent, joinWords } from "./labels";

const members = [makeMember(OTHER_USER, "editor")];
const onBob = { target_type: "user", target_id: OTHER_USER.id };

describe("describeEvent", () => {
  it.each([
    ["case.create", "success", {}, {}, "created the case"],
    [
      "case.create",
      "success",
      { allow_person_entities: true, person_entities_reason: "Executives' public footprint" },
      {},
      "created the case with person entities allowed — reason: Executives' public footprint",
    ],
    ["case.update", "success", { fields: ["title"] }, {}, "changed title"],
    [
      "case.update",
      "success",
      { fields: ["legal_basis", "purpose"], change_reason: "Scope widened by client" },
      {},
      "changed legal basis and purpose — reason: Scope widened by client",
    ],
    [
      "case.update",
      "success",
      { fields: ["allow_person_entities", "legal_basis_note", "retention_days", "x"] },
      {},
      "changed person entities setting, legal basis note, retention period and x",
    ],
    ["case.update", "success", {}, {}, "changed the case"],
    ["case.status_change", "success", { from: "open", to: "closed" }, {}, "closed the case"],
    ["case.status_change", "success", { from: "closed", to: "open" }, {}, "reopened the case"],
    ["case.status_change", "success", { from: "open", to: "archived" }, {}, "archived the case"],
    ["case.status_change", "success", {}, {}, "changed the case's status"],
    ["case.delete", "success", { member_count: 1 }, {}, "deleted the case"],
    [
      "case.delete",
      "failure",
      { reason: "confirm_title_mismatch" },
      {},
      "tried to delete the case (the title did not match)",
    ],
    ["case.member_add", "success", { role: "editor" }, onBob, "added Bob Builder as editor"],
    [
      "case.member_add",
      "failure",
      { reason: "not_found", role: "viewer" },
      {},
      "tried to add a member (no such user or member)",
    ],
    [
      "case.member_update",
      "success",
      { from: "editor", to: "owner" },
      onBob,
      "changed Bob Builder's role from editor to owner",
    ],
    [
      "case.member_update",
      "failure",
      { reason: "last_owner" },
      {},
      "tried to change a member's role (the case must keep an owner)",
    ],
    ["case.member_remove", "success", { role: "viewer", self: true }, {}, "left the case"],
    [
      "case.member_remove",
      "success",
      { role: "viewer", self: false },
      { target_id: "gone" },
      "removed a former member (viewer)",
    ],
    ["case.member_remove", "failure", { reason: "something_new" }, {}, "tried to remove a member"],
    [
      "authz.denied",
      "denied",
      { reason: "missing_capability", required: "manage" },
      {},
      "was refused: needs permission to manage the case",
    ],
    [
      "authz.denied",
      "denied",
      { reason: "missing_capability", required: "future" },
      {},
      "was refused: needs permission to future",
    ],
    ["authz.denied", "denied", { reason: "missing_capability" }, {}, "was refused an action"],
    ["authz.denied", "denied", { reason: "not_a_member" }, {}, "was refused access (not a member)"],
    [
      "authz.denied",
      "denied",
      { reason: "not_view_creator" },
      {},
      "was refused: only a view's creator or an owner may change it",
    ],
    ["view.create", "success", {}, {}, "saved a graph view"],
    ["view.update", "success", {}, {}, "changed a saved graph view"],
    ["view.delete", "success", {}, {}, "deleted a saved graph view"],
    [
      "result_cache.clear",
      "success",
      { deleted: 2 },
      {},
      "cleared the case's cached Shuttle results",
    ],
    ["user.update", "success", {}, {}, "user.update"],
  ] as const)("%s (%s) %j", (action, outcome, details, extra, expected) => {
    expect(describeEvent(makeEvent({ action, outcome, details, ...extra }), members)).toBe(
      expected,
    );
  });

  it("describes a change to the actor's own role, with an unknown role", () => {
    const event = makeEvent({
      action: "case.member_update",
      details: { from: "owner", to: 7 },
      target_id: TEST_USER.id,
    });
    expect(describeEvent(event)).toBe("changed their own role from owner to an unknown role");
  });
});

describe("helpers", () => {
  it("joins words", () => {
    expect(joinWords([])).toBe("");
    expect(joinWords(["a"])).toBe("a");
    expect(joinWords(["a", "b", "c"])).toBe("a, b and c");
  });

  it("falls back for an actor without a label", () => {
    expect(actorName(makeEvent({ actor_label: null }))).toBe("Someone");
    expect(actorName(makeEvent())).toBe(TEST_USER.email);
  });
});
