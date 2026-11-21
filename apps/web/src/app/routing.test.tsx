import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CASE_ID,
  EMPTY_GRAPH,
  EMPTY_PAGE,
  makeCase,
  makeMember,
  SCHEMA,
} from "../test/caseFixtures";
import { clearCookies, FakeApi, json, READY_BODY, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { FakeSigma, waitForSigma } from "../test/sigmaMock";

vi.mock("sigma", () => import("../test/sigmaMock"));

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/cases?limit=25", () => json(EMPTY_PAGE))
    .on("GET", `/cases/${CASE_ID}`, () => json(makeCase()))
    .on("GET", `/cases/${CASE_ID}/members`, () => json([makeMember(TEST_USER, "owner")]))
    .on("GET", `/cases/${CASE_ID}/graph?limit=2000`, () => json(EMPTY_GRAPH))
    .on("GET", "/schema", () => json(SCHEMA))
    .install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  FakeSigma.reset();
  clearCookies();
});

const signedIn = () => api.on("GET", "/auth/me", () => json(TEST_USER));

describe("routing", () => {
  it("redirects / to the case list", async () => {
    signedIn();
    const { router } = renderApp("/");
    expect(await screen.findByRole("heading", { name: "Cases", level: 1 })).toBeVisible();
    expect(router.state.location.pathname).toBe("/cases");
  });

  it("opens a case on its graph tab", async () => {
    signedIn();
    const { router } = renderApp(`/cases/${CASE_ID}`);
    expect(await screen.findByText(/No entities yet/)).toBeVisible();
    expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/graph`);
    expect(screen.getByRole("link", { name: "Graph" })).toHaveAttribute("aria-current", "page");
  });

  it("shows the sign-in screen at a deep link and returns there after sign-in", async () => {
    api
      .on("GET", "/auth/me", () => json({ detail: "Not authenticated." }, 401))
      .on("POST", "/auth/login", () => json({ user: TEST_USER }));
    const { router } = renderApp(`/cases/${CASE_ID}/members`);
    await userEvent.type(await screen.findByLabelText("Email"), TEST_USER.email);
    await userEvent.type(screen.getByLabelText("Password"), "correct horse battery");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Members" })).toBeVisible();
    expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/members`);
  });

  it("shows a 404 page for an unknown address", async () => {
    signedIn();
    renderApp("/nowhere");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeVisible();
    expect(screen.getByText("/nowhere")).toBeVisible();
    expect(document.title).toBe("Page not found · Nightloom");
    await userEvent.click(screen.getByRole("link", { name: "Go to cases" }));
    expect(await screen.findByRole("heading", { name: "Cases", level: 1 })).toBeVisible();
  });

  it("shows a 404 inside a case for an unknown tab", async () => {
    signedIn();
    renderApp(`/cases/${CASE_ID}/nope`);
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Case sections" })).toBeVisible();
    await waitFor(() => {
      expect(document.title).toMatch(/ · Page not found · Nightloom$/);
    });
  });
});

describe("top bar", () => {
  it("links the logo and the navigation, and shows the demo badge only on the demo", async () => {
    signedIn();
    const { router } = renderApp("/cases");
    const banner = await screen.findByRole("banner");
    expect(within(banner).getByRole("link", { name: /Nightloom/ })).toHaveAttribute(
      "href",
      "/cases",
    );
    const nav = within(banner).getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Cases" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.queryByText("Demo data")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "API status: Ready" })).toBeVisible();

    await userEvent.click(within(nav).getByRole("link", { name: "Demo" }));
    // The demo graph is drawn (sigma in place), not just its canvas element.
    await waitForSigma();
    expect(screen.getByText("Demo data")).toBeVisible();
    expect(router.state.location.pathname).toBe("/demo");

    await userEvent.click(within(banner).getByRole("link", { name: /Nightloom/ }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/cases");
    });
    expect(screen.queryByText("Demo data")).not.toBeInTheDocument();
  });
});
