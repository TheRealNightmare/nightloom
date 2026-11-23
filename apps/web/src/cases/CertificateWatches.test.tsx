/** Settings → Certificate monitoring: adding, states, Check now, stopping. */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CASE_ID, makeCtWatch, serveCase } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { CT_WATCHES_HINT } from "./certificateText";

let api: FakeApi;
const path = `/cases/${CASE_ID}`;
const url = `${path}/ct-watches`;

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

type ServeOptions = Parameters<typeof serveCase>[1];

async function openSettings(options: ServeOptions = {}) {
  serveCase(api, options);
  renderApp(`${path}/settings`);
  return screen.findByRole("region", { name: "Certificate monitoring" });
}

describe("certificate monitoring", () => {
  it("explains itself and starts empty", async () => {
    const section = await openSettings();
    expect(within(section).getByText(CT_WATCHES_HINT)).toBeVisible();
    expect(await within(section).findByText("No domains are watched in this case.")).toBeVisible();
  });

  it("watches a domain", async () => {
    const created = makeCtWatch({ checked_at: null, baseline_at: null, last_job_id: null });
    api.on("POST", url, () => json(created, 201));
    const section = await openSettings();
    await userEvent.type(within(section).getByLabelText("Domain to watch"), " example.org ");
    await userEvent.click(within(section).getByRole("button", { name: "Watch domain" }));
    const sent = api.callsTo("POST", url).at(-1)?.init.body as string;
    expect(JSON.parse(sent)).toEqual({ domain: "example.org" });
    const item = await within(section).findByRole("listitem", { name: "example.org" });
    expect(within(item).getByText("Starting")).toBeVisible();
    expect(within(item).getByText("First check within a minute.")).toBeVisible();
  });

  it("shows the API's refusal", async () => {
    api.on("POST", url, () =>
      json({ detail: "Add other.org to the graph first, then watch it." }, 422),
    );
    const section = await openSettings();
    await userEvent.click(within(section).getByRole("button", { name: "Watch domain" }));
    expect(within(section).getByRole("alert")).toHaveTextContent("Enter a domain");
    await userEvent.type(within(section).getByLabelText("Domain to watch"), "other.org");
    await userEvent.click(within(section).getByRole("button", { name: "Watch domain" }));
    expect(await within(section).findByRole("alert")).toHaveTextContent(
      "Add other.org to the graph first",
    );
  });

  it("shows each watch's state", async () => {
    const section = await openSettings({
      ctWatches: [
        makeCtWatch(),
        makeCtWatch({
          id: "w-2",
          domain: "example.net",
          problem: "check_failed",
          problem_message: "The last check failed (crt.sh is often busy).",
        }),
        makeCtWatch({ id: "w-3", domain: "example.com", checking: true }),
      ],
    });
    const watching = await within(section).findByRole("listitem", { name: "example.org" });
    expect(within(watching).getByText("Watching")).toBeVisible();
    expect(within(watching).getByText(/new certificates show as alerts/)).toBeVisible();
    const failing = within(section).getByRole("listitem", { name: "example.net" });
    expect(within(failing).getByText("Needs attention")).toBeVisible();
    expect(within(failing).getByText(/crt\.sh is often busy/)).toBeVisible();
    const checking = within(section).getByRole("listitem", { name: "example.com" });
    expect(within(checking).getByRole("button", { name: "Check example.com now" })).toBeDisabled();
  });

  it("checks now and stops watching", async () => {
    api.on("POST", `${url}/w-1/check`, () => json(makeCtWatch({ checking: true }), 202));
    api.on("DELETE", `${url}/w-1`, () => noContent());
    const section = await openSettings({ ctWatches: [makeCtWatch()] });
    const item = await within(section).findByRole("listitem", { name: "example.org" });
    await userEvent.click(within(item).getByRole("button", { name: "Check example.org now" }));
    expect(await within(item).findByText("Checking")).toBeVisible();
    await userEvent.click(within(item).getByRole("button", { name: "Stop watching example.org" }));
    const dialog = await screen.findByRole("dialog", { name: "Stop watching example.org?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Stop watching" }));
    expect(await within(section).findByText("No domains are watched in this case.")).toBeVisible();
    expect(api.callsTo("DELETE", `${url}/w-1`)).toHaveLength(1);
  });

  it("hides adding and checking in a closed case", async () => {
    const section = await openSettings({
      overrides: { status: "closed" },
      ctWatches: [makeCtWatch()],
    });
    await within(section).findByRole("listitem", { name: "example.org" });
    expect(within(section).queryByLabelText("Domain to watch")).toBeNull();
    expect(within(section).queryByRole("button", { name: /Check .* now/ })).toBeNull();
    expect(
      within(section).getByRole("button", { name: "Stop watching example.org" }),
    ).toBeVisible();
  });
});
