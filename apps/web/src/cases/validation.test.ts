import { describe, expect, it } from "vitest";

import {
  counterText,
  EMPTY_CASE_FORM,
  length,
  toCaseCreate,
  validateCaseForm,
  validateChangeReason,
  validateNote,
  validatePersonReason,
  validateRetention,
  validateTitle,
  type CaseFormValues,
} from "./validation";

const valid: CaseFormValues = {
  ...EMPTY_CASE_FORM,
  title: "Example review",
  purpose: "x".repeat(30),
  legalBasis: "owned_assets",
  ethicsAcknowledged: true,
};

describe("validateCaseForm", () => {
  it("accepts a minimal valid form", () => {
    expect(validateCaseForm(valid)).toEqual({});
  });

  it("reports every missing required field on an empty form", () => {
    expect(Object.keys(validateCaseForm(EMPTY_CASE_FORM)).sort()).toEqual([
      "ethics_acknowledged",
      "legal_basis",
      "purpose",
      "title",
    ]);
  });

  it.each([
    [{ title: "ab" }, "title", "Title must be at least 3 characters."],
    [{ title: "  ab  " }, "title", "Title must be at least 3 characters."],
    [{ title: "x".repeat(201) }, "title", "Title must be at most 200 characters."],
    [
      { description: "x".repeat(5001) },
      "description",
      "Description must be at most 5000 characters.",
    ],
    [{ purpose: "x".repeat(29) }, "purpose", "Purpose must be at least 30 characters."],
    [{ purpose: "x".repeat(2001) }, "purpose", "Purpose must be at most 2000 characters."],
    [{ legalBasis: "" }, "legal_basis", "Choose a legal basis."],
    [
      { legalBasis: "authorized_engagement" },
      "legal_basis_note",
      "Name the engagement and who authorized it.",
    ],
    [
      { legalBasis: "other", legalBasisNote: "   " },
      "legal_basis_note",
      "Explain the legal basis in the note.",
    ],
    [
      { legalBasisNote: "x".repeat(1001) },
      "legal_basis_note",
      "The note must be at most 1000 characters.",
    ],
    [
      { allowPersonEntities: true, personEntitiesReason: "x".repeat(29) },
      "person_entities_reason",
      "Explain why in at least 30 characters.",
    ],
    [
      { allowPersonEntities: true, personEntitiesReason: "x".repeat(501) },
      "person_entities_reason",
      "The reason must be at most 500 characters.",
    ],
    [{ retentionDays: "0" }, "retention_days", "Retention must be between 1 and 3650 days."],
    [{ retentionDays: "3651" }, "retention_days", "Retention must be between 1 and 3650 days."],
    [{ retentionDays: "1.5" }, "retention_days", "Enter a whole number of days."],
    [
      { ethicsAcknowledged: false },
      "ethics_acknowledged",
      "Confirm that this investigation follows the ethics policy.",
    ],
  ] as const)("%j → %s", (change, field, message) => {
    expect(validateCaseForm({ ...valid, ...change })).toEqual({ [field]: message });
  });

  it("counts code points, like the server", () => {
    expect(length(" 🦉🦉🦉 ")).toBe(3);
    expect(validateTitle("🦉🦉🦉")).toBeUndefined();
  });

  it("ignores the person reason while person entities are off", () => {
    expect(validatePersonReason(false, "")).toBeUndefined();
  });

  it("accepts the bounds", () => {
    expect(validateRetention("1")).toBeUndefined();
    expect(validateRetention(" 3650 ")).toBeUndefined();
    expect(validateRetention("")).toBeUndefined();
    expect(validateNote("journalism", "")).toBeUndefined();
    expect(validateNote("other", "Court order 12/2026")).toBeUndefined();
  });
});

describe("validateChangeReason", () => {
  it.each([
    ["", "Give a reason of at least 10 characters."],
    ["  ten chars! ", undefined],
    ["123456789", "Give a reason of at least 10 characters."],
    ["x".repeat(501), "The reason must be at most 500 characters."],
  ])("%j", (reason, message) => {
    expect(validateChangeReason(reason)).toBe(message);
  });
});

describe("toCaseCreate", () => {
  it("trims and turns blanks into nulls", () => {
    expect(
      toCaseCreate({
        ...valid,
        title: "  T  ",
        description: "  ",
        legalBasisNote: " ",
        retentionDays: " 30 ",
      }),
    ).toEqual({
      title: "T",
      description: null,
      purpose: "x".repeat(30),
      legal_basis: "owned_assets",
      legal_basis_note: null,
      ethics_acknowledged: true,
      allow_person_entities: false,
      person_entities_reason: null,
      retention_days: 30,
    });
  });

  it("sends the person reason only when person entities are on", () => {
    expect(
      toCaseCreate({ ...valid, allowPersonEntities: true, personEntitiesReason: " why " }),
    ).toMatchObject({ allow_person_entities: true, person_entities_reason: "why" });
  });

  it("refuses a form without a legal basis", () => {
    expect(() => toCaseCreate({ ...valid, legalBasis: "" })).toThrow();
  });
});

describe("counterText", () => {
  it("mentions the minimum until it is reached", () => {
    expect(counterText(3, 2000, 30)).toBe("3 / 2000 characters (at least 30)");
    expect(counterText(30, 2000, 30)).toBe("30 / 2000 characters");
    expect(counterText(3, 500)).toBe("3 / 500 characters");
  });
});
