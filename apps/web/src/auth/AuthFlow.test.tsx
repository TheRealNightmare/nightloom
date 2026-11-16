import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest, CSRF_HEADER } from "../api/client";
import { ETHICS_URL } from "../components/LoginScreen";
import { clearCookies, FakeApi, json, noContent, READY_BODY, TEST_USER } from "../test/fakeApi";
import { FakeSigma } from "../test/sigmaMock";
import { renderApp } from "../test/renderApp";
import { SESSION_ENDED_NOTICE, useAuth } from "./context";

vi.mock("sigma", () => import("../test/sigmaMock"));

const unauthenticated = () => json({ detail: "Not authenticated." }, 401);

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi().on("GET", "/ready", () => json(READY_BODY)).install();
});

afterEach(() => {
  FakeSigma.reset();
  clearCookies();
});

/** The demo graph route, so the signed-in app shows the graph. */
function renderGate() {
  return renderApp("/demo");
}

const emailField = () => screen.getByLabelText("Email");
const passwordField = () => screen.getByLabelText("Password");
const submit = () => screen.getByRole("button", { name: /Sign in/ });
const userMenuButton = () => screen.getByRole("button", { name: /Ada Analyst/ });

async function signIn(email = "analyst@example.org", password = "correct horse battery") {
  await userEvent.type(emailField(), email);
  await userEvent.type(passwordField(), password);
  await userEvent.click(submit());
}

async function renderSignedIn() {
  api.on("GET", "/auth/me", () => json(TEST_USER));
  document.cookie = "nightloom_csrf=csrf-token";
  const view = renderGate();
  await screen.findByRole("button", { name: /Ada Analyst/ });
  return view;
}

describe("start-up", () => {
  it("shows a splash, not the sign-in form, while checking the session", async () => {
    let answer: (response: Response) => void = () => {};
    api.on("GET", "/auth/me", () => new Promise<Response>((resolve) => (answer = resolve)));
    renderGate();
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();

    answer(unauthenticated());
    expect(await screen.findByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
    expect(screen.queryByText(SESSION_ENDED_NOTICE)).not.toBeInTheDocument();
  });

  it("goes straight to the app when a session exists", async () => {
    await renderSignedIn();
    expect(await screen.findByTestId("graph-canvas")).toBeInTheDocument();
    expect(screen.getByText("Demo data")).toBeVisible();
    expect(await screen.findByRole("button", { name: "API status: Ready" })).toBeVisible();
  });

  it("shows the sign-in form when the API cannot be reached", async () => {
    api.on("GET", "/auth/me", () => {
      throw new TypeError("Failed to fetch");
    });
    renderGate();
    expect(await screen.findByLabelText("Email")).toBeVisible();
  });

  it("cancels the session check on unmount", () => {
    api.on("GET", "/auth/me", () => new Promise<Response>(() => {}));
    const { unmount } = renderGate();
    unmount();
    expect(api.callsTo("GET", "/auth/me")[0]?.init.signal?.aborted).toBe(true);
  });

  it("requires the provider", () => {
    const Orphan = () => {
      useAuth();
      return null;
    };
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Orphan />)).toThrow(/inside <AuthProvider>/);
  });
});

describe("sign-in", () => {
  beforeEach(() => {
    api.on("GET", "/auth/me", unauthenticated);
  });

  it("is accessible: labelled fields, autocomplete, live error area, ethics link", async () => {
    renderGate();
    await screen.findByLabelText("Email");
    await waitFor(() => {
      expect(emailField()).toHaveFocus();
    });
    expect(emailField()).toHaveAttribute("autocomplete", "username");
    expect(passwordField()).toHaveAttribute("autocomplete", "current-password");
    expect(passwordField()).toHaveAttribute("type", "password");
    expect(screen.getByTestId("login-error")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("link", { name: "Use Nightloom responsibly" })).toHaveAttribute(
      "href",
      ETHICS_URL,
    );
    // The API status is only shown once signed in.
    expect(screen.queryByRole("button", { name: /API status/ })).not.toBeInTheDocument();
    expect(api.callsTo("GET", "/ready")).toHaveLength(0);
  });

  it("shows and hides the password", async () => {
    renderGate();
    const toggle = await screen.findByRole("button", { name: "Show password" });
    await userEvent.click(toggle);
    expect(passwordField()).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(toggle);
    expect(passwordField()).toHaveAttribute("type", "password");
  });

  it("signs in: loading → signed out → signed in", async () => {
    api.on("POST", "/auth/login", () => json({ user: TEST_USER }));
    renderGate();
    await screen.findByLabelText("Email");
    await signIn("  analyst@example.org ");
    expect(await screen.findByRole("button", { name: /Ada Analyst/ })).toBeVisible();
    expect(await screen.findByTestId("graph-canvas")).toBeInTheDocument();
    expect(JSON.parse(api.callsTo("POST", "/auth/login")[0]?.init.body as string)).toEqual({
      email: "analyst@example.org",
      password: "correct horse battery",
    });
  });

  it("disables the button while signing in", async () => {
    let answer: (response: Response) => void = () => {};
    api.on("POST", "/auth/login", () => new Promise<Response>((resolve) => (answer = resolve)));
    renderGate();
    await screen.findByLabelText("Email");
    await signIn();
    const button = screen.getByRole("button", { name: "Signing in…" });
    expect(button).toBeDisabled();
    await act(async () => {
      answer(json({ user: TEST_USER }));
      await Promise.resolve();
    });
    expect(await screen.findByRole("button", { name: /Ada Analyst/ })).toBeVisible();
  });

  it("shows the generic message on 401, clears the password and focuses the error", async () => {
    api.on("POST", "/auth/login", () => json({ detail: "something specific" }, 401));
    renderGate();
    await screen.findByLabelText("Email");
    await signIn();
    const error = screen.getByTestId("login-error");
    await waitFor(() => {
      expect(error).toHaveTextContent("Invalid email or password.");
    });
    expect(error).toHaveFocus();
    expect(error).not.toHaveTextContent("something specific");
    expect(passwordField()).toHaveValue("");
    expect(emailField()).toHaveValue("analyst@example.org");
    expect(submit()).toBeEnabled();
  });

  it.each([
    ["61", "Too many attempts. Try again in 2 minutes."],
    ["60", "Too many attempts. Try again in 1 minute."],
    ["5", "Too many attempts. Try again in 1 minute."],
    [null, "Too many attempts. Try again later."],
  ])("shows minutes on 429 (Retry-After %j)", async (retryAfter, message) => {
    api.on("POST", "/auth/login", () =>
      json({}, 429, retryAfter === null ? {} : { "Retry-After": retryAfter }),
    );
    renderGate();
    await screen.findByLabelText("Email");
    await signIn();
    await waitFor(() => {
      expect(screen.getByTestId("login-error")).toHaveTextContent(message);
    });
    expect(passwordField()).toHaveValue("correct horse battery");
  });

  it.each([
    [
      () => {
        throw new TypeError("Failed to fetch");
      },
      "Cannot reach Nightloom. Check your connection and try again.",
    ],
    [() => json({ detail: "Valkey down" }, 503), "Sign-in is temporarily unavailable."],
    [() => json({ detail: "boom" }, 500), "Something went wrong. Try again in a moment."],
  ])("shows a fixed message for other failures", async (handler, message) => {
    api.on("POST", "/auth/login", handler);
    renderGate();
    await screen.findByLabelText("Email");
    await signIn();
    await waitFor(() => {
      expect(screen.getByTestId("login-error")).toHaveTextContent(message);
    });
  });

  it("asks for both fields before calling the API", async () => {
    renderGate();
    await screen.findByLabelText("Email");
    await userEvent.click(submit());
    expect(screen.getByTestId("login-error")).toHaveTextContent("Enter your email and password.");
    expect(api.callsTo("POST", "/auth/login")).toHaveLength(0);
  });
});

