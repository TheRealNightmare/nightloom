import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  ADMIN_USER,
  CASE_ID,
  makeCase,
  makeEvent,
  makeMember,
  OTHER_USER,
  serveCase,
  VIEWER_USER,
} from "../test/caseFixtures";
import { clearCookies, FakeApi, json, READY_BODY, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { GRAPH_EMPTY_MESSAGE } from "./GraphTab";

let api: FakeApi;
const path = `/cases/${CASE_ID}`;

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

const tabs = () => screen.getByRole("navigation", { name: "Case sections" });
const tabNames = () =>
  within(tabs())
    .getAllByRole("link")
    .map((link) => link.textContent);

async function openCase(tab = "graph") {
  const view = renderApp(`${path}/${tab}`);
  await screen.findByRole("navigation", { name: "Case sections" });
  return view;
}

describe("case header", () => {
  it("shows the title, status, legal basis, my role and a collapsed purpose", async () => {
    serveCase(api, { overrides: { legal_basis: "journalism", legal_basis_note: "Story #12" } });
    await openCase();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Example.org exposure review",
    );
    expect(screen.getByText("Open")).toBeVisible();
    expect(screen.getByText("Journalism")).toBeVisible();
    expect(screen.getByTitle("Your role in this case")).toHaveTextContent("Owner");
    const purpose = screen.getByText("Purpose").closest("details")!;
    expect(purpose).not.toHaveAttribute("open");
    await userEvent.click(screen.getByText("Purpose"));
    expect(purpose).toHaveAttribute("open");
    expect(within(purpose).getByText(/Map the public attack surface/)).toBeVisible();
    expect(within(purpose).getByText("Story #12")).toBeVisible();
  });

  it("never shows demo data in a case's graph", async () => {
    serveCase(api);
    await openCase();
    expect(screen.getByText(GRAPH_EMPTY_MESSAGE)).toBeVisible();
    expect(screen.queryByTestId("graph-canvas")).not.toBeInTheDocument();
    expect(screen.queryByText("Demo data")).not.toBeInTheDocument();
  });

  it("renames the case inline", async () => {
    serveCase(api, { role: "editor" });
    api.on("PATCH", path, () => json(makeCase({ title: "Renamed case", my_role: "editor" })));
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Case title" });
    await userEvent.clear(input);
    await userEvent.type(input, "  Renamed case {Enter}");
    expect(await screen.findByRole("heading", { name: "Renamed case" })).toBeVisible();
    expect(JSON.parse(api.callsTo("PATCH", path)[0]?.init.body as string)).toEqual({
      title: "Renamed case",
    });
    expect(screen.getByRole("button", { name: "Rename" })).toHaveFocus();
  });

  it("checks the new title and shows server errors inline", async () => {
    serveCase(api);
    api.on("PATCH", path, () =>
      json({ detail: [{ loc: ["body", "title"], msg: "Bad title" }] }, 422),
    );
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Case title" });
    await userEvent.clear(input);
    await userEvent.type(input, "ab{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Title must be at least 3 characters.");
    expect(api.callsTo("PATCH", path)).toHaveLength(0);
    await userEvent.type(input, "c{Enter}");
    expect(await screen.findByText("Bad title")).toBeVisible();
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("cancels a rename with Escape, and an unchanged title sends nothing", async () => {
    serveCase(api);
    await openCase();
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Case title" }), "xyz{Escape}");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Example.org exposure review",
    );
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("button", { name: "Rename" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.callsTo("PATCH", path)).toHaveLength(0);
  });
});

describe("what each role sees", () => {
  it("viewer: graph and members only, read-only, no rename", async () => {
    serveCase(api, { role: "viewer" });
    await openCase();
    expect(tabNames()).toEqual(["Graph", "Members"]);
    expect(screen.queryByRole("button", { name: "Rename" })).not.toBeInTheDocument();
  });

  it("editor: adds activity, may rename, no settings", async () => {
    serveCase(api, { role: "editor" });
    await openCase();
    expect(tabNames()).toEqual(["Graph", "Members", "Activity"]);
    expect(screen.getByRole("button", { name: "Rename" })).toBeVisible();
  });

  it("owner: every tab", async () => {
    serveCase(api, { role: "owner" });
    await openCase();
    expect(tabNames()).toEqual(["Graph", "Members", "Activity", "Settings"]);
  });

  it("a global viewer is shown as a case viewer", async () => {
    serveCase(api, { user: VIEWER_USER, role: "viewer" });
    await openCase();
    expect(tabNames()).toEqual(["Graph", "Members"]);
  });

  it.each(["activity", "settings"])(
    "viewer opening /%s directly sees a 403 message",
    async (tab) => {
      serveCase(api, { role: "viewer" });
      await openCase(tab);
      expect(screen.getByRole("heading", { name: "You don't have permission" })).toBeVisible();
    },
  );

  it("editor opening settings directly sees a 403 message", async () => {
    serveCase(api, { role: "editor" });
    await openCase("settings");
    expect(screen.getByRole("heading", { name: "You don't have permission" })).toBeVisible();
  });

  it("a non-member (even an admin) gets the same 'Case not found' as for a missing case", async () => {
    api
      .on("GET", "/ready", () => json(READY_BODY))
      .on("GET", "/auth/me", () => json(ADMIN_USER))
      .on("GET", path, () => json({ detail: "Case not found." }, 404));
    renderApp(`${path}/members`);
    expect(await screen.findByRole("heading", { name: "Case not found" })).toBeVisible();
    expect(screen.getByText(/does not exist, or you are not a member/)).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Case sections" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to cases" })).toHaveAttribute("href", "/cases");
  });

  it("shows an error with a retry when the case cannot load", async () => {
    let fail = true;
    api
      .on("GET", "/ready", () => json(READY_BODY))
      .on("GET", "/auth/me", () => json(TEST_USER))
      .on("GET", path, () => (fail ? json({}, 500) : json(makeCase())))
      .on("GET", `${path}/members`, () => json([]));
    renderApp(path);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(GRAPH_EMPTY_MESSAGE)).toBeVisible();
  });
});

