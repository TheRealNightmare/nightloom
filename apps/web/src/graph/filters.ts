/**
 * Graph filters: which entities the view shows. Pure functions only, so every
 * rule is unit-tested without WebGL; GraphView feeds their result to sigma's
 * reducers.
 *
 * Filters apply to entities. A relationship is drawn only when both of its
 * ends are. The filter state round-trips through a URL query string
 * ({@link filtersFromParams} / {@link filtersToParams}), so a filtered view
 * can be shared and survives a reload.
 */
import {
  ENTITY_TYPES,
  SENSITIVITIES,
  type EntityNode,
  type EntityType,
  type InvestigationGraph,
  type Sensitivity,
} from "../domain/entities";

export const SEEN_PRESETS = ["24h", "7d", "30d"] as const;
export type SeenPreset = (typeof SEEN_PRESETS)[number];

/** Length of each preset in milliseconds. */
export const PRESET_MS: Readonly<Record<SeenPreset, number>> = {
  "24h": 24 * 3600 * 1000,
  "7d": 7 * 24 * 3600 * 1000,
  "30d": 30 * 24 * 3600 * 1000,
};

/** "Seen between": a preset ending now, or a range of whole days (UTC, either end open). */
export type SeenFilter =
  | { kind: "all" }
  | { kind: "preset"; preset: SeenPreset }
  | { kind: "range"; from: string | null; to: string | null };

/** Admiralty source reliability, best first (F: cannot be judged). */
export const RELIABILITY_GRADES = ["A", "B", "C", "D", "E", "F"] as const;
export type Reliability = (typeof RELIABILITY_GRADES)[number];
/** Admiralty information credibility, best first (6: cannot be judged). */
export const CREDIBILITY_GRADES = [1, 2, 3, 4, 5, 6] as const;
export type Credibility = (typeof CREDIBILITY_GRADES)[number];

export const MAX_FOCUS_HOPS = 3;

export interface GraphFilters {
  hiddenTypes: ReadonlySet<EntityType>;
  /** Source groups not shown: "manual" or a Shuttle's name (see {@link sourceGroup}). */
  hiddenSources: ReadonlySet<string>;
  hiddenSensitivities: ReadonlySet<Sensitivity>;
  seen: SeenFilter;
  /** 0–1; 0 shows everything. */
  minConfidence: number;
  /** Keep this grade and better; null shows everything. */
  minReliability: Reliability | null;
  minCredibility: Credibility | null;
  /** Show only what is at most `hops` relationships away from this entity. */
  focus: { id: string; hops: number } | null;
  hideIsolated: boolean;
}

export const EMPTY_FILTERS: GraphFilters = {
  hiddenTypes: new Set(),
  hiddenSources: new Set(),
  hiddenSensitivities: new Set(),
  seen: { kind: "all" },
  minConfidence: 0,
  minReliability: null,
  minCredibility: null,
  focus: null,
  hideIsolated: false,
};

// --- sources ---------------------------------------------------------------------------

/** `shuttle:dns@1.0.0` → `dns`; `manual` → `manual`; anything else as it is. */
export function sourceGroup(source: string): string {
  const match = /^shuttle:([^@]+)@/.exec(source);
  return match?.[1] ?? source;
}

