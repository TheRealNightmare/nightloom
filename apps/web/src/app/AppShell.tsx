import { Outlet, useMatch } from "react-router";

import type { User } from "../api/client";
import { useReadiness } from "../api/useReadiness";
import { TopBar } from "../components/TopBar";
import { UserMenu } from "../components/UserMenu";

/** Top bar (logo, navigation, API status, user menu) above the current page. */
export function AppShell({ user }: { user: User }) {
  const { readiness, recheck } = useReadiness();
  const demo = useMatch("/demo") !== null;
  return (
    <div className="app">
      <TopBar
        readiness={readiness}
        onRecheck={recheck}
        demo={demo}
        account={<UserMenu user={user} />}
      />
      <Outlet />
    </div>
  );
}
