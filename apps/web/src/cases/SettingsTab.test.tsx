import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Case, CaseUpdate } from "../api/cases";
import { CASE_ID, makeCase, serveCase } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

let api: FakeApi;
let current: Case;
const path = `/cases/${CASE_ID}`;

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
  current = makeCase();
  serveCase(api);
  api
    .on("GET", path, () => json(current))
    .on("PATCH", path, (init) => {
      const changes = JSON.parse(init.body as string) as CaseUpdate;
      delete changes.change_reason;
      current = {
        ...current,
        ...(changes as Partial<Case>),
        updated_at: new Date(Date.parse(current.updated_at) + 1000).toISOString(),
      };
      if (changes.status === "closed") current.closed_at = current.updated_at;
      return json(current);
    });
});

afterEach(() => {
  clearCookies();
});

async function openSettings() {
  const view = renderApp(`${path}/settings`);
  await screen.findByRole("heading", { name: "Purpose and legal basis" });
  return view;
}

const save = () => userEvent.click(screen.getByRole("button", { name: "Save changes" }));
const patches = () =>
  api.callsTo("PATCH", path).map((call) => JSON.parse(call.init.body as string) as CaseUpdate);
const dialog = () => screen.getByRole("dialog", { name: "Why are you making this change?" });

describe("settings form", () => {
  it("starts from the case's values", async () => {
    current = makeCase({
      legal_basis: "other",
      legal_basis_note: "Court order",
      retention_days: 30,
    });
    await openSettings();
    expect(screen.getByLabelText("Purpose")).toHaveValue(current.purpose);
    expect(screen.getByLabelText("Legal basis")).toHaveValue("other");
    expect(screen.getByLabelText("Legal basis note")).toHaveValue("Court order");
    expect(screen.getByRole("switch", { name: "Allow person entities" })).not.toBeChecked();
    expect(screen.getByLabelText(/Retention/)).toHaveValue("30");
  });

  it("says when there is nothing to save", async () => {
    await openSettings();
    await save();
    expect(screen.getByText("Nothing to save.")).toBeVisible();
    expect(patches()).toHaveLength(0);
  });

  it("saves retention without asking for a reason", async () => {
    await openSettings();
    await userEvent.type(screen.getByLabelText(/Retention/), "365");
    await save();
    await waitFor(() => {
      expect(patches()).toEqual([{ retention_days: 365 }]);
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks for a reason before changing the purpose, and sends it", async () => {
    await openSettings();
    const purpose = screen.getByLabelText("Purpose");
    await userEvent.clear(purpose);
    await userEvent.type(purpose, "A new purpose that is clearly long enough.");
    await save();
    expect(dialog()).toHaveTextContent("Changing the purpose is recorded");
    const reason = within(dialog()).getByLabelText("Reason for the change");
    expect(reason).toHaveFocus();

    await userEvent.type(reason, "short");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save change" }));
    expect(within(dialog()).getByText("Give a reason of at least 10 characters.")).toBeVisible();
    expect(patches()).toHaveLength(0);

    await userEvent.type(reason, " but now long enough");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save change" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(patches()).toEqual([
      {
        purpose: "A new purpose that is clearly long enough.",
        change_reason: "short but now long enough",
      },
    ]);
  });

  it("names every reason-requiring field, including switching on person entities", async () => {
    await openSettings();
    await userEvent.selectOptions(screen.getByLabelText("Legal basis"), "authorized_engagement");
    await userEvent.type(screen.getByLabelText("Legal basis note"), "Contract 42");
    await userEvent.click(screen.getByRole("switch", { name: "Allow person entities" }));
    expect(screen.getByRole("note")).toHaveTextContent("Collecting data about people");
    await save();
    expect(dialog()).toHaveTextContent(
      "Changing the legal basis, legal basis note and person entities setting",
    );
  });

  it("validates the form before asking for a reason", async () => {
    await openSettings();
    await userEvent.selectOptions(screen.getByLabelText("Legal basis"), "other");
    await userEvent.clear(screen.getByLabelText("Purpose"));
    await userEvent.type(screen.getByLabelText(/Retention/), "x");
    await save();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Purpose must be at least 30 characters.")).toBeVisible();
    expect(screen.getByText("Explain the legal basis in the note.")).toBeVisible();
    expect(screen.getByText("Enter a whole number of days.")).toBeVisible();
  });

  it("cancels the dialog with Escape and returns focus to Save", async () => {
    await openSettings();
    await userEvent.click(screen.getByRole("switch", { name: "Allow person entities" }));
    await save();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toHaveFocus();
    await save();
    await userEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
    expect(patches()).toHaveLength(0);
  });

  it("keeps focus inside the dialog", async () => {
    await openSettings();
    await userEvent.click(screen.getByRole("switch", { name: "Allow person entities" }));
    await save();
    const reason = within(dialog()).getByLabelText("Reason for the change");
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.tab();
    expect(reason).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(within(dialog()).getByRole("button", { name: "Save change" })).toHaveFocus();
  });

  it("shows the server's change_reason message in the dialog", async () => {
    api.on("PATCH", path, () =>
      json(
        {
          detail: [
            { loc: ["body", "change_reason"], msg: "String should have at most 500 characters" },
          ],
        },
        422,
      ),
    );
    await openSettings();
    await userEvent.click(screen.getByRole("switch", { name: "Allow person entities" }));
    await save();
    await userEvent.type(
      within(dialog()).getByLabelText("Reason for the change"),
      "Long enough reason",
    );
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save change" }));
    expect(
      await within(dialog()).findByText("String should have at most 500 characters"),
    ).toBeVisible();
  });

  it("closes the dialog and shows other server errors on the form", async () => {
    api.on("PATCH", path, () =>
      json(
        {
          detail: [
            {
              loc: ["body"],
              msg: "Value error, legal_basis_note is required when legal_basis is authorized_engagement or other.",
            },
          ],
        },
        422,
      ),
    );
    await openSettings();
    await userEvent.click(screen.getByRole("switch", { name: "Allow person entities" }));
    await save();
    await userEvent.type(
      within(dialog()).getByLabelText("Reason for the change"),
      "Long enough reason",
    );
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save change" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(screen.getByLabelText(/Legal basis note/)).toHaveAttribute("aria-invalid", "true");
  });

  it("shows a 403 from the server as a form error", async () => {
    api.on("PATCH", path, () => json({ detail: "no" }, 403));
    await openSettings();
    await userEvent.type(screen.getByLabelText(/Retention/), "5");
    await save();
    expect(await screen.findByText("You don't have permission to do this.")).toBeVisible();
  });
});

