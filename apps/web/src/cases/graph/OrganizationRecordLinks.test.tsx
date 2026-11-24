import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { EntityNode } from "../../domain/entities";
import { leiRecordUrl } from "./leiRecord";
import { LeiRecordLink } from "./LeiRecordLink";

const LEI = "5493000EXHOLD0000105";

function node(
  type: EntityNode["type"],
  properties: NonNullable<EntityNode["properties"]>,
): EntityNode {
  return {
    id: `${type}:example holdings limited`,
    label: "Example Holdings Limited",
    type,
    properties,
    source: "shuttle:gleif@1.0.0",
    first_seen: "2026-11-24T06:00:00Z",
    last_seen: "2026-11-24T06:00:00Z",
    confidence: 0.9,
  };
}

describe("leiRecordUrl", () => {
  it("links an LEI to GLEIF's search", () => {
    expect(leiRecordUrl(LEI)).toBe(`https://search.gleif.org/#/record/${LEI}`);
  });

  it("refuses anything that is not an LEI", () => {
    for (const value of [
      undefined,
      42,
      "",
      "5493000EXHOLD000010",
      "5493000EXHOLD00001AB",
      "x/../y",
    ]) {
      expect(leiRecordUrl(value)).toBeUndefined();
    }
  });
});

describe("LeiRecordLink", () => {
  it("shows the link for an organization with an LEI", () => {
    render(<LeiRecordLink entity={node("organization", { lei: LEI })} />);
    const link = screen.getByRole("link", { name: "Open the LEI record at GLEIF" });
    expect(link).toHaveAttribute("href", `https://search.gleif.org/#/record/${LEI}`);
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows nothing without an LEI or for another type", () => {
    const { container: none } = render(<LeiRecordLink entity={node("organization", {})} />);
    expect(none).toBeEmptyDOMElement();
    const { container: domain } = render(<LeiRecordLink entity={node("domain", { lei: LEI })} />);
    expect(domain).toBeEmptyDOMElement();
  });
});
