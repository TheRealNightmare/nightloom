import { describe, expect, it } from "vitest";

import {
  canvasLabel,
  ELLIPSIS,
  MAX_CANVAS_LABEL,
  middleTruncate,
  shortFingerprint,
} from "./labels";

const FINGERPRINT = "c73e72dd5f0a9b8e7d6c5b4a39281706f5e4d3c2b1a0998877665544332241a3";

describe("canvas labels", () => {
  it("keeps short labels as they are", () => {
    expect(canvasLabel({ type: "domain", label: "www.example.org" })).toBe("www.example.org");
    const exact = "a".repeat(MAX_CANVAS_LABEL);
    expect(canvasLabel({ type: "url", label: exact })).toBe(exact);
  });

  it("cuts long labels in the middle to 32 characters", () => {
    const url = "https://www.example.org/a/very/long/path/to/some/archived/page.html";
    const short = canvasLabel({ type: "url", label: url });
    expect(Array.from(short)).toHaveLength(MAX_CANVAS_LABEL);
    expect(short.startsWith("https://www.exam")).toBe(true);
    expect(short.endsWith("ived/page.html")).toBe(true);
    expect(short).toContain(ELLIPSIS);
  });

  it("never splits a character made of two code units", () => {
    const text = "🧵".repeat(40);
    const short = middleTruncate(text, 9);
    const thread = Array.from("🧵".repeat(4));
    expect(Array.from(short)).toEqual([...thread, ELLIPSIS, ...thread]);
    expect(middleTruncate("abcdef", 1)).toBe(ELLIPSIS);
    expect(middleTruncate("abcdef", 2)).toBe(`a${ELLIPSIS}`);
  });

  it("shows a certificate's subject CN, else a short fingerprint", () => {
    expect(
      canvasLabel({
        type: "certificate",
        label: FINGERPRINT,
        properties: { subject_cn: "www.example.org" },
      }),
    ).toBe("www.example.org");
    expect(canvasLabel({ type: "certificate", label: FINGERPRINT })).toBe("c73e72dd…41a3");
    expect(
      canvasLabel({ type: "certificate", label: FINGERPRINT, properties: { subject_cn: "  " } }),
    ).toBe("c73e72dd…41a3");
    expect(
      canvasLabel({
        type: "certificate",
        label: FINGERPRINT,
        properties: { subject_cn: `${"x".repeat(40)}.example.org` },
      }),
    ).toHaveLength(MAX_CANVAS_LABEL);
    expect(shortFingerprint("abc")).toBe("abc");
  });
});
