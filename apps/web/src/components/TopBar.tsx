import type { ReactNode } from "react";
import { Link, NavLink } from "react-router";

import type { ReadinessView } from "../api/useReadiness";
import { LogoMark } from "./LogoMark";
import { StatusIndicator } from "./StatusIndicator";

export interface TopBarProps {
  readiness: ReadinessView;
  onRecheck: () => void;
  account?: ReactNode;
  /** Shows the "Demo data" badge (only on the demo graph). */
  demo?: boolean;
}

export function TopBar({ readiness, onRecheck, account, demo = false }: TopBarProps) {
  return (
    <header className="topbar">
      <div className="topbar__start">
        <Link to="/cases" className="brand" aria-label="Nightloom — cases">
          <LogoMark />
          <span className="brand__name">Nightloom</span>
          <span className="brand__tagline">Every thread, woven into the full picture.</span>
        </Link>
        <nav className="topnav" aria-label="Main">
          <NavLink to="/cases" className="topnav__link">
            Cases
          </NavLink>
          <NavLink to="/demo" className="topnav__link">
            Demo
          </NavLink>
        </nav>
      </div>
      <div className="topbar__end">
        {demo && (
          <span
            className="demo-badge"
            title="This graph is a built-in fixture with fictional, documentation-only data."
          >
            Demo data
          </span>
        )}
        <StatusIndicator readiness={readiness} onRecheck={onRecheck} />
        {account}
      </div>
    </header>
  );
}
