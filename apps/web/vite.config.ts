/// <reference types="vitest/config" />
import { readFileSync, writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// Shown in the account menu (src/version.ts). Read from package.json, which
// scripts/bump-version.py keeps equal to every Python package's version.
const { version } = JSON.parse(readFileSync(new URL("package.json", import.meta.url), "utf8")) as {
  version: string;
};

// The API dev server (`make api`) or the Compose stack (`make up`) listens here.
// Override when API_HOST_PORT is changed in .env.
const apiProxyTarget = process.env.NIGHTLOOM_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

/**
 * Writes bundle-report.json after a build: the gzip size of what the sign-in
 * page loads first (the entry, everything it imports statically, and the
 * CSS) and whether three.js is in it, of the sign-in loom loaded on top of it
 * when the browser is idle, of what the 2D graph loads (the entry, the
 * GraphView chunk and everything they import statically, and the CSS) and of
 * the lazily loaded 3D view on top of it. scripts/check-bundle-size.mjs
 * compares it with bundle-budget.json (CI).
 */
function bundleReport(): Plugin {
  return {
    name: "nightloom-bundle-report",
    apply: "build",
    generateBundle(_options, bundle) {
      const files = Object.values(bundle);
      const chunks = files.flatMap((file) => (file.type === "chunk" ? [file] : []));
      const byName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
      const closure = (start: readonly string[]): Set<string> => {
        const seen = new Set<string>();
        const queue = [...start];
        while (queue.length > 0) {
          const name = queue.pop() ?? "";
          if (seen.has(name) || !byName.has(name)) continue;
          seen.add(name);
          queue.push(...(byName.get(name)?.imports ?? []));
        }
        return seen;
      };
      const chunkOf = (path: string) =>
        chunks.find((chunk) => chunk.moduleIds.some((id) => id.endsWith(path)))?.fileName ?? "";
      const gzip = (text: string | Uint8Array) => gzipSync(text, { level: 9 }).length;
      const sizeOf = (names: Iterable<string>) => {
        const sizes: Record<string, number> = {};
        for (const name of names) sizes[name] = gzip(byName.get(name)?.code ?? "");
        return sizes;
      };
      const entries = chunks.filter((chunk) => chunk.isEntry).map((chunk) => chunk.fileName);
      const signin = closure(entries);
      const threeModule = /node_modules[\\/](three|@react-three)[\\/]/;
      const threeIn = (names: Iterable<string>) =>
        [...names].filter((name) =>
          (byName.get(name)?.moduleIds ?? []).some((id) => threeModule.test(id)),
        );
      const loom = new Set(
        [...closure([chunkOf("/src/auth/loom/loomScene.ts")])].filter((name) => !signin.has(name)),
      );
      const graph2d = closure([...entries, chunkOf("/src/graph/GraphView.tsx")]);
      const graph3d = new Set(
        [
          ...closure([
            chunkOf("/src/graph/three/GraphCanvas3D.tsx"),
            chunkOf("/src/graph/three/keyboard3d.ts"),
          ]),
        ].filter((name) => !graph2d.has(name)),
      );
      const css: Record<string, number> = {};
      for (const file of files) {
        if (file.type === "asset" && file.fileName.endsWith(".css")) {
          css[file.fileName] = gzip(file.source);
        }
      }
      const sum = (sizes: Record<string, number>) =>
        Object.values(sizes).reduce((total, size) => total + size, 0);
      const filesSignin = { ...sizeOf(signin), ...css };
      const filesLoom = sizeOf(loom);
      const files2d = { ...sizeOf(graph2d), ...css };
      const files3d = sizeOf(graph3d);
      const report = {
        signin: { gzip: sum(filesSignin), files: filesSignin, threeChunks: threeIn(signin) },
        loom: { gzip: sum(filesLoom), files: filesLoom, threeChunks: threeIn(loom) },
        graph2d: { gzip: sum(files2d), files: files2d },
        graph3d: { gzip: sum(files3d), files: files3d },
      };
      writeFileSync(
        new URL("bundle-report.json", import.meta.url),
        `${JSON.stringify(report, null, 2)}\n`,
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), bundleReport()],
  define: {
    __NIGHTLOOM_VERSION__: JSON.stringify(version),
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: apiProxyTarget, changeOrigin: true },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/api": { target: apiProxyTarget, changeOrigin: true },
    },
  },
  build: {
    target: "es2023",
    sourcemap: true,
    // sigma + graphology are most of the bundle; keep them in their own chunk
    // so app changes do not invalidate the cached graph libraries.
    rolldownOptions: {
      output: {
        codeSplitting: {
          // A group takes only the modules it names: by default it would also
          // take what they import (React, for three-vendor), and the 2D graph
          // would load the 3D view's vendor chunk.
          includeDependenciesRecursively: false,
          groups: [
            { name: "graph-vendor", test: /node_modules[\\/](sigma|graphology|events[\\/])/ },
            // three.js and React Three Fiber with the libraries R3F runs on: only
            // the lazily loaded 3D view and the sign-in loom use them. R3F's own
            // dependencies belong here too; elsewhere they would land in the 3D
            // view's chunk, and the loom would load the whole graph view.
            {
              name: "three-vendor",
              test: /node_modules[\\/](three|@react-three|zustand|suspend-react|its-fine|use-sync-external-store)[\\/]/,
            },
          ],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // Playwright's end-to-end specs live in e2e/ and run with `pnpm e2e`.
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test/setup.ts"],
    // Without this, Vitest turns stylesheets into empty modules, `?raw` ones
    // included. src/theme/tokens.ts and the token tests read their text.
    css: { include: [/src\/styles\/.+\.css/] },
    // Above Testing Library's 4 s async limit (src/test/setup.ts), so a slow
    // findBy* fails with its own message rather than a test timeout.
    testTimeout: 15_000,
    restoreMocks: true,
    unstubGlobals: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx", "src/test/**", "src/**/*.test.{ts,tsx}", "src/vite-env.d.ts"],
      reporter: ["text", "html", "lcov"],
      thresholds: {
        lines: 80,
        statements: 80,
        functions: 80,
      },
    },
  },
});
