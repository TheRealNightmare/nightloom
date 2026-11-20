/** The web app manifest and the icons it and index.html name (public/). */
import { describe, expect, it } from "vitest";

import indexHtml from "../../index.html?raw";
import manifestText from "../../public/manifest.webmanifest?raw";

const PUBLIC_FILES = Object.keys(
  import.meta.glob(["../../public/**/*.{png,svg}"], { query: "?url", eager: true }),
).map((path) => path.replace("../../public", ""));

interface Manifest {
  name: string;
  start_url: string;
  scope: string;
  display: string;
  icons: { src: string; sizes: string; type: string; purpose?: string }[];
}

const manifest = JSON.parse(manifestText) as Manifest;

describe("the web app manifest", () => {
  it("describes Nightloom on this origin", () => {
    expect(manifest.name).toBe("Nightloom");
    expect(manifest.start_url).toBe("/");
    expect(manifest.scope).toBe("/");
    expect(manifest.display).toBe("standalone");
  });

  it("names icons that exist, same-origin, with a maskable one", () => {
    for (const icon of manifest.icons) {
      expect(icon.src.startsWith("/")).toBe(true);
      expect(PUBLIC_FILES).toContain(icon.src);
    }
    expect(manifest.icons.some((icon) => icon.purpose === "maskable")).toBe(true);
    expect(manifest.icons.map((icon) => icon.sizes)).toEqual(
      expect.arrayContaining(["192x192", "512x512"]),
    );
  });

  it("is linked from index.html with the favicons", () => {
    const links = [...indexHtml.matchAll(/<link [^>]*href="([^"]+)"/g)].map((match) => match[1]);
    expect(links).toContain("/manifest.webmanifest");
    for (const href of links.filter((link) => link !== "/manifest.webmanifest")) {
      expect(PUBLIC_FILES).toContain(href);
    }
  });
});
