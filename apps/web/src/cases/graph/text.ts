import { can, type Case } from "../../api/cases";
import { STATUS_LABELS } from "../labels";

function plural(count: number, one: string, many: string): string {
  return `${count.toLocaleString("en")} ${count === 1 ? one : many}`;
}

/** "2 entities, 1 relationship". */
export function graphSummaryText(nodes: number, edges: number): string {
  return `${plural(nodes, "entity", "entities")}, ${plural(edges, "relationship", "relationships")}`;
}

/** Why the graph cannot be changed, or null when it can. */
export function readOnlyReason(value: Case): string | null {
  if (!can(value.my_role, "edit")) {
    return "You have view access to this case: you can explore its graph but not change it.";
  }
  if (value.status !== "open") {
    return `This case is ${STATUS_LABELS[value.status].toLowerCase()}, so its graph is read-only. An owner can reopen it in Settings.`;
  }
  return null;
}
