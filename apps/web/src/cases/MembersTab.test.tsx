import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Member } from "../api/cases";
import { CASE_ID, makeCase, makeMember, OTHER_USER, serveCase } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

let api: FakeApi;
const path = `/cases/${CASE_ID}`;
const me = makeMember(TEST_USER, "owner");
const bob = makeMember(OTHER_USER, "editor");
const carol: Member = {
  ...makeMember(
    {
      id: "3c1e9d6c-0b8f-4e9c-8a11-5f0c8a526a0e",
      email: "carol@example.org",
      display_name: "Carol Viewer",
    },
    "viewer",
  ),
  global_role: "viewer",
};

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

async function openMembers() {
  const view = renderApp(`${path}/members`);
  await screen.findByRole("table");
  return view;
}

const row = (name: string) => screen.getByRole("cell", { name: new RegExp(name) }).closest("tr")!;

describe("members tab", () => {
  it("lists name, email and role", async () => {
    serveCase(api, { members: [me, bob] });
    await openMembers();
    const bobRow = within(row("Bob Builder"));
    expect(bobRow.getByText("bob@example.org")).toBeVisible();
    expect(bobRow.getByRole("combobox", { name: "Role of Bob Builder" })).toHaveValue("editor");
    expect(within(row("Ada Analyst")).getByText("(you)")).toBeVisible();
  });

  it("is read-only for viewers, who can still leave", async () => {
    serveCase(api, { role: "viewer", members: [{ ...me, role: "viewer" }, bob] });
    await openMembers();
    expect(screen.getByText("Only case owners can add members or change roles.")).toBeVisible();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Add a member" })).not.toBeInTheDocument();
    expect(within(row("Bob Builder")).getByText("Editor")).toBeVisible();
    expect(screen.getByRole("button", { name: "Leave case" })).toBeVisible();
  });

  it("lets the owner add a member by email and role", async () => {
    let members = [me];
    serveCase(api);
    api
      .on("GET", `${path}/members`, () => json(members))
      .on("PUT", `${path}/members`, () => {
        members = [me, bob];
        return json(bob, 201);
      });
    await openMembers();
    const form = screen.getByRole("form", { name: "Add a member" });
    await userEvent.type(within(form).getByLabelText("Email"), " bob@example.org ");
    await userEvent.selectOptions(within(form).getByLabelText("Role"), "editor");
    await userEvent.click(within(form).getByRole("button", { name: "Add member" }));
    expect(await screen.findByText("Bob Builder")).toBeVisible();
    const call = api.callsTo("PUT", `${path}/members`)[0]!;
    expect(JSON.parse(call.init.body as string)).toEqual({
      email: "bob@example.org",
      role: "editor",
    });
    expect(call.headers["X-CSRF-Token"]).toBe("csrf-token");
    expect(within(form).getByLabelText("Email")).toHaveValue("");
  });

  it("checks the email locally and shows the server's unknown-user message", async () => {
    serveCase(api);
    api.on("PUT", `${path}/members`, () =>
      json({ detail: "No active user with that email." }, 404),
    );
    await openMembers();
    const form = screen.getByRole("form", { name: "Add a member" });
    await userEvent.click(within(form).getByRole("button", { name: "Add member" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Enter the email address");
    expect(api.callsTo("PUT", `${path}/members`)).toHaveLength(0);
    await userEvent.type(within(form).getByLabelText("Email"), "ghost@example.org");
    await userEvent.click(within(form).getByRole("button", { name: "Add member" }));
    expect(await within(form).findByText("No active user with that email.")).toBeVisible();
    expect(within(form).getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
  });

  it("shows the global-viewer rule from the server", async () => {
    serveCase(api);
    api.on("PUT", `${path}/members`, () =>
      json({ detail: "Users with the global viewer role can only be case viewers." }, 422),
    );
    await openMembers();
    const form = screen.getByRole("form", { name: "Add a member" });
    await userEvent.type(within(form).getByLabelText("Email"), "carol@example.org");
    await userEvent.selectOptions(within(form).getByLabelText("Role"), "owner");
    await userEvent.click(within(form).getByRole("button", { name: "Add member" }));
    expect(
      await within(form).findByText("Users with the global viewer role can only be case viewers."),
    ).toBeVisible();
  });

  it("offers only the viewer role for global viewers", async () => {
    serveCase(api, { members: [me, carol] });
    await openMembers();
    const select = within(row("Carol Viewer")).getByRole("combobox");
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Viewer"]);
    expect(within(row("Carol Viewer")).getByText(/Viewer account/)).toBeVisible();
  });

  it("changes a member's role", async () => {
    let members = [me, bob];
    serveCase(api);
    api
      .on("GET", `${path}/members`, () => json(members))
      .on("PUT", `${path}/members`, () => {
        members = [me, { ...bob, role: "owner" }];
        return json(members[1]);
      });
    await openMembers();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Role of Bob Builder" }),
      "owner",
    );
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Role of Bob Builder" })).toHaveValue("owner");
    });
    expect(JSON.parse(api.callsTo("PUT", `${path}/members`)[0]?.init.body as string)).toEqual({
      email: "bob@example.org",
      role: "owner",
    });
  });

  it("shows the last-owner conflict inline when demoting the only owner", async () => {
    serveCase(api, { members: [me, bob] });
    api.on("PUT", `${path}/members`, () =>
      json({ detail: "A case must keep at least one owner." }, 409),
    );
    await openMembers();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Role of Ada Analyst" }),
      "editor",
    );
    expect(await screen.findByText("A case must keep at least one owner.")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Role of Ada Analyst" })).toHaveValue("owner");
  });

  it("reloads the case after the owner changes their own role", async () => {
    let role: "owner" | "editor" = "owner";
    const other = { ...bob, role: "owner" as const };
    serveCase(api);
    api
      .on("GET", path, () => json(makeCase({ my_role: role })))
      .on("GET", `${path}/members`, () => json([{ ...me, role }, other]))
      .on("PUT", `${path}/members`, () => {
        role = "editor";
        return json({ ...me, role });
      });
    await openMembers();
    expect(screen.getByRole("link", { name: "Settings" })).toBeVisible();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Role of Ada Analyst" }),
      "editor",
    );
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
    });
    expect(
      await screen.findByText("Only case owners can add members or change roles."),
    ).toBeVisible();
  });

  it("removes another member", async () => {
    let members = [me, bob];
    serveCase(api);
    api
      .on("GET", `${path}/members`, () => json(members))
      .on("DELETE", `${path}/members/${OTHER_USER.id}`, () => {
        members = [me];
        return noContent();
      });
    await openMembers();
    await userEvent.click(screen.getByRole("button", { name: "Remove Bob Builder" }));
    await waitFor(() => {
      expect(screen.queryByText("Bob Builder")).not.toBeInTheDocument();
    });
  });

  it("lets a member leave after confirming, and returns to the list", async () => {
    serveCase(api, {
      role: "editor",
      members: [
        { ...me, role: "editor" },
        { ...bob, role: "owner" },
      ],
    });
    api.on("DELETE", `${path}/members/${TEST_USER.id}`, () => noContent());
    const { router } = await openMembers();
    await userEvent.click(screen.getByRole("button", { name: "Leave case" }));
    await userEvent.click(screen.getByRole("button", { name: "Stay" }));
    expect(api.callsTo("DELETE", `${path}/members/${TEST_USER.id}`)).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Leave case" }));
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/cases");
    });
    expect(await screen.findByText("You left “Example.org exposure review”.")).toBeVisible();
  });

  it("refuses to let the last owner leave", async () => {
    serveCase(api, { members: [me, bob] });
    api.on("DELETE", `${path}/members/${TEST_USER.id}`, () =>
      json({ detail: "A case must keep at least one owner." }, 409),
    );
    await openMembers();
    await userEvent.click(screen.getByRole("button", { name: "Leave case" }));
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(await screen.findByText("A case must keep at least one owner.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Leave case" })).toBeVisible();
  });

  it("reports when the members cannot load", async () => {
    serveCase(api);
    api.on("GET", `${path}/members`, () => json({}, 500));
    renderApp(`${path}/members`);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});
