/**
 * What a wheel event means on the graph: zoom (a mouse wheel, or a pinch),
 * or pan (two fingers scrolling on a trackpad).
 *
 * Browsers report both a mouse wheel and a trackpad scroll as `wheel` events
 * and never say which device sent them, so this is a heuristic. A pinch is
 * the one unambiguous case: browsers send it as a wheel event with `ctrlKey`
 * set (Safari sends gesture events instead, handled separately).
 *
 * The heuristic, in order (the first rule that applies wins):
 *
 * 1. `ctrlKey` → **pinch** (zoom smoothly toward the cursor). Holding Ctrl
 *    while turning a mouse wheel lands here too, which also zooms.
 * 2. `deltaMode` is lines or pages → **mouse** (only wheels report in lines;
 *    Firefox does so for most mice).
 * 3. Within {@link GESTURE_GAP_MS} of the previous event → the previous
 *    event's kind. One gesture keeps one meaning, so a trackpad's momentum
 *    scroll, whose deltas can grow large and round, never turns into a zoom
 *    half-way through, and a fast-spun wheel never into a pan.
 * 4. A horizontal component (`deltaX ≠ 0`) → **trackpad**. Plain wheels
 *    scroll vertically only.
 * 5. A fractional `deltaY` → **trackpad** (Chromium and Safari report
 *    trackpad deltas in fractional CSS pixels).
 * 6. `|deltaY|` below {@link MOUSE_MIN_DELTA} → **trackpad**; one wheel
 *    notch is 48–120 px in every current browser.
 * 7. Otherwise → **mouse**.
 *
 * Known edge cases (the View menu's "Scroll wheel" setting overrides it):
 * - macOS with an accelerated mouse in Safari can report small or fractional
 *   deltas for slow wheel turns: treated as a trackpad (pans).
 * - Mice with a free-spinning or high-resolution wheel (e.g. Logitech
 *   "MagSpeed") send many small deltas: treated as a trackpad (pans).
 * - Tilting a wheel sideways, or Shift+wheel on some systems, sets `deltaX`:
 *   pans, which is what a horizontal scroll means anyway.
 * - A trackpad flick that starts with a single large, round, vertical delta
 *   zooms for that one gesture.
 */

export type WheelKind = "mouse" | "trackpad" | "pinch";
export type WheelSetting = "auto" | "zoom" | "pan";
export type WheelAction = "zoom" | "pinch" | "pan";

/** DOM_DELTA_PIXEL; the others are lines (1) and pages (2). */
export const DELTA_PIXEL = 0;
/** Events closer than this belong to one gesture. */
export const GESTURE_GAP_MS = 120;
/** A wheel notch scrolls at least this many pixels (Firefox: 48; Chromium: 100–120). */
export const MOUSE_MIN_DELTA = 40;
/** Pixels per line and per page when a browser reports in those units. */
export const LINE_HEIGHT_PX = 16;
export const PAGE_HEIGHT_PX = 800;

export interface WheelSample {
  deltaX: number;
  deltaY: number;
  deltaMode: number;
  ctrlKey: boolean;
  /** Milliseconds (the event's timeStamp). */
  time: number;
}

export interface WheelVerdict {
  kind: WheelKind;
  time: number;
}

/** Classifies one event; `previous` is the verdict on the event before it. */
export function classifyWheel(sample: WheelSample, previous: WheelVerdict | null): WheelKind {
  if (sample.ctrlKey) return "pinch";
  if (sample.deltaMode !== DELTA_PIXEL) return "mouse";
  if (
    previous !== null &&
    previous.kind !== "pinch" &&
    sample.time - previous.time >= 0 &&
    sample.time - previous.time < GESTURE_GAP_MS
  ) {
    return previous.kind;
  }
  if (sample.deltaX !== 0) return "trackpad";
  if (!Number.isInteger(sample.deltaY)) return "trackpad";
  if (Math.abs(sample.deltaY) < MOUSE_MIN_DELTA) return "trackpad";
  return "mouse";
}

/** What to do with an event of `kind`, given the user's setting. */
export function wheelAction(kind: WheelKind, setting: WheelSetting): WheelAction {
  if (kind === "pinch") return "pinch";
  if (setting === "zoom") return "zoom";
  if (setting === "pan") return "pan";
  return kind === "trackpad" ? "pan" : "zoom";
}

/** Remembers the previous verdict, so a gesture keeps one meaning (rule 3). */
export class WheelClassifier {
  private previous: WheelVerdict | null = null;

  classify(sample: WheelSample): WheelKind {
    const kind = classifyWheel(sample, this.previous);
    this.previous = { kind, time: sample.time };
    return kind;
  }

  reset(): void {
    this.previous = null;
  }
}

/** The event's deltas in pixels, whatever unit it reports in. */
export function pixelDeltas(sample: Pick<WheelSample, "deltaX" | "deltaY" | "deltaMode">): {
  dx: number;
  dy: number;
} {
  const unit =
    sample.deltaMode === 1 ? LINE_HEIGHT_PX : sample.deltaMode === 2 ? PAGE_HEIGHT_PX : 1;
  return { dx: sample.deltaX * unit, dy: sample.deltaY * unit };
}

/** How much a pinch zooms per pixel of deltaY (Chromium sends about ±1–10 per frame). */
export const PINCH_SENSITIVITY = 0.01;

/** The camera-ratio factor for a pinch frame: > 1 zooms out, < 1 zooms in. */
export function pinchFactor(deltaY: number): number {
  const clamped = Math.max(-50, Math.min(50, deltaY));
  return Math.exp(clamped * PINCH_SENSITIVITY);
}

export function isWheelSetting(value: unknown): value is WheelSetting {
  return value === "auto" || value === "zoom" || value === "pan";
}
