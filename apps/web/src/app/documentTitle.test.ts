import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { documentTitle, useDocumentTitle } from "./documentTitle";

describe("documentTitle", () => {
  it("joins the parts with the app name last, leaving out empty ones", () => {
    expect(documentTitle()).toBe("Nightloom");
    expect(documentTitle("Cases")).toBe("Cases · Nightloom");
    expect(documentTitle("Example case", null, " ", undefined, "Graph")).toBe(
      "Example case · Graph · Nightloom",
    );
  });

  it("sets document.title and follows changes", () => {
    const { rerender } = renderHook(
      ({ part }) => {
        useDocumentTitle(part);
      },
      { initialProps: { part: "Cases" } },
    );
    expect(document.title).toBe("Cases · Nightloom");
    rerender({ part: "Users" });
    expect(document.title).toBe("Users · Nightloom");
  });
});
