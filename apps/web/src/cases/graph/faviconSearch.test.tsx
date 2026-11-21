import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { EntityNode } from "../../domain/entities";
import { faviconSearches } from "./faviconSearch";
import { FaviconSearchLinks } from "./FaviconSearchLinks";

const SHA = "a".repeat(64);

function node(type: EntityNode["type"], label: string, sha256?: string): EntityNode {
  return {
    id: `${type}:${label}`,
    label,
    type,
    properties: sha256 ? { sha256 } : {},
    source: "shuttle:favicon@1.0.0",
    first_seen: "2026-11-22T12:00:00Z",
    last_seen: "2026-11-22T12:00:00Z",
    confidence: 0.95,
  };
}

describe("faviconSearches", () => {
  it("searches Shodan and FOFA by the hash", () => {
    const [shodan, fofa, ...rest] = faviconSearches("-1010568750");
    expect(shodan).toEqual({
      service: "Shodan",
      url: "https://www.shodan.io/search?query=http.favicon.hash%3A-1010568750",
    });
    expect(fofa?.service).toBe("FOFA");
    const query = new URL(fofa?.url ?? "").searchParams.get("qbase64") ?? "";
    expect(atob(query)).toBe('icon_hash="-1010568750"');
    expect(rest).toEqual([]);
  });

  it("searches Censys by the SHA-256 when it is known", () => {
    const censys = faviconSearches("1", SHA).at(-1);
    expect(censys?.service).toBe("Censys");
    const query = new URL(censys?.url ?? "").searchParams.get("q");
    expect(query).toContain(`host.services.endpoints.http.favicons.hash_sha256: "${SHA}"`);
    expect(query).toContain(`web.endpoints.http.favicons.hash_sha256: "${SHA}"`);
    // Anything that is not a SHA-256 is never put into a query.
    expect(faviconSearches("1", 'x" or 1').map((s) => s.service)).toEqual(["Shodan", "FOFA"]);
  });
});

describe("FaviconSearchLinks", () => {
  it("links to the searches in a new tab", () => {
    render(<FaviconSearchLinks entity={node("favicon", "-1010568750", SHA)} />);
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Search Shodan",
      "Search FOFA",
      "Search Censys",
    ]);
    for (const link of links) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  });

  it("shows nothing for other types", () => {
    const { container } = render(<FaviconSearchLinks entity={node("domain", "example.org")} />);
    expect(container).toBeEmptyDOMElement();
  });
});
