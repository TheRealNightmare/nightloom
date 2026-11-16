import { LogoMark } from "./LogoMark";

/** Shown while the session is being checked, so the sign-in form never flashes. */
export function Splash() {
  return (
    <div className="splash" role="status">
      <LogoMark size={56} />
      <span className="splash__name">Nightloom</span>
      <span className="visually-hidden">Loading…</span>
    </div>
  );
}