describe("signed in", () => {
  it("returns to the sign-in screen with a notice when the session expires", async () => {
    await renderSignedIn();
    api.on("GET", "/cases", unauthenticated);
    await act(async () => {
      await apiRequest("/cases").catch(() => undefined);
    });
    expect(await screen.findByRole("heading", { name: "Sign in to Nightloom" })).toBeVisible();
    expect(screen.getByText(SESSION_ENDED_NOTICE)).toBeVisible();
    expect(screen.queryByRole("button", { name: /API status/ })).not.toBeInTheDocument();
  });

  it("shows the user's name and role in the menu", async () => {
    await renderSignedIn();
    const trigger = userMenuButton();
    expect(trigger).toHaveTextContent("Analyst");
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("analyst@example.org")).toBeVisible();
    expect(screen.getByRole("menu")).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "Change password" })).toHaveFocus();
  });

  it("signs out with the CSRF header", async () => {
    api.on("POST", "/auth/logout", () => noContent());
    await renderSignedIn();
    await userEvent.click(userMenuButton());
    await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByLabelText("Email")).toBeVisible();
    expect(api.callsTo("POST", "/auth/logout")[0]?.headers[CSRF_HEADER]).toBe("csrf-token");
    expect(screen.queryByText(SESSION_ENDED_NOTICE)).not.toBeInTheDocument();
  });

  it.each([
    [
      "a network error",
      () => {
        throw new TypeError("Failed to fetch");
      },
    ],
    ["an ended session", unauthenticated],
    ["a server error", () => json({}, 500)],
  ])("signs out locally even on %s", async (_label, handler) => {
    api.on("POST", "/auth/logout", handler);
    await renderSignedIn();
    await userEvent.click(userMenuButton());
    await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByLabelText("Email")).toBeVisible();
    expect(screen.queryByText(SESSION_ENDED_NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByTestId("graph-canvas")).not.toBeInTheDocument();
  });
});

describe("user menu keyboard", () => {
  it("opens with ArrowDown, moves with arrows, Home and End", async () => {
    await renderSignedIn();
    userMenuButton().focus();
    await userEvent.keyboard("{ArrowDown}");
    const change = screen.getByRole("menuitem", { name: "Change password" });
    const signOut = screen.getByRole("menuitem", { name: "Sign out" });
    expect(change).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(signOut).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(change).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(signOut).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(change).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(signOut).toHaveFocus();
    await userEvent.keyboard("x");
    expect(signOut).toHaveFocus();
  });

  it("closes on Escape and returns focus to the button", async () => {
    await renderSignedIn();
    await userEvent.click(userMenuButton());
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(userMenuButton()).toHaveFocus();
  });

  it("closes on Escape from the button itself", async () => {
    await renderSignedIn();
    await userEvent.click(userMenuButton());
    userMenuButton().focus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes on Tab", async () => {
    await renderSignedIn();
    await userEvent.click(userMenuButton());
    await userEvent.tab();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("stays open on a click inside", async () => {
    await renderSignedIn();
    await userEvent.click(userMenuButton());
    await userEvent.click(
      within(screen.getByRole("menu").parentElement!).getByText("analyst@example.org"),
    );
    expect(screen.getByRole("menu")).toBeVisible();
  });

  it("closes on a click outside", async () => {
    await renderSignedIn();
    await userEvent.click(userMenuButton());
    await userEvent.click(screen.getByText("Demo data"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