describe("status actions", () => {
  it("closes, reopens and archives", async () => {
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Close case" }));
    expect(await screen.findByRole("button", { name: "Reopen case" })).toBeVisible();
    expect(screen.getByText("Closed")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Reopen case" }));
    await userEvent.click(await screen.findByRole("button", { name: "Archive case" }));
    expect(await screen.findByText("Archived")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Archive case" })).not.toBeInTheDocument();
    expect(patches()).toEqual([{ status: "closed" }, { status: "open" }, { status: "archived" }]);
  });

  it("shows a failure inline", async () => {
    api.on("PATCH", path, () => json({}, 500));
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Close case" }));
    expect(await screen.findByText("Something went wrong. Try again in a moment.")).toBeVisible();
  });
});

describe("danger zone", () => {
  const confirmField = () => screen.getByLabelText(/Type the case title/);
  const deleteButton = () => screen.getByRole("button", { name: "Delete case" });

  it("deletes only after the exact title is typed, then returns to the list", async () => {
    api.on("DELETE", path, () => noContent());
    const { router } = await openSettings();
    expect(deleteButton()).toBeDisabled();
    await userEvent.type(confirmField(), "example.org exposure review");
    expect(deleteButton()).toBeDisabled();
    await userEvent.clear(confirmField());
    await userEvent.type(confirmField(), "Example.org exposure review");
    expect(deleteButton()).toBeEnabled();
    await userEvent.click(deleteButton());
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/cases");
    });
    expect(await screen.findByText("Deleted “Example.org exposure review”.")).toBeVisible();
    expect(JSON.parse(api.callsTo("DELETE", path)[0]?.init.body as string)).toEqual({
      confirm_title: "Example.org exposure review",
    });
  });

  it("shows the server's refusal", async () => {
    api.on("DELETE", path, () =>
      json({ detail: "confirm_title does not match the case title." }, 422),
    );
    await openSettings();
    await userEvent.type(confirmField(), "Example.org exposure review");
    await userEvent.click(deleteButton());
    expect(await screen.findByText("confirm_title does not match the case title.")).toBeVisible();
    expect(deleteButton()).toBeEnabled();
  });
});

describe("settings error clearing", () => {
  it("clears a field's error when it is edited", async () => {
    await openSettings();
    await userEvent.clear(screen.getByLabelText("Purpose"));
    await save();
    expect(screen.getByText("Purpose must be at least 30 characters.")).toBeVisible();
    await userEvent.type(screen.getByLabelText("Purpose"), "x");
    expect(screen.queryByText("Purpose must be at least 30 characters.")).not.toBeInTheDocument();
  });
});
