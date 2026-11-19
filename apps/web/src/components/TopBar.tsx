import type { ReactNode } from "react";
import { Link, NavLink } from "react-router";

import type { ReadinessView } from "../api/useReadiness";
import { ThemeToggle } from "../theme/ThemeToggle";
import { LogoMark } from "./LogoMark";
import { StatusIndicator } from "./StatusIndicator";

export interface TopBarProps {
  readiness: ReadinessView;
  onRecheck: () => void;
  account?: ReactNode;
  /** Shows the "Demo data" badge (only on the demo graph). */
  demo?: boolean;
  /** Adds the admin pages (Users, Audit log) to the navigation. */
  admin?: boolean;
}

export function TopBar({
  readiness,
  onRecheck,
  account,
  demo = false,
  admin = false,
}: TopBarProps) {
  return (
    <header className="topbar">
      <div className="topbar__start">
        <Link to="/cases" className="brand" aria-label="Nightloom — cases">
          <LogoMark />
          <span className="brand__name">Nightloom</span>
        </Link>
        <nav className="topnav" aria-label="Main">
          <NavLink to="/cases" className="topnav__link">
            Cases
          </NavLink>
          <NavLink to="/demo" className="topnav__link">
            Demo
          </NavLink>
          {admin && (
            <>
              <NavLink to="/admin/users" className="topnav__link">
                Users
              </NavLink>
              <NavLink to="/admin/audit" className="topnav__link">
                Audit log
              </NavLink>
            </>
          )}
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
        <ThemeToggle />
        {account}
      </div>
    </header>
  );
}
