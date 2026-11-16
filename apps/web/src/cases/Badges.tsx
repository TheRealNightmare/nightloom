import type { AuditOutcome, CaseRole, CaseStatus } from "../api/cases";
import { CASE_ROLE_LABELS, STATUS_LABELS } from "./labels";

export function StatusBadge({ status }: { status: CaseStatus }) {
  return (
    <span className="status-badge" data-status={status}>
      {STATUS_LABELS[status]}
    </span>
  );
}

export function CaseRoleBadge({ role }: { role: CaseRole }) {
  return (
    <span className="role-badge" data-case-role={role} title="Your role in this case">
      {CASE_ROLE_LABELS[role]}
    </span>
  );
}

const OUTCOME_LABELS: Record<AuditOutcome, string> = {
  success: "Success",
  failure: "Failed",
  denied: "Denied",
};

export function OutcomeBadge({ outcome }: { outcome: AuditOutcome }) {
  return (
    <span className="outcome-badge" data-outcome={outcome}>
      {OUTCOME_LABELS[outcome]}
    </span>
  );
}
