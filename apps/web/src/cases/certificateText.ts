/** Wording for Certificate Transparency monitoring (watches, alerts, audit events). */

const plural = (count: number, one: string, many: string) =>
  `${String(count)} ${count === 1 ? one : many}`;

/** "2 new certificates and 3 new names", "1 new certificate", "4 new names". */
export function newThings(certificates: number, names: number): string {
  const parts: string[] = [];
  if (certificates > 0) parts.push(plural(certificates, "new certificate", "new certificates"));
  if (names > 0) parts.push(plural(names, "new name", "new names"));
  return parts.length === 0 ? "nothing new" : parts.join(" and ");
}

export const CT_WATCHES_HINT =
  "Nightloom asks crt.sh every hour for new certificates of these domains and their subdomains, " +
  "as a crt.sh job in your name. The first check records what exists; after that, new " +
  "certificates and names are added to the graph and shown as an alert on the Graph tab.";

export const CT_NOT_IN_GRAPH_HINT =
  "Watch a domain that is in this case's graph (add it there first).";

/** One line on a watch's state, from its last check. */
export function watchStatus(item: {
  checking: boolean;
  checked_at: string | null;
  baseline_at: string | null;
  problem_message: string | null;
}): string {
  if (item.problem_message !== null) return item.problem_message;
  if (item.checking) return "Checking now.";
  if (item.checked_at === null) return "First check within a minute.";
  if (item.baseline_at === null) return "Waiting for a first successful check.";
  return "Watching: new certificates show as alerts on the Graph tab.";
}
