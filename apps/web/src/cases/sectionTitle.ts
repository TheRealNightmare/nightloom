/** The tab names in the browser tab's title ("Example case · Graph · Nightloom"). */
const SECTION_TITLES: Readonly<Record<string, string>> = {
  graph: "Graph",
  members: "Members",
  activity: "Activity",
  settings: "Settings",
};

/** The section of `/cases/:caseId/<section>/…`, as a title; unknown paths are "Page not found". */
export function sectionTitle(pathname: string, caseId: string): string | null {
  const prefix = `/cases/${caseId}`;
  const rest = pathname.startsWith(prefix) ? pathname.slice(prefix.length) : "";
  const section = rest.split("/").find((part) => part !== "") ?? "";
  if (section === "") return null;
  return SECTION_TITLES[section] ?? "Page not found";
}
