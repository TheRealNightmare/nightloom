/**
 * The loom's own clock: draws at most `fps` frames a second, and only while
 * nothing pauses it (the tab is hidden, the scene is off-screen). Time only
 * moves while it runs, so the loom resumes where it stopped instead of
 * jumping ahead. No three.js here (it is tested on its own).
 */

export type PauseReason = "hidden" | "offscreen";

export interface FrameLoopOptions {
  /** Draws the scene at `seconds` of loom time. */
  draw: (seconds: number) => void;
  /** Loom time at the first frame. */
  startSeconds: number;
  /** At most this many frames a second (≤ 30 for the loom). */
  fps: number;
  /** For tests: the browser's frame scheduling and clock. */
  requestFrame?: (callback: (now: number) => void) => number;
  cancelFrame?: (handle: number) => void;
}

export interface FrameLoop {
  pause: (reason: PauseReason) => void;
  resume: (reason: PauseReason) => void;
  /** Whether frames are being drawn. */
  running: () => boolean;
  stop: () => void;
}

/** A frame that comes later than this (a stalled tab) moves the loom by this much at most. */
const MAX_STEP_SECONDS = 0.1;

export function createFrameLoop({
  draw,
  startSeconds,
  fps,
  requestFrame = (callback) => window.requestAnimationFrame(callback),
  cancelFrame = (handle) => {
    window.cancelAnimationFrame(handle);
  },
}: FrameLoopOptions): FrameLoop {
  const interval = 1000 / fps;
  const paused = new Set<PauseReason>();
  let seconds = startSeconds;
  let handle: number | null = null;
  let lastDraw: number | null = null;
  let stopped = false;

  const tick = (now: number) => {
    handle = null;
    if (stopped || paused.size > 0) return;
    // A small tolerance: a 60 Hz display's frames are 16.7 ms apart, so
    // every second one (33.3 ms) is drawn at 30 fps.
    if (lastDraw === null || now - lastDraw >= interval - 1) {
      if (lastDraw !== null) seconds += Math.min((now - lastDraw) / 1000, MAX_STEP_SECONDS);
      lastDraw = now;
      draw(seconds);
    }
    handle = requestFrame(tick);
  };

  const start = () => {
    if (stopped || handle !== null || paused.size > 0) return;
    // The time spent paused does not count.
    lastDraw = null;
    handle = requestFrame(tick);
  };

  start();

  return {
    pause(reason) {
      paused.add(reason);
      if (handle !== null) {
        cancelFrame(handle);
        handle = null;
      }
    },
    resume(reason) {
      paused.delete(reason);
      start();
    },
    running: () => !stopped && paused.size === 0,
    stop() {
      stopped = true;
      if (handle !== null) cancelFrame(handle);
      handle = null;
    },
  };
}
