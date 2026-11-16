import type { ReactNode } from "react";

import type { ReadinessView } from "../api/useReadiness";
import { LogoMark } from "./LogoMark";
import { StatusIndicator } from "./StatusIndicator";

export interface TopBarProps {
  readiness: ReadinessView;
  onRecheck: () => void;
  account?: ReactNode;
}

export function TopBar({ readiness, onRecheck, account }: TopBarProps) {
  return (
    <header className="topbar">
      <div className="brand">
        <LogoMark />
        <span className="brand__name">Nightloom</span>
        <span className="brand__tagline">Every thread, woven into the full picture.</span>
      </div>
      <div className="topbar__end">
        <span
          className="demo-badge"
          title="This graph is a built-in fixture with fictional, documentation-only data."
        >
          Demo data
        </span>
        <StatusIndicator readiness={readiness} onRecheck={onRecheck} />
        {account}
      </div>
    </header>
  );
}
