import { describe, expect, it } from "vitest";

import { keyAction, MOVE_STEP_PX, PAN_STEP_PX, SHORTCUTS, type KeyInput } from "./keyboard";

const key = (k: string, extra: Partial<KeyInput> = {}): KeyInput => ({
  key: k,
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  ...extra,
});

describe("graph keyboard map", () => {
  it("pans with the arrows", () => {
    expect(keyAction(key("ArrowLeft"), false)).toEqual({ kind: "pan", dx: -PAN_STEP_PX, dy: 0 });
    expect(keyAction(key("ArrowDown"), true)).toEqual({ kind: "pan", dx: 0, dy: PAN_STEP_PX });
  });

  it("moves the focused node with Shift+arrows, and pans without one", () => {
    expect(keyAction(key("ArrowUp", { shiftKey: true }), true)).toEqual({
      kind: "moveNode",
      dx: 0,
      dy: -MOVE_STEP_PX,
    });
    expect(keyAction(key("ArrowUp", { shiftKey: true }), false)?.kind).toBe("pan");
  });

  it("zooms, rotates and resets", () => {
    expect(keyAction(key("+"), false)).toEqual({ kind: "zoom", direction: 1 });
    expect(keyAction(key("="), false)).toEqual({ kind: "zoom", direction: 1 });
    expect(keyAction(key("-"), false)).toEqual({ kind: "zoom", direction: -1 });
    expect(keyAction(key("q"), false)).toEqual({ kind: "rotate", steps: -1 });
    expect(keyAction(key("E", { shiftKey: true }), false)).toEqual({ kind: "rotate", steps: 1 });
    expect(keyAction(key("0"), false)).toEqual({ kind: "reset" });
  });

  it("moves focus with Tab and acts on the focused node", () => {
    expect(keyAction(key("Tab"), false)).toEqual({ kind: "focusNext", backwards: false });
    expect(keyAction(key("Tab", { shiftKey: true }), true)).toEqual({
      kind: "focusNext",
      backwards: true,
    });
    expect(keyAction(key("Enter"), true)).toEqual({ kind: "select" });
    expect(keyAction(key("Enter"), false)).toBeNull();
    expect(keyAction(key("p"), true)).toEqual({ kind: "togglePin" });
    expect(keyAction(key("p"), false)).toBeNull();
    expect(keyAction(key("Escape"), false)).toEqual({ kind: "escape" });
    expect(keyAction(key("?", { shiftKey: true }), false)).toEqual({ kind: "help" });
    expect(keyAction(key("v"), false)).toEqual({ kind: "views" });
    expect(keyAction(key("V", { shiftKey: true }), true)).toEqual({ kind: "views" });
  });

  it("leaves browser and system shortcuts alone", () => {
    expect(keyAction(key("+", { ctrlKey: true }), false)).toBeNull();
    expect(keyAction(key("ArrowLeft", { altKey: true }), false)).toBeNull();
    expect(keyAction(key("0", { metaKey: true }), false)).toBeNull();
    expect(keyAction(key("x"), true)).toBeNull();
  });

  it("documents every action in the shortcuts list", () => {
    const listed = SHORTCUTS.flatMap((group) => group.shortcuts.flatMap((s) => s.keys));
    for (const keyName of ["Q", "E", "0", "P", "V", "Enter", "Esc", "Tab", "?", "Shift+drag"]) {
      expect(listed).toContain(keyName);
    }
  });
});
