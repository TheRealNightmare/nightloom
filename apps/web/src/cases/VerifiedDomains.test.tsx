/** Settings → Verified domains: adding, the proof, Check now with polling, removing. */
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Verification } from "../api/ownership";
import { CASE_ID, makeVerification, serveCase } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { CHECK_POLL_MS } from "./VerifiedDomains";
import { VERIFIED_DOMAINS_HINT } from "./verificationText";

let api: FakeApi;
const path = `/cases/${CASE_ID}`;
const url = `${path}/verifications`;

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  vi.useRealTimers();
  clearCookies();
});

type ServeOptions = Parameters<typeof serveCase>[1];

async function openSettings(options: ServeOptions = {}) {
  serveCase(api, options);
  renderApp(`${path}/settings`);
  return screen.findByRole("region", { name: "Verified domains" });
}

const bodyOf = (method: string, route: string) =>
  JSON.parse(api.callsTo(method, route).at(-1)?.init.body as string) as unknown;

describe("verified domains", () => {
  it("explains itself and starts empty", async () => {
    const section = await openSettings();
    expect(within(section).getByText(VERIFIED_DOMAINS_HINT)).toBeVisible();
    expect(
      await within(section).findByText("No domains yet. Active Shuttles cannot run in this case."),
    ).toBeVisible();
  });

  it("adds a domain and shows where to publish the proof", async () => {
    const created = makeVerification({
      method: "http_file",
      proof: {
        record_type: null,
        record_name: null,
        url: "https://example.org/.well-known/nightloom-verification.txt",
        value: "nightloom-verification=abc123",
      },
    });
    api.on("POST", url, () => json(created, 201));
    const section = await openSettings();
    await userEvent.type(within(section).getByLabelText("Domain"), " example.org ");
    await userEvent.click(within(section).getByRole("radio", { name: "File on the website" }));
    await userEvent.click(within(section).getByRole("button", { name: "Add domain" }));
    expect(bodyOf("POST", url)).toEqual({ domain: "example.org", method: "http_file" });
    const item = await within(section).findByRole("listitem", { name: "example.org" });
    expect(within(item).getByText("Not verified yet")).toBeVisible();
    const proof = within(item).getByTestId("proof");
    expect(within(proof).getByText(created.proof.url ?? "")).toBeVisible();
    expect(within(proof).getByText("nightloom-verification=abc123")).toBeVisible();
    expect(within(proof).getByText(/over HTTPS and without redirects/)).toBeVisible();
    await userEvent.click(within(item).getByRole("button", { name: "Hide proof" }));
    expect(within(item).queryByTestId("proof")).toBeNull();
  });

  it("shows the API's refusal", async () => {
    api.on("POST", url, () =>
      json({ detail: "This is a public suffix (like co.uk) or has no known suffix." }, 422),
    );
    const section = await openSettings();
    await userEvent.click(within(section).getByRole("button", { name: "Add domain" }));
    expect(within(section).getByRole("alert")).toHaveTextContent("Enter a domain");
    await userEvent.type(within(section).getByLabelText("Domain"), "co.uk");
    await userEvent.click(within(section).getByRole("button", { name: "Add domain" }));
    await waitFor(() => {
      expect(within(section).getByRole("alert")).toHaveTextContent("public suffix");
    });
  });

  it("checks now and follows the check until it reports", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const pending = makeVerification();
    let listed: Verification[] = [pending];
    api.on("POST", `${url}/${pending.id}/check`, () => {
      listed = [{ ...pending, checking: true }];
      return json(listed[0], 202);
    });
    const section = await openSettings({ verifications: [pending] });
    api.on("GET", url, () => json({ items: listed }));
    const item = await within(section).findByRole("listitem", { name: "example.org" });
    await userEvent.click(within(item).getByRole("button", { name: "Check example.org now" }));
    expect(await within(item).findByText("Checking")).toBeVisible();
    expect(within(item).getByRole("button", { name: "Check example.org now" })).toBeDisabled();
    listed = [
      {
        ...pending,
        status: "verified",
        verified_at: "2026-11-21T10:00:00Z",
        expires_at: "2026-12-21T10:00:00Z",
        checked_at: "2026-11-21T10:00:00Z",
        addresses: ["93.184.215.14"],
      },
    ];
    await act(() => vi.advanceTimersByTimeAsync(CHECK_POLL_MS));
    await waitFor(() => {
      expect(within(item).getByText("Verified")).toBeVisible();
    });
    expect(within(item).getByText(/Until Dec 21, 2026/)).toBeVisible();
    expect(within(item).getByText(/also covers 93\.184\.215\.14/)).toBeVisible();
  });

  it("says why a proof was not found", async () => {
    const section = await openSettings({
      verifications: [
        makeVerification({
          status: "failed",
          last_error: "record_not_found",
          error_message: "No TXT record with the token was found.",
        }),
        makeVerification({ id: "b", domain: "example.net", status: "expired" }),
      ],
    });
    const failed = await within(section).findByRole("listitem", { name: "example.org" });
    expect(within(failed).getByText("Not found")).toBeVisible();
    expect(within(failed).getByText(/No TXT record with the token/)).toBeVisible();
    const expired = within(section).getByRole("listitem", { name: "example.net" });
    expect(within(expired).getByText(/Check again to renew/)).toBeVisible();
  });

  it("removes a domain after confirming", async () => {
    const item = makeVerification();
    api.on("DELETE", `${url}/${item.id}`, () => noContent());
    const section = await openSettings({ verifications: [item] });
    const row = await within(section).findByRole("listitem", { name: "example.org" });
    await userEvent.click(within(row).getByRole("button", { name: "Remove example.org" }));
    const dialog = screen.getByRole("dialog", { name: "Remove example.org?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => {
      expect(within(section).queryByRole("listitem", { name: "example.org" })).toBeNull();
    });
    expect(api.callsTo("DELETE", `${url}/${item.id}`)).toHaveLength(1);
  });

  it("only lists and removes in a closed case", async () => {
    const section = await openSettings({
      overrides: { status: "closed", closed_at: "2026-11-21T00:00:00Z" },
      verifications: [makeVerification()],
    });
    const row = await within(section).findByRole("listitem", { name: "example.org" });
    expect(within(section).queryByRole("form", { name: "Add a domain to verify" })).toBeNull();
    expect(within(row).queryByRole("button", { name: /^Check / })).toBeNull();
    expect(within(row).getByRole("button", { name: "Remove example.org" })).toBeVisible();
  });
});
