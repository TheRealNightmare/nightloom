import { describe, expect, it } from "vitest";

import { PAN_STEP_PX, type KeyInput } from "../keyboard";
import { keyAction3d, ORBIT_STEP, SHORTCUTS_3D } from "./keyboard3d";

const key = (value: string, modifiers: Partial<KeyInput> = {}): KeyInput => ({
  key: value,
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  ...modifiers,
});

describe("3D keyboard map", () => {
  it("orbits with the arrows and pans with Shift+arrows", () => {
    expect(keyAction3d(key("ArrowLeft"), false)).toEqual({
      kind: "orbit",
      azimuth: -ORBIT_STEP,
      polar: -0,
    });
    expect(keyAction3d(key("ArrowUp"), false)).toEqual({
      kind: "orbit",
      azimuth: 0,
      polar: ORBIT_STEP,
    });
    expect(keyAction3d(key("ArrowDown", { shiftKey: true }), true)).toEqual({
      kind: "pan",
      dx: 0,
      dy: PAN_STEP_PX,
    });
  });

  it("keeps the 2D keys: zoom, Q / E around the vertical axis, reset, Tab, views, help", () => {
    expect(keyAction3d(key("+"), false)).toEqual({ kind: "zoom", direction: 1 });
    expect(keyAction3d(key("_"), false)).toEqual({ kind: "zoom", direction: -1 });
    expect(keyAction3d(key("q"), false)).toEqual({ kind: "rotate", steps: -1 });
    expect(keyAction3d(key("E"), false)).toEqual({ kind: "rotate", steps: 1 });
    expect(keyAction3d(key("0"), false)).toEqual({ kind: "reset" });
    expect(keyAction3d(key("Tab", { shiftKey: true }), false)).toEqual({
      kind: "focusNext",
      backwards: true,
    });
    expect(keyAction3d(key("v"), false)).toEqual({ kind: "views" });
    expect(keyAction3d(key("?"), false)).toEqual({ kind: "help" });
    expect(keyAction3d(key("Escape"), false)).toEqual({ kind: "escape" });
  });

  it("acts on a focused node only for Enter and P", () => {
    expect(keyAction3d(key("Enter"), false)).toBeNull();
    expect(keyAction3d(key("Enter"), true)).toEqual({ kind: "select" });
    expect(keyAction3d(key("p"), false)).toBeNull();
    expect(keyAction3d(key("P"), true)).toEqual({ kind: "togglePin" });
  });

  it("leaves browser and system shortcuts, and other keys, alone", () => {
    expect(keyAction3d(key("ArrowLeft", { altKey: true }), false)).toBeNull();
    expect(keyAction3d(key("+", { ctrlKey: true }), false)).toBeNull();
    expect(keyAction3d(key("x"), false)).toBeNull();
  });

  it("lists every keyboard action in the shortcuts", () => {
    const keys = SHORTCUTS_3D.flatMap((group) => group.shortcuts.flatMap((s) => s.keys));
    for (const expected of ["←", "Shift+arrows", "Q", "0", "Tab", "Enter", "P", "Esc", "V", "?"]) {
      expect(keys).toContain(expected);
    }
  });
});
