/**
 * The 3D view's keyboard map: the 2D map where it makes sense (zoom, reset,
 * Tab, Enter, P, Escape, V, ?), with the arrows orbiting instead of panning
 * (Shift+arrows pan) and Q / E turning the graph around its vertical axis
 * (there is no roll). Pure, like ../keyboard.
 */
import { ROTATE_STEP } from "../geometry";
import { PAN_STEP_PX, type KeyInput, type ShortcutGroup } from "../keyboard";

/** One arrow press orbits this far (radians). */
export const ORBIT_STEP = Math.PI / 18;
/** One Q / E press: 15°, as in 2D. */
export const TURN_STEP = ROTATE_STEP;

export type KeyAction3D =
  | { kind: "orbit"; azimuth: number; polar: number }
  | { kind: "pan"; dx: number; dy: number }
  | { kind: "zoom"; direction: 1 | -1 }
  | { kind: "rotate"; steps: 1 | -1 }
  | { kind: "reset" }
  | { kind: "focusNext"; backwards: boolean }
  | { kind: "select" }
  | { kind: "togglePin" }
  | { kind: "escape" }
  | { kind: "help" }
  | { kind: "views" };

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

/**
 * The action for a key press, or null to let the browser have it (keys
 * with Ctrl, Cmd or Alt never are). Enter and P act on a focused node only.
 */
export function keyAction3d(input: KeyInput, hasFocusedNode: boolean): KeyAction3D | null {
  if (input.ctrlKey || input.metaKey || input.altKey) return null;
  const arrow = ARROWS[input.key];
  if (arrow) {
    const [x, y] = arrow;
    if (input.shiftKey) return { kind: "pan", dx: x * PAN_STEP_PX, dy: y * PAN_STEP_PX };
    // Left / right: the camera goes around to that side; up / down: it rises or sinks.
    return { kind: "orbit", azimuth: x * ORBIT_STEP, polar: -y * ORBIT_STEP };
  }
  switch (input.key) {
    case "+":
    case "=":
      return { kind: "zoom", direction: 1 };
    case "-":
    case "_":
      return { kind: "zoom", direction: -1 };
    case "q":
    case "Q":
      return { kind: "rotate", steps: -1 };
    case "e":
    case "E":
      return { kind: "rotate", steps: 1 };
    case "0":
      return { kind: "reset" };
    case "Tab":
      return { kind: "focusNext", backwards: input.shiftKey };
    case "Enter":
      return hasFocusedNode ? { kind: "select" } : null;
    case "p":
    case "P":
      return hasFocusedNode ? { kind: "togglePin" } : null;
    case "Escape":
      return { kind: "escape" };
    case "?":
      return { kind: "help" };
    case "v":
    case "V":
      return { kind: "views" };
    default:
      return null;
  }
}

/** Every 3D control, for the shortcuts dialog. */
export const SHORTCUTS_3D: readonly ShortcutGroup[] = [
  {
    title: "Keyboard (with the 3D graph focused)",
    shortcuts: [
      { keys: ["←", "→"], description: "Orbit around the graph by 10°" },
      { keys: ["↑", "↓"], description: "Look from higher / lower by 10°" },
      { keys: ["Shift+arrows"], description: "Pan" },
      { keys: ["+", "−"], description: "Zoom in / out" },
      { keys: ["Q", "E"], description: "Turn around the vertical axis by 15°" },
      { keys: ["0"], description: "Reset the view (frame the whole graph)" },
      {
        keys: ["Tab", "Shift+Tab"],
        description: "Next / previous entity (nearest first, then by position)",
      },
      { keys: ["Enter"], description: "Fly to the focused entity and select it" },
      { keys: ["P"], description: "Pin or unpin the focused entity" },
      { keys: ["Esc"], description: "Leave the entity (or clear the selection)" },
      { keys: ["V"], description: "Open the saved views of the case" },
      { keys: ["?"], description: "Show these shortcuts" },
    ],
  },
  {
    title: "Mouse",
    shortcuts: [
      { keys: ["Click"], description: "Select an entity" },
      { keys: ["Double-click"], description: "Fly to an entity" },
      {
        keys: ["Ctrl+click", "⌘+click"],
        description: "Add or remove an entity from the selection",
      },
      {
        keys: ["Drag an entity"],
        description: "Move it on the plane facing you (it stays pinned there)",
      },
      { keys: ["Shift+drag"], description: "Select the entities in a rectangle" },
      { keys: ["Drag"], description: "Orbit around the graph" },
      { keys: ["Right-drag"], description: "Pan" },
      { keys: ["Wheel"], description: "Zoom toward the pointer" },
      { keys: ["Right-click an entity"], description: "Pin, select neighbours, hide its type" },
    ],
  },
  {
    title: "Trackpad",
    shortcuts: [
      { keys: ["Two-finger scroll"], description: "Pan" },
      { keys: ["Pinch"], description: "Zoom toward the pointer" },
    ],
  },
  {
    title: "Touch",
    shortcuts: [
      { keys: ["Drag"], description: "Orbit, or move an entity when started on one" },
      { keys: ["Two-finger drag"], description: "Pan" },
      { keys: ["Pinch"], description: "Zoom" },
      { keys: ["Long-press an entity"], description: "Open its menu" },
    ],
  },
];

export const GRAPH_HELP_3D =
  "3D view. Tab moves through the entities, nearest first; Enter flies to one and selects it, P " +
  "pins it. Arrow keys orbit, Shift with arrows pans, plus and minus zoom, Q and E turn the graph, " +
  "0 resets the view. Press question mark for every shortcut.";
