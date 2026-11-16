import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CASE_ID, makeCase, VIEWER_USER } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, READY_BODY, TEST_USER } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { ETHICS_DOC_URL } from "./labels";

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi()
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(TEST_USER))
    .on("GET", `/cases/${CASE_ID}`, () => json(makeCase()))
    .install();
  document.cookie = "nightloom_csrf=csrf-token";
});

afterEach(() => {
  clearCookies();
});

const PURPOSE = "Map the public attack surface of example.org before the audit.";

const field = (name: string | RegExp) => screen.getByLabelText(name);
const submit = () => userEvent.click(screen.getByRole("button", { name: "Create case" }));
const ethics = () => screen.getByRole("checkbox", { name: /I confirm this investigation/ });

async function open() {
  const view = renderApp("/cases/new");
  await screen.findByRole("heading", { name: "New case" });
  return view;
}

async function fillValid() {
  await userEvent.type(field("Title"), "Example.org exposure review");
  await userEvent.type(field("Purpose"), PURPOSE);
  await userEvent.selectOptions(field("Legal basis"), "owned_assets");
  await userEvent.click(ethics());
}

function errorFor(label: string | RegExp): string | null {
  const input = field(label);
  const ids = input.getAttribute("aria-describedby")?.split(" ") ?? [];
  const error = ids
    .map((id) => document.getElementById(id))
    .find((el) => el?.className === "field__error");
  return error?.textContent ?? null;
}

