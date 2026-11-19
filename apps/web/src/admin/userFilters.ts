import type { Role, User } from "../api/client";

export type ActiveFilter = "" | "active" | "inactive";

/** Case-insensitive search over display name and email. */
export function matchesUser(user: User, search: string, role: Role | "", active: ActiveFilter) {
  const needle = search.trim().toLowerCase();
  if (
    needle !== "" &&
    !user.display_name.toLowerCase().includes(needle) &&
    !user.email.toLowerCase().includes(needle)
  ) {
    return false;
  }
  if (role !== "" && user.role !== role) return false;
  if (active === "active" && !user.is_active) return false;
  if (active === "inactive" && user.is_active) return false;
  return true;
}
