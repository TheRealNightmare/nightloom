import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ScanProfile } from "../api/scans";
import {
  ADMIN_USER,
  DNS_SHUTTLE,
  makeProfile,
  makeShuttle,
  QUICK_PROFILE,
} from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent, READY_BODY } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

let api: FakeApi;
let profiles: ScanProfile[];
const CUSTOM = makeProfile({
  id: "0f1e2d3c-4b5a-4968-8776-655443322110",
  name: "Mail check",
  description: "",
  shuttles: ["dns", "gone"],
  built_in: false,
  input_types: ["domain"],
  missing_shuttles: ["gone"],
});
const SHUTTLES = [
  DNS_SHUTTLE,
  makeShuttle({ name: "rdap", title: "Registration data (RDAP)" }),
  makeShuttle({ name: "active-scan", title: "Active scan", mode: "active" }),
];

beforeEach(() => {
  profiles = [QUICK_PROFILE, CUSTOM];
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(ADMIN_USER))
    .on("GET", "/scan-profiles", () => json(profiles))
    .on("GET", "/shuttles", () => json(SHUTTLES))
    .install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

async function openPage() {
  renderApp("/admin/profiles");
  return screen.findByRole("table", { name: "Scan profiles" });
}

describe("scan profiles page", () => {
  it("lists built-in and custom profiles; only custom ones can be changed", async () => {
    const table = await openPage();
    expect(document.title).toMatch(/^Scan profiles/);
    const [quick, custom] = within(table).getAllByRole("row").slice(1);
    expect(quick).toHaveTextContent("Quick domain recon · built in");
    expect(quick).toHaveTextContent("ASN, Domain, IP address, Netblock");
    expect(within(quick!).queryByRole("button")).toBeNull();
    expect(custom).toHaveTextContent("gone (not installed)");
    expect(within(custom!).getByRole("button", { name: "Edit Mail check" })).toBeVisible();
  });

  it("creates a profile with the Shuttles in the order ticked", async () => {
    api.on("POST", "/scan-profiles", (init) => {
      const body = JSON.parse(init.body as string) as { name: string };
      const created = makeProfile({ id: "new", name: body.name, built_in: false });
      profiles = [...profiles, created];
      return json(created, 201);
    });
    await openPage();
    await userEvent.click(screen.getByRole("button", { name: "New profile" }));
    const dialog = screen.getByRole("dialog", { name: "New scan profile" });
    // Active Shuttles cannot be chosen.
    expect(within(dialog).queryByRole("checkbox", { name: /Active scan/ })).toBeNull();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save profile" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Give the profile a name.");
    await userEvent.type(within(dialog).getByLabelText("Name"), "Registration first");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save profile" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Choose at least one Shuttle.");
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /Registration data/ }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /DNS records/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save profile" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved “Registration first”.");
    expect(JSON.parse(api.callsTo("POST", "/scan-profiles")[0]?.init.body as string)).toEqual({
      name: "Registration first",
      description: "",
      shuttles: ["rdap", "dns"],
    });
    expect(await screen.findByText("Registration first")).toBeVisible();
  });

  it("edits and deletes a custom profile, and shows refusals", async () => {
    api.on("PATCH", `/scan-profiles/${CUSTOM.id}`, () =>
      json({ detail: "A profile with this name already exists." }, 409),
    );
    api.on("DELETE", `/scan-profiles/${CUSTOM.id}`, () => {
      profiles = [QUICK_PROFILE];
      return noContent();
    });
    await openPage();
    await userEvent.click(screen.getByRole("button", { name: "Edit Mail check" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Mail check" });
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Mail check");
    expect(within(dialog).getByRole("checkbox", { name: /DNS records/ })).toBeChecked();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save profile" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/already exists/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await userEvent.click(screen.getByRole("button", { name: "Delete Mail check" }));
    await userEvent.click(
      within(screen.getByRole("dialog", { name: "Delete “Mail check”?" })).getByRole("button", {
        name: "Delete",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Deleted “Mail check”.");
    expect(screen.queryByText("Mail check")).toBeNull();
  });

  it("offers to try again when the profiles cannot load", async () => {
    let fail = true;
    api.on("GET", "/scan-profiles", () => (fail ? json({ detail: "Down." }, 503) : json(profiles)));
    renderApp("/admin/profiles");
    const retry = await screen.findByRole("button", { name: "Try again" });
    fail = false;
    await userEvent.click(retry);
    expect(await screen.findByRole("table", { name: "Scan profiles" })).toBeVisible();
  });
});
