import { Outlet } from "react-router";

import { NotFoundPage } from "../app/NotFoundPage";
import { useAuth } from "../auth/context";

/**
 * Route guard for the admin pages: global admins see them, everyone else
 * gets the ordinary "Page not found" (the API refuses them anyway).
 */
export function RequireAdmin() {
  const { state } = useAuth();
  const admin = state.status === "signed_in" && state.user.role === "admin";
  return admin ? <Outlet /> : <NotFoundPage />;
}