describe("create case form", () => {
  it("validates every required field locally and sends nothing", async () => {
    await open();
    await submit();
    expect(errorFor("Title")).toBe("Title must be at least 3 characters.");
    expect(errorFor("Purpose")).toBe("Purpose must be at least 30 characters.");
    expect(errorFor("Legal basis")).toBe("Choose a legal basis.");
    expect(
      screen.getByText("Confirm that this investigation follows the ethics policy."),
    ).toBeVisible();
    expect(ethics()).toHaveAttribute("aria-invalid", "true");
    expect(field("Title")).toHaveFocus();
    expect(api.callsTo("POST", "/cases")).toHaveLength(0);
  });

  it("explains why the purpose is required and counts its characters", async () => {
    await open();
    expect(
      screen.getByText(/requires a stated purpose before any data is collected/),
    ).toBeVisible();
    expect(screen.getByText("0 / 2000 characters (at least 30)")).toBeVisible();
    await userEvent.type(field("Purpose"), "x".repeat(30));
    expect(screen.getByText("30 / 2000 characters")).toBeVisible();
  });

  it("explains the selected legal basis", async () => {
    await open();
    await userEvent.selectOptions(field("Legal basis"), "journalism");
    expect(screen.getByText("Reporting in the public interest.")).toBeVisible();
    expect(screen.queryByLabelText(/Legal basis note/)).not.toBeInTheDocument();
  });

  it.each([
    ["authorized_engagement", "Name the engagement and who authorized it."],
    ["other", "Explain the legal basis in the note."],
  ])("requires a note for %s", async (basis, message) => {
    await open();
    await fillValid();
    await userEvent.selectOptions(field("Legal basis"), basis);
    expect(field("Legal basis note")).toBeRequired();
    await submit();
    expect(errorFor("Legal basis note")).toBe(message);
    expect(field("Legal basis note")).toHaveFocus();
  });

  it("keeps person entities off by default and asks for a reason when on", async () => {
    await open();
    const toggle = screen.getByRole("switch", { name: "Allow person entities" });
    expect(toggle).not.toBeChecked();
    expect(screen.queryByLabelText(/Why does this case need person entities/)).toBeNull();

    await fillValid();
    await userEvent.click(toggle);
    const warning = screen.getByRole("note");
    expect(warning).toHaveTextContent("Collecting data about people");
    expect(within(warning).getByRole("link", { name: "ethics policy" })).toHaveAttribute(
      "href",
      ETHICS_DOC_URL,
    );
    await userEvent.type(field(/Why does this case need person entities/), "Too short");
    await submit();
    expect(errorFor(/Why does this case need person entities/)).toBe(
      "Explain why in at least 30 characters.",
    );
    expect(api.callsTo("POST", "/cases")).toHaveLength(0);
  });

  it("checks the retention period", async () => {
    await open();
    await fillValid();
    await userEvent.type(field(/Retention/), "0");
    await submit();
    expect(errorFor(/Retention/)).toBe("Retention must be between 1 and 3650 days.");
  });

  it("checks the description length", async () => {
    await open();
    await fillValid();
    await userEvent.click(field(/Description/));
    await userEvent.paste("x".repeat(5001));
    expect(screen.getByText("5001 / 5000 characters")).toBeVisible();
    await submit();
    expect(errorFor(/Description/)).toBe("Description must be at most 5000 characters.");
  });

  it("links the ethics policy from the confirmation", async () => {
    await open();
    expect(screen.getAllByRole("link", { name: "ethics policy" })[0]).toHaveAttribute(
      "href",
      ETHICS_DOC_URL,
    );
  });

  it("creates the case and opens it", async () => {
    api.on("POST", "/cases", () => json(makeCase(), 201));
    const { router } = await open();
    await fillValid();
    await userEvent.selectOptions(field("Legal basis"), "authorized_engagement");
    await userEvent.type(field("Legal basis note"), "Pentest contract ACME-42");
    await userEvent.click(screen.getByRole("switch", { name: "Allow person entities" }));
    await userEvent.type(
      field(/Why does this case need person entities/),
      "The client asked us to review staff exposure.",
    );
    await userEvent.type(field(/Retention/), "90");
    await submit();
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/graph`);
    });
    const call = api.callsTo("POST", "/cases")[0]!;
    expect(call.headers["X-CSRF-Token"]).toBe("csrf-token");
    expect(JSON.parse(call.init.body as string)).toEqual({
      title: "Example.org exposure review",
      description: null,
      purpose: PURPOSE,
      legal_basis: "authorized_engagement",
      legal_basis_note: "Pentest contract ACME-42",
      ethics_acknowledged: true,
      allow_person_entities: true,
      person_entities_reason: "The client asked us to review staff exposure.",
      retention_days: 90,
    });
  });

  it("puts the server's 422 messages on the right fields", async () => {
    api.on("POST", "/cases", () =>
      json(
        {
          detail: [
            { loc: ["body", "title"], msg: "Title is taken in a parallel universe" },
            {
              loc: ["body"],
              msg: "Value error, legal_basis_note is required when legal_basis is authorized_engagement or other.",
            },
            { loc: ["body"], msg: "Value error, Something general." },
          ],
        },
        422,
      ),
    );
    await open();
    await fillValid();
    await submit();
    expect(await screen.findByText("Title is taken in a parallel universe")).toBeVisible();
    expect(errorFor("Title")).toBe("Title is taken in a parallel universe");
    expect(screen.getByRole("alert")).toHaveTextContent("Something general.");
    expect(field(/Legal basis note/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Create case" })).toBeEnabled();
  });

  it("maps the server's ethics message to the checkbox", async () => {
    api.on("POST", "/cases", () =>
      json(
        {
          detail: [
            {
              loc: ["body"],
              msg: "Value error, You must acknowledge ETHICS.md (ethics_acknowledged: true).",
            },
          ],
        },
        422,
      ),
    );
    await open();
    await fillValid();
    await submit();
    expect(
      await screen.findByText("You must acknowledge ETHICS.md (ethics_acknowledged: true)."),
    ).toBeVisible();
    expect(ethics()).toHaveFocus();
  });

  it("shows other failures as a form error", async () => {
    api.on("POST", "/cases", () => json({ detail: "x" }, 403));
    await open();
    await fillValid();
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You don't have permission to do this.",
    );
  });

  it("tells global viewers they cannot create cases", async () => {
    api.on("GET", "/auth/me", () => json(VIEWER_USER));
    renderApp("/cases/new");
    expect(await screen.findByRole("heading", { name: "You don't have permission" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Create case" })).not.toBeInTheDocument();
  });
});

describe("error clearing", () => {
  it("clears a field's error as soon as it is edited", async () => {
    await open();
    await submit();
    expect(errorFor("Title")).not.toBeNull();
    await userEvent.type(field("Title"), "Fixed title");
    expect(errorFor("Title")).toBeNull();
    expect(field("Title")).not.toHaveAttribute("aria-invalid");
    expect(errorFor("Purpose")).not.toBeNull();
    await userEvent.selectOptions(field("Legal basis"), "journalism");
    expect(errorFor("Legal basis")).toBeNull();
    await userEvent.click(ethics());
    expect(
      screen.queryByText("Confirm that this investigation follows the ethics policy."),
    ).not.toBeInTheDocument();
  });
});