describe("activity tab", () => {
  const bob = makeMember(OTHER_USER, "editor");

  it("lists events newest first with labels, actors, times and outcome badges", async () => {
    serveCase(api, {
      members: [makeMember(TEST_USER, "owner"), bob],
      events: [
        makeEvent({
          id: 4,
          action: "authz.denied",
          outcome: "denied",
          actor_label: OTHER_USER.email,
          details: { reason: "missing_capability", required: "manage" },
        }),
        makeEvent({
          id: 3,
          action: "case.update",
          details: { fields: ["purpose"], change_reason: "Client widened scope" },
        }),
        makeEvent({
          id: 2,
          action: "case.member_add",
          target_type: "user",
          target_id: OTHER_USER.id,
          details: { role: "editor" },
        }),
        makeEvent({ id: 1, action: "case.create", details: {} }),
      ],
    });
    await openCase("activity");
    const items = within(await screen.findByRole("list", { name: /Case activity/ })).getAllByRole(
      "listitem",
    );
    expect(items.map((item) => item.querySelector(".activity__text")?.textContent)).toEqual([
      "bob@example.org was refused: needs permission to manage the case",
      "analyst@example.org changed purpose — reason: Client widened scope",
      "analyst@example.org added Bob Builder as editor",
      "analyst@example.org created the case",
    ]);
    expect(within(items[0]!).getByText("Denied")).toBeVisible();
    expect(within(items[1]!).queryByText("Success")).not.toBeInTheDocument();
    expect(items[3]!.querySelector("time")).toHaveAttribute("title", "Nov 10, 2026, 10:00 AM UTC");
  });

  it("loads more events", async () => {
    serveCase(api);
    api
      .on("GET", `${path}/audit?limit=50`, () =>
        json({
          items: [
            makeEvent({
              id: 9,
              action: "case.delete",
              outcome: "failure",
              details: { reason: "confirm_title_mismatch" },
            }),
          ],
          next_before_id: 9,
        }),
      )
      .on("GET", `${path}/audit?before_id=9&limit=50`, () =>
        json({ items: [makeEvent({ id: 1 })], next_before_id: null }),
      );
    await openCase("activity");
    expect(await screen.findByText(/tried to delete the case/)).toBeVisible();
    expect(screen.getByText("Failed")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText(/created the case/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("says when there is nothing yet, and reports load errors", async () => {
    serveCase(api);
    await openCase("activity");
    expect(await screen.findByText("No activity recorded yet.")).toBeVisible();
  });

  it("reports a failure to load", async () => {
    serveCase(api);
    api.on("GET", `${path}/audit?limit=50`, () => json({ detail: "no" }, 403));
    await openCase("activity");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("You don't have permission to do this.");
    });
  });

  it("reports a failure to load more", async () => {
    serveCase(api);
    api
      .on("GET", `${path}/audit?limit=50`, () =>
        json({ items: [makeEvent({ id: 5 })], next_before_id: 5 }),
      )
      .on("GET", `${path}/audit?before_id=5&limit=50`, () => json({}, 500));
    await openCase("activity");
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});
