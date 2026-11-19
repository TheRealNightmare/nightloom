/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Shown in the account menu (src/version.ts). Read from package.json, which
// scripts/bump-version.py keeps equal to every Python package's version.
const { version } = JSON.parse(readFileSync(new URL("package.json", import.meta.url), "utf8")) as {
  version: string;
};

// The API dev server (`make api`) or the Compose stack (`make up`) listens here.
// Override when API_HOST_PORT is changed in .env.
const apiProxyTarget = process.env.NIGHTLOOM_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
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
          groups: [{ name: "graph-vendor", test: /node_modules[\\/](sigma|graphology)/ }],
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
