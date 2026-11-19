import { describe, expect, it } from "vitest";

import { ENTITY_TYPES } from "../domain/entities";
import { buildGraph, recolorGraph } from "../graph/buildGraph";
import { GRAPH_PALETTES, graphPalette } from "./graphPalette";
import { parseTokens, token } from "./tokens";

const prov = {
  source: "test",
  first_seen: "2026-01-01T00:00:00Z",
  last_seen: "2026-01-02T00:00:00Z",
  confidence: 1,
};

describe("parseTokens", () => {
  it("merges :root into each theme and resolves var() references", () => {
    const tokens = parseTokens(`
      /* a comment { with braces } */
      :root { --a: var(--b); --size: 4px; }
      :root[data-theme="dark"] { --b: #000000; }
      :root[data-theme="light"] { --b: #ffffff; }
      .other { --ignored: red; }
    `);
    expect(tokens.dark).toEqual({ "--a": "#000000", "--b": "#000000", "--size": "4px" });
    expect(tokens.light["--a"]).toBe("#ffffff");
  });

  it("rejects unknown and circular references", () => {
    expect(() => parseTokens(':root { --a: var(--nope); } :root[data-theme="dark"] {}')).toThrow(
      /Unknown design token --nope/,
    );
    expect(() => parseTokens(":root { --a: var(--b); --b: var(--a); }")).toThrow(/Circular/);
  });

  it("knows every token of tokens.css", () => {
    expect(token("dark", "--bg")).toBe("#12131a");
    expect(token("light", "--bg")).toBe("#f4f0e6");
    expect(() => token("dark", "--nope")).toThrow(/Unknown design token/);
  });
});

describe("graphPalette", () => {
  it("reads the canvas colors from the tokens", () => {
    for (const theme of ["dark", "light"] as const) {
      const palette = graphPalette(theme);
      expect(palette).toMatchObject({
        background: token(theme, "--bg"),
        label: token(theme, "--muted"),
        edge: token(theme, "--hair"),
        selection: token(theme, "--brass"),
      });
      expect(palette.types.ip_address).toBe(token(theme, "--type-ip-address"));
      // Types without a color of their own fall back to --muted.
      expect(palette.types.email).toBe(token(theme, "--muted"));
      for (const type of ENTITY_TYPES) expect(palette.types[type]).toMatch(/^#[0-9a-f]{6}$/);
    }
  });
});

describe("recolorGraph", () => {
  it("repaints nodes and edges for another theme without moving anything", () => {
    const graph = buildGraph(
      {
        nodes: [
          { id: "d", label: "example.org", type: "domain", ...prov },
          { id: "i", label: "192.0.2.1", type: "ip_address", ...prov },
        ],
        edges: [{ id: "e", from: "d", to: "i", relation: "resolves_to", ...prov }],
      },
      { layoutIterations: 5 },
    );
    const before = graph.getNodeAttributes("d");
    expect(before.color).toBe(GRAPH_PALETTES.dark.types.domain);

    recolorGraph(graph, GRAPH_PALETTES.light);
    const after = graph.getNodeAttributes("d");
    expect(after.color).toBe(GRAPH_PALETTES.light.types.domain);
    expect(graph.getNodeAttribute("i", "color")).toBe(GRAPH_PALETTES.light.types.ip_address);
    expect(graph.getEdgeAttribute("e", "color")).toBe(GRAPH_PALETTES.light.edge);
    expect([after.x, after.y, after.size]).toEqual([before.x, before.y, before.size]);

    // Repainting with the same palette changes nothing.
    recolorGraph(graph, GRAPH_PALETTES.light);
    expect(graph.getNodeAttributes("d")).toBe(after);
  });
});
