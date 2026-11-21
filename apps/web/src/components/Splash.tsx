import { LogoMark } from "./LogoMark";

/**
 * Shown while the session is being checked, so the sign-in form never
 * flashes. The pulsing mark is never alone for long: the words appear after
 * 300 ms (at once with reduced motion, where nothing pulses).
 */
export function Splash() {
  return (
    <div className="splash" role="status">
      <LogoMark size={56} />
      <span className="splash__name">Nightloom</span>
      <span className="splash__status">Loading…</span>
    </div>
  );
}
