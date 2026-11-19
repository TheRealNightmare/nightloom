import { useId, useMemo } from "react";

import {
  SENSITIVITIES,
  type EntityNode,
  type InvestigationGraph,
  type Sensitivity,
} from "../domain/entities";
import {
  countSourceGroups,
  CREDIBILITY_GRADES,
  MAX_FOCUS_HOPS,
  RELIABILITY_GRADES,
  SEEN_PRESETS,
  type Credibility,
  type GraphFilters,
  type Reliability,
  type SeenFilter,
  type SeenPreset,
} from "../graph/filters";
import type { FiltersUpdate } from "../graph/useUrlFilters";

/** An Admiralty grade and its meaning (from `GET /schema`, when the page has it). */
export interface GradeOption {
  code: string;
  label: string;
}

export interface FiltersPanelProps {
  data: InvestigationGraph;
  filters: GraphFilters;
  onChange: FiltersUpdate;
  /** "Showing 120 of 1,340 entities · 3 filters active". */
  summary: string;
  activeCount: number;
  onClearAll: () => void;
  onClose: () => void;
  /** The selected entity, which focus mode can centre on. */
  selected: EntityNode | null;
  grades?: { reliability: readonly GradeOption[]; credibility: readonly GradeOption[] };
}

const SENSITIVITY_LABELS: Readonly<Record<Sensitivity, string>> = {
  infrastructure: "Infrastructure",
  organization: "Organization",
  person: "Person-related",
};

const PRESET_LABELS: Readonly<Record<SeenPreset, string>> = {
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
};

function toggled<T>(set: ReadonlySet<T>, item: T): Set<T> {
  const next = new Set(set);
  if (next.has(item)) next.delete(item);
  else next.add(item);
  return next;
}

function seenChoice(seen: SeenFilter): string {
  return seen.kind === "all" ? "all" : seen.kind === "preset" ? seen.preset : "range";
}

function gradeLabel(code: string, grades: readonly GradeOption[] | undefined): string {
  const label = grades?.find((grade) => grade.code === code)?.label;
  return label ? `${code} – ${label}` : code;
}

