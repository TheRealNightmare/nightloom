import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { EntityNode } from "../../domain/entities";
import { OrganizationRecordLinks } from "./OrganizationRecordLinks";
import { leiRecordUrl, wikidataItemUrl } from "./organizationRecords";

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

describe("record URLs", () => {
  it("links an LEI to GLEIF and an item to Wikidata", () => {
    expect(leiRecordUrl(LEI)).toBe(`https://search.gleif.org/#/record/${LEI}`);
    expect(wikidataItemUrl("Q95")).toBe("https://www.wikidata.org/wiki/Q95");
  });

  it("refuses anything else", () => {
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
    for (const value of [undefined, 95, "", "Q0", "P31", "Q95/../x", "q95"]) {
      expect(wikidataItemUrl(value)).toBeUndefined();
    }
  });
});

describe("OrganizationRecordLinks", () => {
  it("shows the links an organization has", () => {
    render(
      <OrganizationRecordLinks entity={node("organization", { lei: LEI, wikidata_id: "Q95" })} />,
    );
    const gleif = screen.getByRole("link", { name: "LEI record at GLEIF" });
    expect(gleif).toHaveAttribute("href", `https://search.gleif.org/#/record/${LEI}`);
    expect(gleif).toHaveAttribute("rel", "noopener noreferrer");
    const wikidata = screen.getByRole("link", { name: "Wikidata item" });
    expect(wikidata).toHaveAttribute("href", "https://www.wikidata.org/wiki/Q95");
  });

  it("shows only the links it can", () => {
    render(<OrganizationRecordLinks entity={node("organization", { wikidata_id: "Q95" })} />);
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("shows nothing without records or for another type", () => {
    const { container: none } = render(
      <OrganizationRecordLinks entity={node("organization", {})} />,
    );
    expect(none).toBeEmptyDOMElement();
    const { container: domain } = render(
      <OrganizationRecordLinks entity={node("domain", { lei: LEI })} />,
    );
    expect(domain).toBeEmptyDOMElement();
  });
});
