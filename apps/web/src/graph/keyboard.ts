/**
 * The graph's keyboard map (the canvas is focusable). Pure: a key event in,
 * an action out, so the map is unit-tested and the shortcuts dialog and the
 * README list exactly what the handler does.
 */

/** Pixels per arrow press (pan), and per Shift+arrow press (move a pinned node). */
export const PAN_STEP_PX = 80;
export const MOVE_STEP_PX = 10;

export type KeyAction =
  | { kind: "pan"; dx: number; dy: number }
  | { kind: "moveNode"; dx: number; dy: number }
  | { kind: "zoom"; direction: 1 | -1 }
  | { kind: "rotate"; steps: 1 | -1 }
  | { kind: "reset" }
  | { kind: "focusNext"; backwards: boolean }
  | { kind: "select" }
  | { kind: "togglePin" }
  | { kind: "escape" }
  | { kind: "help" };

export interface KeyInput {
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
}

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

/**
 * The action for a key press, or null to let the browser have it. Keys with
 * Ctrl, Cmd or Alt are never taken (browser and system shortcuts).
 * `hasFocusedNode`: Enter, P and Shift+arrows act on a focused node only.
 */
export function keyAction(input: KeyInput, hasFocusedNode: boolean): KeyAction | null {
  if (input.ctrlKey || input.metaKey || input.altKey) return null;
  const arrow = ARROWS[input.key];
  if (arrow) {
    const [x, y] = arrow;
    if (input.shiftKey && hasFocusedNode) {
      return { kind: "moveNode", dx: x * MOVE_STEP_PX, dy: y * MOVE_STEP_PX };
    }
    // Moving the camera right shows what is to the right: the content moves left.
    return { kind: "pan", dx: x * PAN_STEP_PX, dy: y * PAN_STEP_PX };
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
    default:
      return null;
  }
}

export interface Shortcut {
  keys: readonly string[];
  description: string;
}

export interface ShortcutGroup {
  title: string;
  shortcuts: readonly Shortcut[];
}

/** Every graph control, for the shortcuts dialog. */
export const SHORTCUTS: readonly ShortcutGroup[] = [
  {
    title: "Keyboard (with the graph focused)",
    shortcuts: [
      { keys: ["←", "↑", "→", "↓"], description: "Pan" },
      { keys: ["+", "−"], description: "Zoom in / out" },
      { keys: ["Q", "E"], description: "Rotate left / right by 15°" },
      { keys: ["0"], description: "Reset the view (zoom, position and rotation)" },
      { keys: ["Tab", "Shift+Tab"], description: "Move to the next / previous entity" },
      { keys: ["Enter"], description: "Select the focused entity" },
      { keys: ["P"], description: "Pin or unpin the focused entity" },
      { keys: ["Shift+arrows"], description: "Move the focused pinned entity by 10 px" },
      { keys: ["Esc"], description: "Leave the entity (or clear the selection)" },
      { keys: ["?"], description: "Show these shortcuts" },
    ],
  },
  {
    title: "Mouse",
    shortcuts: [
      { keys: ["Click"], description: "Select an entity" },
      {
        keys: ["Ctrl+click", "⌘+click"],
        description: "Add or remove an entity from the selection",
      },
      { keys: ["Drag an entity"], description: "Move it (it stays pinned there)" },
      { keys: ["Shift+drag"], description: "Select the entities in a rectangle" },
      { keys: ["Drag"], description: "Pan" },
      { keys: ["Wheel"], description: "Zoom toward the pointer" },
      { keys: ["Alt+drag", "Right-drag"], description: "Rotate the view" },
      { keys: ["Right-click an entity"], description: "Pin, select neighbours, hide its type" },
    ],
  },
  {
    title: "Trackpad",
    shortcuts: [
      { keys: ["Two-finger scroll"], description: "Pan" },
      { keys: ["Pinch"], description: "Zoom toward the pointer" },
      { keys: ["Option+drag"], description: "Rotate the view (macOS)" },
    ],
  },
  {
    title: "Touch",
    shortcuts: [
      { keys: ["Drag"], description: "Pan, or move an entity when started on one" },
      { keys: ["Pinch"], description: "Zoom" },
      { keys: ["Two-finger twist"], description: "Rotate" },
      { keys: ["Long-press an entity"], description: "Open its menu" },
    ],
  },
];