/** The Filters panel of the graph view. Entity types are filtered with the legend. */
export function FiltersPanel({
  data,
  filters,
  onChange,
  summary,
  activeCount,
  onClearAll,
  onClose,
  selected,
  grades,
}: FiltersPanelProps) {
  const id = useId();
  const sources = useMemo(() => countSourceGroups(data.nodes), [data]);
  const sensitivities = useMemo(() => {
    const present = new Set(data.nodes.map((node) => node.sensitivity));
    return SENSITIVITIES.filter((value) => present.has(value));
  }, [data]);
  const focusNode = useMemo(
    () =>
      filters.focus === null ? null : data.nodes.find((node) => node.id === filters.focus?.id),
    [data, filters.focus],
  );
  const set = (patch: Partial<GraphFilters>) => {
    onChange((current) => ({ ...current, ...patch }));
  };
  const seen = filters.seen;

  return (
    <section className="graph-filters" aria-labelledby={`${id}-title`}>
      <div className="graph-filters__header">
        <h2 id={`${id}-title`} className="overlay-title">
          Filters
        </h2>
        <button type="button" className="link-button" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="graph-filters__summary" role="status">
        {summary}
        {activeCount > 0 && (
          <>
            {" · "}
            <button type="button" className="link-button" onClick={onClearAll}>
              Clear all
            </button>
          </>
        )}
      </p>
      <p className="graph-filters__hint">Show or hide entity types with the legend.</p>

      {sources.length > 0 && (
        <fieldset className="graph-filters__group">
          <legend>Data sources</legend>
          {sources.map(({ group, count }) => (
            <label key={group} className="checkbox graph-filters__check">
              <input
                type="checkbox"
                checked={!filters.hiddenSources.has(group)}
                onChange={() => {
                  onChange((current) => ({
                    ...current,
                    hiddenSources: toggled(current.hiddenSources, group),
                  }));
                }}
              />
              <span>{group === "manual" ? "Added by hand" : group}</span>
              <span className="graph-filters__count">{count}</span>
            </label>
          ))}
        </fieldset>
      )}

      {sensitivities.length > 0 && (
        <fieldset className="graph-filters__group">
          <legend>Sensitivity</legend>
          {sensitivities.map((value) => (
            <label key={value} className="checkbox graph-filters__check">
              <input
                type="checkbox"
                checked={!filters.hiddenSensitivities.has(value)}
                onChange={() => {
                  onChange((current) => ({
                    ...current,
                    hiddenSensitivities: toggled(current.hiddenSensitivities, value),
                  }));
                }}
              />
              <span>{SENSITIVITY_LABELS[value]}</span>
            </label>
          ))}
        </fieldset>
      )}

      <fieldset className="graph-filters__group">
        <legend>Seen between</legend>
        <label className="graph-filters__field">
          <span className="visually-hidden">Time range</span>
          <select
            className="field__input"
            value={seenChoice(seen)}
            onChange={(event) => {
              const value = event.target.value;
              set({
                seen:
                  value === "all"
                    ? { kind: "all" }
                    : value === "range"
                      ? { kind: "range", from: null, to: null }
                      : { kind: "preset", preset: value as SeenPreset },
              });
            }}
          >
            <option value="all">All time</option>
            {SEEN_PRESETS.map((preset) => (
              <option key={preset} value={preset}>
                {PRESET_LABELS[preset]}
              </option>
            ))}
            <option value="range">Custom range</option>
          </select>
        </label>
        {seen.kind === "range" && (
          <div className="graph-filters__range">
            <label className="graph-filters__field">
              <span>From</span>
              <input
                className="field__input"
                type="date"
                value={seen.from ?? ""}
                onChange={(event) => {
                  set({ seen: { ...seen, from: event.target.value || null } });
                }}
              />
            </label>
            <label className="graph-filters__field">
              <span>To</span>
              <input
                className="field__input"
                type="date"
                value={seen.to ?? ""}
                onChange={(event) => {
                  set({ seen: { ...seen, to: event.target.value || null } });
                }}
              />
            </label>
          </div>
        )}
      </fieldset>

      <fieldset className="graph-filters__group">
        <legend>Rating</legend>
        <label className="graph-filters__field">
          <span>
            Minimum confidence: <strong>{Math.round(filters.minConfidence * 100)}%</strong>
          </span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={Math.round(filters.minConfidence * 100)}
            onChange={(event) => {
              set({ minConfidence: Number(event.target.value) / 100 });
            }}
          />
        </label>
        <label className="graph-filters__field">
          <span>Minimum source reliability</span>
          <select
            className="field__input"
            value={filters.minReliability ?? ""}
            onChange={(event) => {
              const value = event.target.value;
              set({ minReliability: value === "" ? null : (value as Reliability) });
            }}
          >
            <option value="">Any</option>
            {RELIABILITY_GRADES.slice(0, -1).map((code) => (
              <option key={code} value={code}>
                {gradeLabel(code, grades?.reliability)}
              </option>
            ))}
          </select>
        </label>
        <label className="graph-filters__field">
          <span>Minimum information credibility</span>
          <select
            className="field__input"
            value={filters.minCredibility === null ? "" : String(filters.minCredibility)}
            onChange={(event) => {
              const value = event.target.value;
              set({ minCredibility: value === "" ? null : (Number(value) as Credibility) });
            }}
          >
            <option value="">Any</option>
            {CREDIBILITY_GRADES.slice(0, -1).map((code) => (
              <option key={code} value={String(code)}>
                {gradeLabel(String(code), grades?.credibility)}
              </option>
            ))}
          </select>
        </label>
      </fieldset>

      <fieldset className="graph-filters__group">
        <legend>Focus</legend>
        {filters.focus === null ? (
          <>
            <button
              type="button"
              className="button"
              disabled={selected === null}
              onClick={() => {
                if (selected) set({ focus: { id: selected.id, hops: 1 } });
              }}
            >
              Focus on the selected entity
            </button>
            {selected === null && <p className="graph-filters__hint">Select an entity first.</p>}
          </>
        ) : (
          <div className="graph-filters__focus">
            <label className="graph-filters__field">
              <span>
                Show only entities within{" "}
                <select
                  className="field__input graph-filters__inline"
                  aria-label="Hops"
                  value={String(filters.focus.hops)}
                  onChange={(event) => {
                    const hops = Number(event.target.value);
                    onChange((current) =>
                      current.focus === null
                        ? current
                        : { ...current, focus: { ...current.focus, hops } },
                    );
                  }}
                >
                  {Array.from({ length: MAX_FOCUS_HOPS }, (_, i) => i + 1).map((hops) => (
                    <option key={hops} value={String(hops)}>
                      {hops}
                    </option>
                  ))}
                </select>{" "}
                {filters.focus.hops === 1 ? "hop" : "hops"} of{" "}
                <strong>{focusNode?.label ?? "an entity that is no longer in this graph"}</strong>
              </span>
            </label>
            <button
              type="button"
              className="link-button"
              onClick={() => {
                set({ focus: null });
              }}
            >
              Stop focusing
            </button>
          </div>
        )}
      </fieldset>

      <label className="checkbox graph-filters__check">
        <input
          type="checkbox"
          checked={filters.hideIsolated}
          onChange={(event) => {
            set({ hideIsolated: event.target.checked });
          }}
        />
        <span>Hide isolated entities</span>
      </label>
    </section>
  );
}
