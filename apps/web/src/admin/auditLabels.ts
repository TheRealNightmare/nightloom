/**
 * Readable text for the admin audit log. Case events reuse the case Activity
 * tab's wording ({@link describeEvent}); the rest (sign-ins, user management,
 * graph and job events) is described here.
 */
import type { AuditAction } from "../api/admin";
import type { AuditEvent } from "../api/cases";
import type { User } from "../api/client";
import { ROLE_LABELS } from "../auth/messages";
import { describeEvent, joinWords } from "../cases/labels";

/** Short names for the action filter. */
export const ACTION_LABELS: Record<AuditAction, string> = {
  "auth.login": "Sign-in",
  "auth.logout": "Sign-out",
  "auth.password_change": "Password change",
  "auth.rate_limited": "Rate limited",
  "user.create": "User created",
  "user.update": "User changed",
  "user.sessions_revoke": "Sessions revoked",
  "user.password_reset": "Password reset",
  "authz.denied": "Access refused",
  "audit.verify": "Audit chain verified",
  "case.create": "Case created",
  "case.update": "Case changed",
  "case.status_change": "Case status changed",
  "case.delete": "Case deleted",
  "case.member_add": "Member added",
  "case.member_update": "Member role changed",
  "case.member_remove": "Member removed",
  "case.graph_delete": "Case graph deleted",
  "case.export": "Graph exported",
  "entity.create": "Entity added",
  "entity.delete": "Entity deleted",
  "relationship.create": "Relationship added",
  "view.create": "Saved view created",
  "view.update": "Saved view changed",
  "view.delete": "Saved view deleted",
  "job.create": "Shuttle job queued",
  "job.cancel": "Shuttle job cancelled",
  "job.finish": "Shuttle job finished",
  "result_cache.clear": "Result cache cleared",
};

/** `reason` codes of failed sign-ins and admin actions. */
const REASONS: Record<string, string> = {
  unknown_email: "unknown email",
  wrong_password: "wrong password",
  inactive_user: "deactivated account",
  password_too_long: "password too long",
  invalid_credentials: "invalid credentials",
  wrong_current_password: "wrong current password",
  password_policy: "password policy",
  email_taken: "email already in use",
  conflict: "not allowed",
  not_found: "no such user",
  self_reset: "own account",
};

const USER_FIELDS: Record<string, string> = {
  display_name: "display name",
  role: "role",
  is_active: "active",
};

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function reasonSuffix(details: Record<string, unknown>): string {
  const reason = text(details["reason"]);
  if (reason === null) return "";
  return ` (${REASONS[reason] ?? reason.replaceAll("_", " ")})`;
}

function typeSuffix(details: Record<string, unknown>): string {
  const type = text(details["type"]);
  return type === null ? "" : ` (${type})`;
}

function roleText(value: unknown): string {
  return typeof value === "string" && value in ROLE_LABELS
    ? ROLE_LABELS[value as keyof typeof ROLE_LABELS].toLowerCase()
    : "an unknown role";
}

/**
 * What an audit event says, after the actor's name: "signed in", "reset the
 * password of Bob Builder", "created the case". `users` resolves user IDs to names.
 */
export function describeAuditEvent(event: AuditEvent, users: readonly User[] = []): string {
  const { details, outcome } = event;
  const failed = outcome !== "success";
  const target = () =>
    users.find((u) => u.id === event.target_id)?.display_name ?? text(details["email"]) ?? "a user";
  const revoked =
    typeof details["revoked_sessions"] === "number"
      ? `, ${plural(details["revoked_sessions"], "session")} ended`
      : "";

  switch (event.action) {
    case "auth.login":
      return failed ? `failed to sign in${reasonSuffix(details)}` : "signed in";
    case "auth.logout":
      return "signed out";
    case "auth.password_change":
      if (failed) return `failed to change their password${reasonSuffix(details)}`;
      return details["change_required"] === true
        ? `replaced their temporary password${revoked}`
        : `changed their password${revoked}`;
    case "auth.rate_limited":
      return "was rate limited";
    case "user.create": {
      if (failed) return `tried to create a user${reasonSuffix(details)}`;
      const how = details["credential"] === "generated" ? " with a temporary password" : "";
      return `created ${target()} as ${roleText(details["role"])}${how}`;
    }
    case "user.update": {
      if (failed) return `tried to change ${target()}${reasonSuffix(details)}`;
      const changes = details["changes"];
      const fields =
        typeof changes === "object" && changes !== null ? Object.keys(changes) : ([] as string[]);
      const names = fields.map((field) => USER_FIELDS[field] ?? field);
      return names.length > 0
        ? `changed ${joinWords(names)} of ${target()}${revoked}`
        : `changed ${target()}`;
    }
    case "user.sessions_revoke":
      return failed
        ? `tried to sign out ${target()}${reasonSuffix(details)}`
        : `signed out ${target()} everywhere${revoked}`;
    case "user.password_reset":
      return failed
        ? `tried to reset the password of ${target()}${reasonSuffix(details)}`
        : `reset the password of ${target()}${revoked}`;
    case "authz.denied": {
      if (event.case_id !== null) return describeEvent(event);
      const path = text(details["path"]);
      return path === null ? "was refused access" : `was refused access to ${path}`;
    }
    case "audit.verify":
      if (details["ok"] === true && typeof details["checked"] === "number") {
        return `verified the audit chain (${plural(details["checked"], "event")}, intact)`;
      }
      return typeof details["first_bad_id"] === "number"
        ? `verified the audit chain: broken at event #${details["first_bad_id"]}`
        : "verified the audit chain";
    case "case.graph_delete":
      return failed ? "failed to remove a deleted case's graph" : "removed a deleted case's graph";
    case "case.export":
      return failed
        ? `tried to export the graph${reasonSuffix({ reason: details["error"] })}`
        : `exported the graph as ${text(details["format"])?.toUpperCase() ?? "a file"}`;
    case "entity.create":
      return `added an entity${typeSuffix(details)}`;
    case "entity.delete":
      return `deleted an entity${typeSuffix(details)}`;
    case "relationship.create":
      return `added a relationship${typeSuffix(details)}`;
    case "job.create":
      return failed
        ? `tried to run ${text(details["shuttle"]) ?? "a Shuttle"}${reasonSuffix(details)}`
        : `ran ${text(details["shuttle"]) ?? "a Shuttle"}`;
    case "view.create":
    case "view.update":
    case "view.delete":
      return describeEvent(event);
    case "job.cancel":
      return "cancelled a Shuttle job";
    case "job.finish":
      return `finished a Shuttle job (${text(details["status"]) ?? outcome})`;
    case "result_cache.clear": {
      if (event.case_id !== null) return describeEvent(event);
      const shuttle = text(details["shuttle"]);
      const deleted = details["deleted"];
      const count =
        typeof deleted === "number"
          ? ` (${String(deleted)} ${deleted === 1 ? "entry" : "entries"})`
          : "";
      return shuttle === null || shuttle === "all"
        ? `cleared the result cache${count}`
        : `cleared the cached results of ${shuttle}${count}`;
    }
    default:
      // case.create, case.update, case.status_change, case.delete, case.member_*.
      return event.action.startsWith("case.") ? describeEvent(event) : event.action;
  }
}

/** Who did it: the recorded label, else the system or "Someone" (failed sign-ins). */
export function auditActor(event: AuditEvent): string {
  return event.actor_label ?? "Someone";
}
