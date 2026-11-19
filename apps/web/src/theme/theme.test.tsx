import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import themeInitScript from "../../public/theme-init.js?raw";
import baseCss from "../styles/base.css?raw";
import { useTheme } from "./context";
import {
  applyTheme,
  initialTheme,
  storedTheme,
  storeTheme,
  systemTheme,
  THEME_STORAGE_KEY,
  THEME_SWITCHING_ATTRIBUTE,
} from "./theme";
import { ThemeProvider } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";
import { token } from "./tokens";

/** A fake `prefers-color-scheme` that tests can flip. */
function mockSystem(initial: "light" | "dark" | "none") {
  let current = initial;
  const listeners = new Set<() => void>();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      get matches() {
        return query.includes("light") ? current === "light" : current === "dark";
      },
      media: query,
      addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    })),
  );
  return {
    set(next: "light" | "dark") {
      current = next;
      for (const listener of listeners) listener();
    },
    listeners,
  };
}

function blockStorage() {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("blocked", "SecurityError");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("blocked", "SecurityError");
  });
}

function ShowTheme() {
  return <p data-testid="theme">{useTheme().theme}</p>;
}

function renderToggle() {
  return render(
    <ThemeProvider>
      <ShowTheme />
      <ThemeToggle />
    </ThemeProvider>,
  );
}

const htmlTheme = () => document.documentElement.getAttribute("data-theme");

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute(THEME_SWITCHING_ATTRIBUTE);
});

describe("the system default", () => {
  it("follows prefers-color-scheme", () => {
    mockSystem("light");
    expect(systemTheme()).toBe("light");
    mockSystem("dark");
    expect(systemTheme()).toBe("dark");
  });

  it("is dark without a preference or without matchMedia", () => {
    mockSystem("none");
    expect(systemTheme()).toBe("dark");
    vi.stubGlobal("matchMedia", undefined);
    expect(systemTheme()).toBe("dark");
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => {
        throw new Error("unsupported");
      }),
    );
    expect(systemTheme()).toBe("dark");
  });

  it("is used when nothing is stored, and followed as it changes", () => {
    const system = mockSystem("light");
    renderToggle();
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(htmlTheme()).toBe("light");

    act(() => {
      system.set("dark");
    });
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
    expect(htmlTheme()).toBe("dark");
  });
});

describe("the toggle", () => {
  it("switches the theme, says what it will do, and remembers the choice", async () => {
    mockSystem("dark");
    const { unmount } = renderToggle();
    const toggle = screen.getByRole("button", { name: "Switch to light theme" });

    await userEvent.click(toggle);
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(htmlTheme()).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();

    // A new page load starts from the stored choice, not the system.
    unmount();
    document.documentElement.removeAttribute("data-theme");
    renderToggle();
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(htmlTheme()).toBe("light");
  });

  it("stops following the system once the user has chosen", async () => {
    const system = mockSystem("dark");
    renderToggle();
    await userEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(system.listeners.size).toBe(0);
    act(() => {
      system.set("dark");
    });
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
  });

  it("still works for this page when storage is unavailable", async () => {
    mockSystem("light");
    blockStorage();
    expect(storedTheme()).toBeNull();
    expect(storeTheme("dark")).toBe(false);
    expect(initialTheme()).toBe("light");

    renderToggle();
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
    expect(htmlTheme()).toBe("dark");
  });

  it("ignores a stored value that is not a theme", () => {
    mockSystem("light");
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    expect(storedTheme()).toBeNull();
    expect(initialTheme()).toBe("light");
  });

  it("is inert outside a provider (isolated components)", async () => {
    document.documentElement.setAttribute("data-theme", "light");
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(htmlTheme()).toBe("light");
  });
});

describe("applyTheme", () => {
  it("sets data-theme and the browser's theme color", () => {
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.media = "(prefers-color-scheme: dark)";
    document.head.append(meta);
    applyTheme("light");
    expect(htmlTheme()).toBe("light");
    expect(meta.content).toBe(token("light", "--bg"));
    expect(meta.hasAttribute("media")).toBe(false);
    meta.remove();
  });

  it("marks the switch for a moment only when asked to animate", () => {
    vi.useFakeTimers();
    try {
      const root = document.documentElement;
      applyTheme("dark");
      expect(root.hasAttribute(THEME_SWITCHING_ATTRIBUTE)).toBe(false);
      applyTheme("light", { animate: true });
      expect(root.hasAttribute(THEME_SWITCHING_ATTRIBUTE)).toBe(true);
      vi.runAllTimers();
      expect(root.hasAttribute(THEME_SWITCHING_ATTRIBUTE)).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("animates colors only without prefers-reduced-motion", () => {
    const css = baseCss;
    const rule = css.indexOf(`[${THEME_SWITCHING_ATTRIBUTE}]`);
    expect(rule).toBeGreaterThan(0);
    const media = css.lastIndexOf("@media", rule);
    expect(css.slice(media, rule)).toMatch(/^@media \(prefers-reduced-motion: no-preference\)/);
  });
});

describe("public/theme-init.js (before the first paint)", () => {
  const script = themeInitScript;
  const run = () => {
    // The shipped file, run as the browser runs it.
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const execute = new Function(themeInitScript) as () => void;
    execute();
  };

  it("applies the stored choice", () => {
    mockSystem("light");
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    run();
    expect(htmlTheme()).toBe("dark");
  });

  it("falls back to the system preference", () => {
    mockSystem("light");
    run();
    expect(htmlTheme()).toBe("light");
    window.localStorage.setItem(THEME_STORAGE_KEY, "nonsense");
    mockSystem("dark");
    run();
    expect(htmlTheme()).toBe("dark");
  });

  it("survives blocked storage and a missing matchMedia", () => {
    blockStorage();
    mockSystem("light");
    run();
    expect(htmlTheme()).toBe("light");
    vi.stubGlobal("matchMedia", undefined);
    run();
    expect(htmlTheme()).toBe("dark");
  });

  it("uses the storage key the app writes", () => {
    expect(script).toContain(`"${THEME_STORAGE_KEY}"`);
  });
});
