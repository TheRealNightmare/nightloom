import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Case } from "../api/cases";
import { ADMIN_USER, EMPTY_PAGE, makeCase, OTHER_USER, VIEWER_USER } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, READY_BODY, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(TEST_USER))
    .install();
});

afterEach(() => {
  clearCookies();
});

const page = (items: Case[], next: string | null = null) => json({ items, next_cursor: next });
const second = makeCase({
  id: "00000000-0000-4000-8000-000000000002",
  title: "Phishing kit tracking",
  status: "closed",
  legal_basis: "threat_intelligence",
  my_role: "viewer",
});
const list = () => screen.getByRole("list", { name: "Cases" });

describe("case list", () => {
  it("shows each case's title, status, legal basis, my role and update time", async () => {
    api.on("GET", "/cases?limit=25", () => page([makeCase(), second]));
    renderApp("/cases");
    const rows = within(await screen.findByRole("list", { name: "Cases" })).getAllByRole(
      "listitem",
    );
    expect(rows).toHaveLength(2);
    const first = within(rows[0]!);
    expect(first.getByRole("link", { name: "Example.org exposure review" })).toHaveAttribute(
      "href",
      `/cases/${makeCase().id}`,
    );
    expect(first.getByText("Open")).toBeVisible();
    expect(first.getByText("Owned assets")).toBeVisible();
    expect(first.getByText("Owner")).toBeVisible();
    expect(first.getByText(/ago|just now|yesterday|last (week|month|year)/)).toHaveAttribute(
      "title",
      "Nov 10, 2026, 10:00 AM UTC",
    );
    const other = within(rows[1]!);
    expect(other.getByText("Closed")).toBeVisible();
    expect(other.getByText("Threat intelligence")).toBeVisible();
    expect(other.getByText("Viewer")).toBeVisible();
  });

  it("searches titles after typing stops", async () => {
    api
      .on("GET", "/cases?limit=25", () => page([makeCase(), second]))
      .on("GET", "/cases?limit=25&q=phish", () => page([second]));
    renderApp("/cases");
    await screen.findByRole("list", { name: "Cases" });
    await userEvent.type(screen.getByRole("searchbox", { name: "Search titles" }), "phish");
    await waitFor(() => {
      expect(within(list()).getAllByRole("listitem")).toHaveLength(1);
    });
    expect(within(list()).getByText("Phishing kit tracking")).toBeVisible();
    // One request for the finished word, none for the keystrokes before it.
    expect(api.calls.filter((c) => c.url.includes("q=")).map((c) => c.url)).toEqual([
      "/api/v1/cases?limit=25&q=phish",
    ]);
  });

  it("filters by status", async () => {
    api
      .on("GET", "/cases?limit=25", () => page([makeCase(), second]))
      .on("GET", "/cases?limit=25&status=closed", () => page([second]));
    renderApp("/cases");
    await screen.findByRole("list", { name: "Cases" });
    const status = screen.getByRole("group", { name: "Status" });
    expect(within(status).getByRole("radio", { name: "All" })).toBeChecked();
    await userEvent.click(within(status).getByRole("radio", { name: "Closed" }));
    expect(within(status).getByRole("radio", { name: "Closed" })).toBeChecked();
    await waitFor(() => {
      expect(within(list()).getAllByRole("listitem")).toHaveLength(1);
    });
  });

  it("loads more with the cursor until there are no more", async () => {
    api
      .on("GET", "/cases?limit=25", () => page([makeCase()], "cursor-1"))
      .on("GET", "/cases?cursor=cursor-1&limit=25", () => page([second]));
    renderApp("/cases");
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() => {
      expect(within(list()).getAllByRole("listitem")).toHaveLength(2);
    });
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("shows an error when loading more fails", async () => {
    api
      .on("GET", "/cases?limit=25", () => page([makeCase()], "cursor-1"))
      .on("GET", "/cases?cursor=cursor-1&limit=25", () => json({}, 500));
    renderApp("/cases");
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    expect(within(list()).getAllByRole("listitem")).toHaveLength(1);
  });

  it("shows an error when the list cannot load", async () => {
    api.on("GET", "/cases?limit=25", () => {
      throw new TypeError("Failed to fetch");
    });
    renderApp("/cases");
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach Nightloom");
  });

  it("shows an empty state with a New case button for analysts", async () => {
    api.on("GET", "/cases?limit=25", () => json(EMPTY_PAGE));
    renderApp("/cases");
    expect(await screen.findByRole("heading", { name: "No cases yet" })).toBeVisible();
    expect(screen.getByRole("link", { name: "New case" })).toHaveAttribute("href", "/cases/new");
  });

  it("offers New case to admins", async () => {
    api
      .on("GET", "/auth/me", () => json(ADMIN_USER))
      .on("GET", "/cases?limit=25", () => json(EMPTY_PAGE));
    renderApp("/cases");
    expect(await screen.findByRole("link", { name: "New case" })).toBeVisible();
  });

  it("hides New case from global viewers", async () => {
    api
      .on("GET", "/auth/me", () => json(VIEWER_USER))
      .on("GET", "/cases?limit=25", () => json(EMPTY_PAGE));
    renderApp("/cases");
    expect(await screen.findByText(/Ask a case owner to add you/)).toBeVisible();
    expect(screen.queryByRole("link", { name: "New case" })).not.toBeInTheDocument();
  });

  it("says when nothing matches the filters", async () => {
    api
      .on("GET", "/cases?limit=25", () => page([makeCase()]))
      .on("GET", "/cases?limit=25&status=archived", () => json(EMPTY_PAGE));
    renderApp("/cases");
    await screen.findByRole("list", { name: "Cases" });
    await userEvent.click(
      within(screen.getByRole("group", { name: "Status" })).getByRole("radio", {
        name: "Archived",
      }),
    );
    expect(await screen.findByRole("heading", { name: "No matching cases" })).toBeVisible();
  });

  it("shows a notice handed over by navigation", async () => {
    api.on("GET", "/cases?limit=25", () => json(EMPTY_PAGE));
    renderApp({ pathname: "/cases", state: { notice: `Deleted “${OTHER_USER.display_name}”.` } });
    expect(await screen.findByText("Deleted “Bob Builder”.")).toHaveAttribute("role", "status");
  });
});