/** Every source of an entity (its full list for case data, else the joined string). */
export function sourcesOf(node: EntityNode): readonly string[] {
  if (node.details) return node.details.sources;
  return node.source
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

export interface SourceGroupCount {
  group: string;
  count: number;
}

/** Source groups present in the data, "manual" first, then by name, with entity counts. */
export function countSourceGroups(nodes: readonly EntityNode[]): SourceGroupCount[] {
  const counts = new Map<string, number>();
  for (const node of nodes) {
    for (const group of new Set(sourcesOf(node).map(sourceGroup))) {
      counts.set(group, (counts.get(group) ?? 0) + 1);
    }
  }
  return [...counts]
    .map(([group, count]) => ({ group, count }))
    .sort(
      (a, b) =>
        Number(b.group === "manual") - Number(a.group === "manual") ||
        a.group.localeCompare(b.group),
    );
}

// --- admiralty -------------------------------------------------------------------------

/** `"B2"` → `{reliability: "B", credibility: 2}`; null when absent or malformed. */
export function parseAdmiralty(
  rating: string | null | undefined,
): { reliability: Reliability; credibility: Credibility } | null {
  const match = /^([A-F])([1-6])$/.exec(rating ?? "");
  if (!match?.[1] || !match[2]) return null;
  return {
    reliability: match[1] as Reliability,
    credibility: Number(match[2]) as Credibility,
  };
}

// --- time ------------------------------------------------------------------------------

const DAY_MS = 24 * 3600 * 1000;

/** The filter's window in epoch milliseconds (inclusive); null ends are open. */
export function seenWindow(
  seen: SeenFilter,
  now: number,
): { from: number | null; to: number | null } | null {
  switch (seen.kind) {
    case "all":
      return null;
    case "preset":
      return { from: now - PRESET_MS[seen.preset], to: null };
    case "range": {
      const from = seen.from === null ? null : Date.parse(`${seen.from}T00:00:00Z`);
      const to = seen.to === null ? null : Date.parse(`${seen.to}T00:00:00Z`) + DAY_MS - 1;
      return {
        from: from === null || Number.isNaN(from) ? null : from,
        to: to === null || Number.isNaN(to) ? null : to,
      };
    }
  }
}

/** Whether the entity was seen in the window: [first_seen, last_seen] overlaps it. */
function seenIn(node: EntityNode, window: { from: number | null; to: number | null }): boolean {
  const first = Date.parse(node.first_seen);
  const last = Date.parse(node.last_seen);
  if (window.from !== null && last < window.from) return false;
  if (window.to !== null && first > window.to) return false;
  return true;
}

// --- applying ----------------------------------------------------------------------------

export interface FilterContext {
  /** Epoch milliseconds the presets count back from. */
  now: number;
}

/** Whether the entity passes the filters that look only at the entity itself. */
export function passesEntityFilters(
  node: EntityNode,
  filters: GraphFilters,
  window: { from: number | null; to: number | null } | null,
): boolean {
  if (filters.hiddenTypes.has(node.type)) return false;
  if (node.sensitivity && filters.hiddenSensitivities.has(node.sensitivity)) return false;
  if (filters.hiddenSources.size > 0) {
    const groups = sourcesOf(node).map(sourceGroup);
    // Hidden only when every one of its sources is hidden.
    if (groups.length > 0 && groups.every((group) => filters.hiddenSources.has(group))) {
      return false;
    }
  }
  if (node.confidence < filters.minConfidence) return false;
  if (filters.minReliability !== null || filters.minCredibility !== null) {
    // An entity without a rating counts as "cannot be judged" (F6).
    const rating = parseAdmiralty(node.details?.admiralty) ?? {
      reliability: "F",
      credibility: 6,
    };
    if (
      filters.minReliability !== null &&
      RELIABILITY_GRADES.indexOf(rating.reliability) >
        RELIABILITY_GRADES.indexOf(filters.minReliability)
    ) {
      return false;
    }
    if (filters.minCredibility !== null && rating.credibility > filters.minCredibility) {
      return false;
    }
  }
  if (window !== null && !seenIn(node, window)) return false;
  return true;
}

/** Undirected adjacency over the given nodes (edges to other nodes are left out). */
export function adjacency(
  data: InvestigationGraph,
  among: ReadonlySet<string>,
): Map<string, Set<string>> {
  const neighbours = new Map<string, Set<string>>();
  for (const id of among) neighbours.set(id, new Set());
  for (const edge of data.edges) {
    if (edge.from === edge.to || !among.has(edge.from) || !among.has(edge.to)) continue;
    neighbours.get(edge.from)?.add(edge.to);
    neighbours.get(edge.to)?.add(edge.from);
  }
  return neighbours;
}

/** Ids within `hops` relationships of `start` (breadth-first, ignoring direction). */
export function withinHops(
  neighbours: ReadonlyMap<string, ReadonlySet<string>>,
  start: string,
  hops: number,
): Set<string> {
  const reached = new Set<string>([start]);
  let frontier = [start];
  for (let depth = 0; depth < hops && frontier.length > 0; depth += 1) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const neighbour of neighbours.get(id) ?? []) {
        if (reached.has(neighbour)) continue;
        reached.add(neighbour);
        next.push(neighbour);
      }
    }
    frontier = next;
  }
  return reached;
}

/**
 * The ids of the entities the view shows. Order: the per-entity filters, then
 * focus mode (hops counted through entities that passed them; the focused
 * entity itself always shows), then "hide isolated entities" (no relationship
 * to another shown entity).
 */
export function applyFilters(
  data: InvestigationGraph,
  filters: GraphFilters,
  { now }: FilterContext,
): Set<string> {
  const window = seenWindow(filters.seen, now);
  const ids = new Set<string>();
  for (const node of data.nodes) {
    if (passesEntityFilters(node, filters, window)) ids.add(node.id);
  }
  let visible = ids;
  const focus = filters.focus;
  if (focus !== null && data.nodes.some((node) => node.id === focus.id)) {
    visible.add(focus.id);
    visible = withinHops(adjacency(data, visible), focus.id, focus.hops);
  }
  if (filters.hideIsolated) {
    const neighbours = adjacency(data, visible);
    visible = new Set([...visible].filter((id) => (neighbours.get(id)?.size ?? 0) > 0));
  }
  return visible;
}

/** Whether the time filter limits anything (a custom range needs at least one end). */
export function isSeenActive(seen: SeenFilter): boolean {
  return (
    seen.kind === "preset" || (seen.kind === "range" && (seen.from !== null || seen.to !== null))
  );
}

