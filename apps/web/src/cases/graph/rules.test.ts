import { describe, expect, it } from "vitest";

import { SCHEMA } from "../../test/caseFixtures";
import {
  describeAdmiralty,
  entityTypeInfo,
  isTypeAllowed,
  relationsFor,
  typesBySensitivity,
} from "./rules";

const names = (items: { type: string }[]) => items.map((item) => item.type);

describe("schema-driven relationship filtering", () => {
  it("offers only the types whose allow-list has the pair", () => {
    expect(names(relationsFor(SCHEMA, "domain", "domain"))).toEqual([
      "HAS_SUBDOMAIN",
      "HAS_MX",
      "HAS_NS",
      "ALIAS_OF",
      "RELATED_TO",
    ]);
    expect(names(relationsFor(SCHEMA, "domain", "ip_address"))).toEqual([
      "RESOLVES_TO",
      "RELATED_TO",
    ]);
    expect(names(relationsFor(SCHEMA, "organization", "asn"))).toEqual(["OWNS", "RELATED_TO"]);
    expect(names(relationsFor(SCHEMA, "document", "person"))).toEqual(["MENTIONS", "RELATED_TO"]);
  });

  it("is directional", () => {
    // An address names a host by reverse DNS (HAS_PTR), never RESOLVES_TO.
    expect(names(relationsFor(SCHEMA, "ip_address", "domain"))).toEqual(["HAS_PTR", "RELATED_TO"]);
  });

  it("knows which types need a note", () => {
    const related = relationsFor(SCHEMA, "url", "breach");
    expect(related.map((r) => [r.type, r.note_required])).toEqual([["RELATED_TO", true]]);
  });
});

describe("entity types", () => {
  it("groups types by sensitivity in schema order", () => {
    const groups = typesBySensitivity(SCHEMA);
    expect(groups.map((g) => g.label)).toEqual([
      "Infrastructure",
      "Organization",
      "Person-related",
    ]);
    expect(names(groups[0]?.types ?? []).slice(0, 3)).toEqual(["domain", "ip_address", "netblock"]);
    expect(names(groups[2]?.types ?? [])).toContain("email");
    expect(groups.flatMap((g) => g.types)).toHaveLength(SCHEMA.entity_types.length);
  });

  it("gates person-related types on the case", () => {
    expect(isTypeAllowed(SCHEMA, "email", false)).toBe(false);
    expect(isTypeAllowed(SCHEMA, "image", false)).toBe(false);
    expect(isTypeAllowed(SCHEMA, "email", true)).toBe(true);
    expect(isTypeAllowed(SCHEMA, "organization", false)).toBe(true);
    expect(isTypeAllowed(SCHEMA, "domain", false)).toBe(true);
  });

  it("has documentation-safe examples", () => {
    expect(entityTypeInfo(SCHEMA, "ip_address")?.example).toBe("192.0.2.1");
    expect(entityTypeInfo(SCHEMA, "domain")?.example).toMatch(/example\.org$/);
  });
});

describe("Admiralty ratings", () => {
  it("explains both grades", () => {
    expect(describeAdmiralty(SCHEMA, "B2")).toEqual({
      reliability: "B: Usually reliable",
      credibility: "2: Probably true",
    });
    expect(describeAdmiralty(SCHEMA, "F6")?.reliability).toBe("F: Reliability cannot be judged");
  });

  it("returns null for anything else", () => {
    expect(describeAdmiralty(SCHEMA, "G7")).toBeNull();
    expect(describeAdmiralty(SCHEMA, "")).toBeNull();
    expect(
      describeAdmiralty({ ...SCHEMA, admiralty: { reliability: [], credibility: [] } }, "B2"),
    ).toBeNull();
  });
});
