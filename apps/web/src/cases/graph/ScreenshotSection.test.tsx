import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EntityNode } from "../../domain/entities";
import { clearCookies, FakeApi, json } from "../../test/fakeApi";
import { LOOKS_LIKE_HINT, ScreenshotSection } from "./ScreenshotSection";

const CASE = "case-1";
const SHOT = `${"a".repeat(64)}.png`;
const KEY = "url:https://example.org/login";
let api: FakeApi;

beforeEach(() => {
  api = new FakeApi().install();
});

afterEach(() => {
  clearCookies();
});

function page(properties: Record<string, string>): EntityNode {
  return {
    id: KEY,
    label: "https://example.org/login",
    type: "url",
    properties,
    source: "shuttle:screenshot@1.0.0",
    first_seen: "2026-11-23T08:00:00Z",
    last_seen: "2026-11-23T08:00:00Z",
    confidence: 0.9,
  };
}

const similarUrl = `/cases/${CASE}/screenshots/similar?key=${encodeURIComponent(KEY)}`;

describe("ScreenshotSection", () => {
  it("shows the screenshot and the pages that look alike", async () => {
    api.on("GET", similarUrl, () =>
      json({
        items: [
          {
            key: "url:https://examp1e.org/login",
            type: "url",
            value: "https://examp1e.org/login",
            distance: 0,
            screenshot: SHOT,
          },
          {
            key: "url:https://example.net/",
            type: "url",
            value: "https://example.net/",
            distance: 7,
            screenshot: null,
          },
        ],
      }),
    );
    const focus = vi.fn();
    render(
      <ScreenshotSection
        caseId={CASE}
        entity={page({ screenshot: SHOT, screenshot_at: "2026-11-23T08:00:00Z" })}
        revision={1}
        onFocusEntity={focus}
      />,
    );
    const image = screen.getByRole("img", { name: "Screenshot of https://example.org/login" });
    expect(image).toHaveAttribute("src", `/api/v1/cases/${CASE}/evidence/${SHOT}`);
    expect(screen.getByText(LOOKS_LIKE_HINT)).toBeVisible();
    const list = await screen.findByRole("list", { name: "Pages that look alike" });
    expect(within(list).getByText("looks the same")).toBeVisible();
    expect(within(list).getByText("7 bits apart")).toBeVisible();
    await userEvent.click(within(list).getByRole("button", { name: "https://example.net/" }));
    expect(focus).toHaveBeenCalledWith("url:https://example.net/");
  });

  it("says when nothing looks alike", async () => {
    api.on("GET", similarUrl, () => json({ items: [] }));
    render(<ScreenshotSection caseId={CASE} entity={page({ screenshot: SHOT })} revision={1} />);
    expect(await screen.findByText("No other page in this case looks like it.")).toBeVisible();
  });

  it("shows nothing without a screenshot", () => {
    const { container } = render(
      <ScreenshotSection
        caseId={CASE}
        entity={page({ screenshot: "../../etc.png" })}
        revision={1}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(api.callsTo("GET", similarUrl)).toHaveLength(0);
  });

  it("says when the list cannot be loaded", async () => {
    api.on("GET", similarUrl, () => json({ detail: "down" }, 503));
    render(<ScreenshotSection caseId={CASE} entity={page({ screenshot: SHOT })} revision={1} />);
    expect(await screen.findByText("The look-alike pages could not be loaded.")).toBeVisible();
  });
});
