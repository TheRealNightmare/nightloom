import { Navigate, type RouteObject } from "react-router";

import { AuditPage } from "../admin/AuditPage";
import { RequireAdmin } from "../admin/RequireAdmin";
import { UsersPage } from "../admin/UsersPage";
import { AuthGate } from "../auth/AuthGate";
import { ActivityTab } from "../cases/ActivityTab";
import { CaseCreatePage } from "../cases/CaseCreatePage";
import { CaseListPage } from "../cases/CaseListPage";
import { CaseWorkspace } from "../cases/CaseWorkspace";
import { GraphTab } from "../cases/GraphTab";
import { MembersTab } from "../cases/MembersTab";
import { SettingsTab } from "../cases/SettingsTab";
import { Splash } from "../components/Splash";
import { NotFoundPage } from "./NotFoundPage";

/**
 * Every route sits under {@link AuthGate}, so a signed-out visitor sees the
 * sign-in screen at whatever URL they opened and stays on it after signing in.
 */
export const routes: RouteObject[] = [
  {
    element: <AuthGate />,
    children: [
      { index: true, element: <Navigate to="/cases" replace /> },
      { path: "cases", element: <CaseListPage /> },
      { path: "cases/new", element: <CaseCreatePage /> },
      {
        path: "cases/:caseId",
        element: <CaseWorkspace />,
        children: [
          { index: true, element: <Navigate to="graph" replace /> },
          { path: "graph", element: <GraphTab /> },
          { path: "members", element: <MembersTab /> },
          { path: "activity", element: <ActivityTab /> },
          { path: "settings", element: <SettingsTab /> },
          { path: "*", element: <NotFoundPage embedded /> },
        ],
      },
      {
        // Global admins only; everyone else gets "Page not found".
        path: "admin",
        element: <RequireAdmin />,
        children: [
          { index: true, element: <Navigate to="users" replace /> },
          { path: "users", element: <UsersPage /> },
          { path: "audit", element: <AuditPage /> },
          {
            path: "result-cache",
            // Rarely opened: loaded on demand, outside the main bundle.
            lazy: async () => ({
              Component: (await import("../admin/ResultCachePage")).ResultCachePage,
            }),
            hydrateFallbackElement: <Splash />,
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
      {
        path: "demo",
        // Loaded on demand: sigma and graphology are most of the bundle.
        lazy: async () => ({ Component: (await import("../demo/DemoPage")).DemoPage }),
        hydrateFallbackElement: <Splash />,
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
