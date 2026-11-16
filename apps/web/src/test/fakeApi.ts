/**
 * A stand-in for `fetch` that routes "METHOD /api/v1/path" to handlers and
 * records every call, so tests can check the headers that were sent.
 */
import { vi } from "vitest";

import type { User } from "../api/client";

export type Handler = (init: RequestInit) => Response | Promise<Response>;

export interface FakeCall {
  method: string;
  url: string;
  init: RequestInit;
  headers: Record<string, string>;
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export function noContent(): Response {
  return new Response(null, { status: 204 });
}

export const TEST_USER: User = {
  id: "0b6f3c1e-1f53-4d4b-9a0e-6d1f0f1a2b3c",
  email: "analyst@example.org",
  display_name: "Ada Analyst",
  role: "analyst",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  last_login_at: null,
};

export const READY_BODY = { status: "ready", checks: {} };

export class FakeApi {
  readonly calls: FakeCall[] = [];
  private readonly routes = new Map<string, Handler>();
  readonly fetch = vi.fn((input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init.method ?? "GET").toUpperCase();
    this.calls.push({
      method,
      url,
      init,
      headers: { ...(init.headers as Record<string, string> | undefined) },
    });
    const handler = this.routes.get(`${method} ${url}`);
    if (!handler) return Promise.resolve(json({ detail: "Not Found" }, 404));
    try {
      return Promise.resolve(handler(init));
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new Error(String(error)));
    }
  });

  /** `path` is relative to /api/v1. */
  on(method: string, path: string, handler: Handler): this {
    this.routes.set(`${method} /api/v1${path}`, handler);
    return this;
  }

  callsTo(method: string, path: string): FakeCall[] {
    return this.calls.filter((c) => c.method === method && c.url === `/api/v1${path}`);
  }

  /** Installs itself as the global fetch (undone by `unstubGlobals`). */
  install(): this {
    vi.stubGlobal("fetch", this.fetch);
    return this;
  }
}

/** Removes every cookie jsdom holds for the test document. */
export function clearCookies(): void {
  for (const part of document.cookie.split(";")) {
    const name = part.split("=")[0]?.trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  }
}
