/**
 * What a saved view restores, and its conversion to and from the API's
 * state (saved as version 2; version-1 views still load, as 2D). Pure functions only: GraphView captures and applies a
 * {@link ViewSnapshot}; the case's Views menu saves and loads
 * {@link ApiViewState}s.
 */
import { VIEW_LIMITS, type ApiCamera, type ApiFilters, type ApiViewState } from "../api/views";
import { ENTITY_TYPES, SENSITIVITIES, type EntityType, type Sensitivity } from "../domain/entities";
import {
  CREDIBILITY_GRADES,
  MAX_FOCUS_HOPS,
  RELIABILITY_GRADES,
  SEEN_PRESETS,
  type Credibility,
  type GraphFilters,
  type Reliability,
  type SeenFilter,
} from "./filters";
import { DEFAULT_DEPTH, isDepthMode, type Camera3D, type DepthMode, type ViewMode } from "./mode";
import type { Positions } from "./pins";

/** The camera in graph terms (see {@link ApiCamera}), so it means the same on every screen. */
export type CameraSnapshot = ApiCamera;

/** How the graph looks: what a saved view captures and applies. */
export interface ViewSnapshot {
  mode: ViewMode;
  /** What the third axis shows in 3D. */
  depth: DepthMode;
  /** Null while the graph is not drawn yet (in 3D: the 3D camera seen from the front). */
  camera: CameraSnapshot | null;
  /** The 3D camera (views saved in 3D only). */
  camera3d: Camera3D | null;
  filters: GraphFilters;
  /** Ids of the expanded groups. */
  expanded: string[];
  /** The pinned entities (entity key → [x, y]). */
  pins: Positions;
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;

function day(value: string | null): string | null {
  return value !== null && DAY.test(value) ? value : null;
}

export function filtersToApi(filters: GraphFilters): ApiFilters {
  const { seen } = filters;
  return {
    hidden_types: ENTITY_TYPES.filter((type) => filters.hiddenTypes.has(type)),
    hidden_sources: [...filters.hiddenSources]
      .filter((source) => source !== "" && source.length <= VIEW_LIMITS.sourceMax)
      .sort()
      .slice(0, VIEW_LIMITS.hiddenSources),
    hidden_sensitivities: SENSITIVITIES.filter((value) => filters.hiddenSensitivities.has(value)),
    seen:
      seen.kind === "range"
        ? { kind: "range", from: day(seen.from), to: day(seen.to) }
        : seen.kind === "preset"
          ? { kind: "preset", preset: seen.preset }
          : { kind: "all" },
    min_confidence: Math.min(Math.max(filters.minConfidence, 0), 1),
    min_reliability: filters.minReliability,
    min_credibility: filters.minCredibility,
    focus: filters.focus === null ? null : { key: filters.focus.id, hops: filters.focus.hops },
    hide_isolated: filters.hideIsolated,
  };
}

function seenFromApi(seen: ApiFilters["seen"]): SeenFilter {
  if (seen.kind === "preset" && SEEN_PRESETS.includes(seen.preset)) {
    return { kind: "preset", preset: seen.preset };
  }
  if (seen.kind === "range") return { kind: "range", from: day(seen.from), to: day(seen.to) };
  return { kind: "all" };
}

/** The filters of a saved view; values this version of the web does not know are ignored. */
export function filtersFromApi(filters: ApiFilters): GraphFilters {
  const hops = filters.focus?.hops ?? 1;
  return {
    hiddenTypes: new Set(
      filters.hidden_types.filter((type): type is EntityType =>
        (ENTITY_TYPES as readonly string[]).includes(type),
      ),
    ),
    hiddenSources: new Set(filters.hidden_sources),
    hiddenSensitivities: new Set(
      filters.hidden_sensitivities.filter((value): value is Sensitivity =>
        (SENSITIVITIES as readonly string[]).includes(value),
      ),
    ),
    seen: seenFromApi(filters.seen),
    minConfidence: Math.min(Math.max(filters.min_confidence, 0), 1),
    minReliability: (RELIABILITY_GRADES as readonly string[]).includes(
      filters.min_reliability ?? "",
    )
      ? (filters.min_reliability as Reliability)
      : null,
    minCredibility: (CREDIBILITY_GRADES as readonly number[]).includes(filters.min_credibility ?? 0)
      ? (filters.min_credibility as Credibility)
      : null,
    focus:
      filters.focus === null
        ? null
        : {
            id: filters.focus.key,
            hops: Number.isInteger(hops) ? Math.min(Math.max(hops, 1), MAX_FOCUS_HOPS) : 1,
          },
    hideIsolated: filters.hide_isolated,
  };
}

/** The API state of a snapshot (null while the camera is unknown: the graph is not drawn). */
export function snapshotToState(snapshot: ViewSnapshot): ApiViewState | null {
  if (snapshot.camera === null) return null;
  const positions: Record<string, [number, number]> = {};
  for (const [key, [x, y]] of Object.entries(snapshot.pins).slice(0, VIEW_LIMITS.positions)) {
    positions[key] = [x, y];
  }
  return {
    version: 2,
    mode: snapshot.mode,
    camera: { ...snapshot.camera },
    ...(snapshot.mode === "3d" && snapshot.camera3d
      ? {
          camera3d: {
            position: [...snapshot.camera3d.position],
            target: [...snapshot.camera3d.target],
            up: [...snapshot.camera3d.up],
          },
        }
      : {}),
    depth: snapshot.depth,
    filters: filtersToApi(snapshot.filters),
    groups: { expanded: [...new Set(snapshot.expanded)].slice(0, VIEW_LIMITS.expandedGroups) },
    positions,
  };
}

/**
 * The snapshot a saved view applies. Pins of entities the case does not have
 * (`known` says which it has) are skipped.
 */
export function stateToSnapshot(state: ApiViewState, known: (id: string) => boolean): ViewSnapshot {
  const pins: Positions = {};
  for (const [key, [x, y]] of Object.entries(state.positions)) {
    if (known(key)) pins[key] = [x, y];
  }
  const v2 = state.version === 2 ? state : null;
  const camera3d = v2?.camera3d ?? null;
  return {
    // A version-1 view is 2D.
    mode: v2?.mode ?? "2d",
    depth: v2 && isDepthMode(v2.depth) ? v2.depth : DEFAULT_DEPTH,
    camera: { ...state.camera },
    camera3d: camera3d
      ? { position: [...camera3d.position], target: [...camera3d.target], up: [...camera3d.up] }
      : null,
    filters: filtersFromApi(state.filters),
    expanded: [...state.groups.expanded],
    pins,
  };
}

// --- names ------------------------------------------------------------------------------

// Controls (C0, DEL, C1) and the line and paragraph separators, like the API.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;

/** Characters as the API counts them: code points, not UTF-16 units. */
export function nameLength(text: string): number {
  let count = 0;
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  for (const _ of text) count += 1;
  return count;
}

/** Why a view name would be refused, or null when it is fine (it is trimmed first). */
export function viewNameError(name: string): string | null {
  const trimmed = name.trim();
  if (trimmed === "") return "Enter a name.";
  if (nameLength(trimmed) > VIEW_LIMITS.nameMax) {
    return `Use at most ${String(VIEW_LIMITS.nameMax)} characters.`;
  }
  if (CONTROL.test(trimmed)) return "Remove line breaks, tabs and other control characters.";
  return null;
}

// --- links ------------------------------------------------------------------------------

export const VIEW_PARAM = "view";

/** The absolute link that opens `viewId` in the case graph at `location`. */
export function viewLink(location: Pick<Location, "origin" | "pathname">, viewId: string): string {
  const params = new URLSearchParams({ [VIEW_PARAM]: viewId });
  return `${location.origin}${location.pathname}?${params.toString()}`;
}
