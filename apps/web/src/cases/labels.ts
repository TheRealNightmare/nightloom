import type { AuditEvent, CaseRole, CaseStatus, LegalBasis, Member } from "../api/cases";

export const ETHICS_DOC_URL = "https://github.com/TheRealNightmare/nightloom/blob/main/ETHICS.md";

export const LEGAL_BASIS_LABELS: Record<LegalBasis, string> = {
  owned_assets: "Owned assets",
  authorized_engagement: "Authorized engagement",
  threat_intelligence: "Threat intelligence",
  journalism: "Journalism",
  academic_research: "Academic research",
  self_assessment: "Self-assessment",
  other: "Other",
};

/** One line per legal basis, shown under the select. */
export const LEGAL_BASIS_HELP: Record<LegalBasis, string> = {
  owned_assets: "Infrastructure you or your organization own or operate.",
  authorized_engagement:
    "An assessment you are contracted or otherwise authorized to perform — name it in the note.",
  threat_intelligence: "Tracking malicious infrastructure and campaigns, not private individuals.",
  journalism: "Reporting in the public interest.",
  academic_research: "Research under your institution's ethics and legal rules.",
  self_assessment: "Your own online footprint or that of your own organization.",
  other: "Another lawful basis — explain it in the note.",
};

export const STATUS_LABELS: Record<CaseStatus, string> = {
  open: "Open",
  closed: "Closed",
  archived: "Archived",
};

export const CASE_ROLE_LABELS: Record<CaseRole, string> = {
  owner: "Owner",
  editor: "Editor",
  viewer: "Viewer",
};

/** Field names as they appear in `case.update` events. */
const FIELD_LABELS: Record<string, string> = {
  title: "title",
  description: "description",
  purpose: "purpose",
  legal_basis: "legal basis",
  legal_basis_note: "legal basis note",
  allow_person_entities: "person entities setting",
  retention_days: "retention period",
};

const CAPABILITY_LABELS: Record<string, string> = {
  read: "read the case",
  edit: "edit the case",
  run_shuttles: "run Shuttles",
  read_audit: "read the case activity",
  manage: "manage the case",
};

const FAILURE_REASONS: Record<string, string> = {
  last_owner: "the case must keep an owner",
  not_found: "no such user or member",
  confirm_title_mismatch: "the title did not match",
  too_many_runs: "two runs already in progress",
};

/** "a, b and c". */
export function joinWords(words: string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words.at(-1) ?? ""}`;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function roleLabel(value: unknown): string {
  return typeof value === "string" && value in CASE_ROLE_LABELS
    ? CASE_ROLE_LABELS[value as CaseRole].toLowerCase()
    : "an unknown role";
}

function withReason(label: string, reason: string | null): string {
  return reason === null ? label : `${label} — reason: ${reason}`;
}

/**
 * What a case audit event says, after the actor's name: "created the case",
 * "changed purpose — reason: …". `members` resolves user IDs to names.
 */
export function describeEvent(event: AuditEvent, members: readonly Member[] = []): string {
  const { details, outcome } = event;
  const failed = outcome !== "success";
  const failure = text(details["reason"]);
  const failureNote =
    failure !== null && failure in FAILURE_REASONS ? ` (${FAILURE_REASONS[failure] ?? ""})` : "";
  const member = () =>
    members.find((m) => m.user_id === event.target_id)?.display_name ?? "a former member";
  const onSelf = event.target_id !== null && event.target_id === event.actor_user_id;

  switch (event.action) {
    case "case.create": {
      const base = "created the case";
      return details["allow_person_entities"] === true
        ? withReason(
            `${base} with person entities allowed`,
            text(details["person_entities_reason"]),
          )
        : base;
    }
    case "case.update": {
      const fields = Array.isArray(details["fields"])
        ? details["fields"].map((f) => FIELD_LABELS[String(f)] ?? String(f))
        : [];
      const label = fields.length > 0 ? `changed ${joinWords(fields)}` : "changed the case";
      return withReason(label, text(details["change_reason"]));
    }
    case "case.status_change": {
      const to = text(details["to"]);
      if (to === "closed") return "closed the case";
      if (to === "archived") return "archived the case";
      if (to === "open") return "reopened the case";
      return "changed the case's status";
    }
    case "case.delete":
      return failed ? `tried to delete the case${failureNote}` : "deleted the case";
    case "case.member_add":
      return failed
        ? `tried to add a member${failureNote}`
        : `added ${member()} as ${roleLabel(details["role"])}`;
    case "case.member_update":
      return failed
        ? `tried to change a member's role${failureNote}`
        : `changed ${onSelf ? "their own" : `${member()}'s`} role from ${roleLabel(details["from"])} to ${roleLabel(details["to"])}`;
    case "case.member_remove":
      if (failed) return `tried to remove a member${failureNote}`;
      return details["self"] === true
        ? "left the case"
        : `removed ${member()} (${roleLabel(details["role"])})`;
    // Only the view's ID is recorded (names may be person-related).
    case "view.create":
      return "saved a graph view";
    case "view.update":
      return "changed a saved graph view";
    case "view.delete":
      return "deleted a saved graph view";
    case "result_cache.clear":
      return "cleared the case's cached Shuttle results";
    case "scan_run.create": {
      const name = text(details["profile_name"]) ?? "a profile";
      if (failed) return `tried to run ${name}${failureNote}`;
      const count = typeof details["jobs"] === "number" ? details["jobs"] : null;
      return count === null
        ? `ran ${name}`
        : `ran ${name} (${String(count)} job${count === 1 ? "" : "s"})`;
    }
    case "scan_run.cancel":
      return "cancelled a profile run";
    case "authz.denied": {
      if (details["reason"] === "not_a_member") return "was refused access (not a member)";
      if (details["reason"] === "not_view_creator") {
        return "was refused: only a view's creator or an owner may change it";
      }
      const required = text(details["required"]);
      const what = required === null ? null : (CAPABILITY_LABELS[required] ?? required);
      return what === null ? "was refused an action" : `was refused: needs permission to ${what}`;
    }
    default:
      return event.action;
  }
}

export function actorName(event: AuditEvent): string {
  return event.actor_label ?? "Someone";
}