/** How many filters are active (each kind counts once; the type filter once for any types). */
export function countActiveFilters(filters: GraphFilters): number {
  return [
    filters.hiddenTypes.size > 0,
    filters.hiddenSources.size > 0,
    filters.hiddenSensitivities.size > 0,
    isSeenActive(filters.seen),
    filters.minConfidence > 0,
    filters.minReliability !== null,
    filters.minCredibility !== null,
    filters.focus !== null,
    filters.hideIsolated,
  ].filter(Boolean).length;
}

/** "Showing 120 of 1,340 entities · 3 filters active". */
export function filterSummary(shown: number, total: number, active: number): string {
  const entities = `Showing ${shown.toLocaleString("en")} of ${total.toLocaleString("en")} ${
    total === 1 ? "entity" : "entities"
  }`;
  if (active === 0) return entities;
  return `${entities} · ${String(active)} ${active === 1 ? "filter" : "filters"} active`;
}

// --- URL query string -----------------------------------------------------------------------

/** The query parameters the filters use; anything else in the URL is left alone. */
export const FILTER_PARAMS = [
  "hide",
  "xsrc",
  "xsens",
  "seen",
  "conf",
  "rel",
  "cred",
  "focus",
  "hops",
  "isolated",
] as const;

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function list(params: URLSearchParams, name: string): string[] {
  return (params.get(name) ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

function parseSeen(value: string | null): SeenFilter {
  if (value === null) return { kind: "all" };
  if ((SEEN_PRESETS as readonly string[]).includes(value)) {
    return { kind: "preset", preset: value as SeenPreset };
  }
  if (!value.includes("~")) return { kind: "all" };
  // A range with neither end set is kept, so the date fields stay open.
  const [from = "", to = ""] = value.split("~");
  return { kind: "range", from: DATE.test(from) ? from : null, to: DATE.test(to) ? to : null };
}

/** Reads filters from a query string. Unknown or malformed values are ignored. */
export function filtersFromParams(params: URLSearchParams): GraphFilters {
  const confidence = Number(params.get("conf"));
  const reliability = params.get("rel");
  const credibility = Number(params.get("cred"));
  const focusId = params.get("focus");
  const hops = Number(params.get("hops") ?? "1");
  return {
    hiddenTypes: new Set(
      list(params, "hide").filter((type): type is EntityType =>
        (ENTITY_TYPES as readonly string[]).includes(type),
      ),
    ),
    hiddenSources: new Set(list(params, "xsrc")),
    hiddenSensitivities: new Set(
      list(params, "xsens").filter((value): value is Sensitivity =>
        (SENSITIVITIES as readonly string[]).includes(value),
      ),
    ),
    seen: parseSeen(params.get("seen")),
    minConfidence:
      Number.isFinite(confidence) && confidence > 0 ? Math.min(confidence, 100) / 100 : 0,
    minReliability:
      reliability !== null && (RELIABILITY_GRADES as readonly string[]).includes(reliability)
        ? (reliability as Reliability)
        : null,
    minCredibility: (CREDIBILITY_GRADES as readonly number[]).includes(credibility)
      ? (credibility as Credibility)
      : null,
    focus:
      focusId !== null && focusId !== ""
        ? {
            id: focusId,
            hops: Number.isInteger(hops) ? Math.min(Math.max(hops, 1), MAX_FOCUS_HOPS) : 1,
          }
        : null,
    hideIsolated: params.get("isolated") === "hide",
  };
}

/**
 * Writes filters into a copy of `base`, replacing only the filter parameters.
 * Inactive filters leave no parameter, so an unfiltered view has a clean URL.
 */
export function filtersToParams(
  filters: GraphFilters,
  base: URLSearchParams = new URLSearchParams(),
): URLSearchParams {
  const params = new URLSearchParams(base);
  for (const name of FILTER_PARAMS) params.delete(name);
  const set = (name: string, value: string | null) => {
    if (value !== null && value !== "") params.set(name, value);
  };
  set("hide", ENTITY_TYPES.filter((type) => filters.hiddenTypes.has(type)).join(","));
  set("xsrc", [...filters.hiddenSources].sort().join(","));
  set("xsens", SENSITIVITIES.filter((value) => filters.hiddenSensitivities.has(value)).join(","));
  const { seen } = filters;
  if (seen.kind === "preset") set("seen", seen.preset);
  if (seen.kind === "range") set("seen", `${seen.from ?? ""}~${seen.to ?? ""}`);
  if (filters.minConfidence > 0) set("conf", String(Math.round(filters.minConfidence * 100)));
  set("rel", filters.minReliability);
  set("cred", filters.minCredibility === null ? null : String(filters.minCredibility));
  if (filters.focus !== null) {
    set("focus", filters.focus.id);
    if (filters.focus.hops !== 1) set("hops", String(filters.focus.hops));
  }
  if (filters.hideIsolated) set("isolated", "hide");
  return params;
}
