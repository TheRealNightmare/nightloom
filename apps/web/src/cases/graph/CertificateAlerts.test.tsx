/** The Graph tab's Certificate Transparency alerts: listing, showing, marking seen. */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CASE_ID, makeCtAlert, serveCase } from "../../test/caseFixtures";
import { clearCookies, FakeApi, json } from "../../test/fakeApi";
import { renderApp } from "../../test/renderApp";

let api: FakeApi;
const path = `/cases/${CASE_ID}`;
const ack = `${path}/ct-alerts/acknowledge`;

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

type ServeOptions = Parameters<typeof serveCase>[1];

async function openGraph(options: ServeOptions = {}) {
  serveCase(api, options);
  renderApp(`${path}/graph`);
  return screen.findByRole("region", { name: "New certificates" });
}

describe("certificate alerts", () => {
  it("lists unseen alerts with what is new", async () => {
    const section = await openGraph({
      ctAlerts: [
        makeCtAlert({
          names: ["a.example.org", "b.example.org", "c.example.org", "d.example.org"],
          names_total: 7,
          certificates: 2,
        }),
        makeCtAlert({ id: "a-2", domain: "example.net", certificates: 0 }),
      ],
    });
    const first = within(section).getByRole("listitem", { name: "Alert for example.org" });
    expect(first).toHaveTextContent("2 new certificates and 7 new names");
    expect(first).toHaveTextContent(
      "a.example.org, b.example.org, c.example.org, d.example.org and 3 more",
    );
    const second = within(section).getByRole("listitem", { name: "Alert for example.net" });
    expect(second).toHaveTextContent("1 new name");
    expect(within(section).getByRole("button", { name: "Mark all 2 seen" })).toBeVisible();
  });

  it("marks one or all seen", async () => {
    api.on("POST", ack, () => json({ acknowledged: 1 }));
    const section = await openGraph({
      ctAlerts: [makeCtAlert(), makeCtAlert({ id: "a-2", domain: "example.net" })],
    });
    await userEvent.click(
      within(section).getByRole("button", { name: "Mark the example.org alert seen" }),
    );
    expect(JSON.parse(api.callsTo("POST", ack).at(-1)?.init.body as string)).toEqual({
      ids: ["a-1"],
    });
    expect(within(section).queryByRole("listitem", { name: "Alert for example.org" })).toBeNull();
    await userEvent.click(
      within(section).getByRole("button", { name: "Mark the example.net alert seen" }),
    );
    expect(screen.queryByRole("region", { name: "New certificates" })).toBeNull();
  });

  it("lets viewers read but not mark seen", async () => {
    const section = await openGraph({ role: "viewer", ctAlerts: [makeCtAlert()] });
    expect(within(section).getByText(/1 new certificate and 1 new name/)).toBeVisible();
    expect(within(section).queryByRole("button", { name: /seen/ })).toBeNull();
    expect(within(section).getByRole("button", { name: "Show in graph" })).toBeVisible();
  });

  it("shows nothing without unseen alerts", async () => {
    serveCase(api);
    renderApp(`${path}/graph`);
    await screen.findByRole("region", { name: "Case graph" });
    expect(screen.queryByRole("region", { name: "New certificates" })).toBeNull();
  });
});
