import { useEffect, useId, useState } from "react";

import { RequestAbortedError } from "../../api/client";
import { getProfiles, MAX_RUN_ENTITIES, type ScanProfile, type ScanRun } from "../../api/scans";
import type { EntityNode } from "../../domain/entities";
import { describeCaseError } from "../errors";
import { USE_RECENT_LABEL } from "./RunShuttles";
import { describeJobError } from "./jobText";
import { PIVOT_HINT, PIVOT_INFRASTRUCTURE_HINT, PIVOT_LABEL, runStartedText } from "./runText";
import type { RunOptions } from "./useCaseRuns";

export const NO_PROFILES_MESSAGE = "No profile has a Shuttle for this type of entity.";
export const TOO_MANY_ENTITIES_MESSAGE = `A profile runs on at most ${String(MAX_RUN_ENTITIES)} entities. Select fewer.`;
export const PROFILE_RECENT_HINT =
  "Shuttles that keep their results reuse recent ones for the same entity instead of asking again.";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; profiles: ScanProfile[] };

export interface RunProfileProps {
  /** The entities to run on: the selected one, or a multi-selection. */
  entities: readonly EntityNode[];
  onRun: (profile: ScanProfile, entityKeys: string[], options: RunOptions) => Promise<ScanRun>;
  /** Whether the case's owners allow auto-pivot (offers "Pivot from here"). */
  allowPivot?: boolean;
}

/** Choose a scan profile and run its Shuttles on the selected entities. */
export function RunProfile({ entities, onRun, allowPivot = false }: RunProfileProps) {
  const id = useId();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [chosen, setChosen] = useState<string | null>(null);
  const [useRecent, setUseRecent] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    getProfiles().then(
      (profiles) => {
        if (current) setLoad({ kind: "ready", profiles });
      },
      (caught: unknown) => {
        if (!current || caught instanceof RequestAbortedError) return;
        setLoad({ kind: "error", message: describeCaseError(caught) });
      },
    );
    return () => {
      current = false;
    };
  }, []);

  const types = new Set(entities.map((entity) => entity.type));
  const usable =
    load.kind === "ready"
      ? load.profiles.filter((profile) => profile.input_types.some((type) => types.has(type)))
      : [];
  const profile = usable.find((item) => item.id === chosen) ?? usable[0] ?? null;
  const tooMany = entities.length > MAX_RUN_ENTITIES;
  const target =
    entities.length === 1 ? (entities[0]?.label ?? "") : `${String(entities.length)} entities`;
  const infrastructure = entities.every((entity) => entity.sensitivity === "infrastructure");

  const run = (pivot: boolean) => {
    if (profile === null) return;
    setBusy(true);
    setError(null);
    setStarted(null);
    onRun(
      profile,
      entities.map((entity) => entity.id),
      { fresh: !useRecent, pivot },
    ).then(
      (created) => {
        setBusy(false);
        setStarted(runStartedText(created));
      },
      (caught: unknown) => {
        setBusy(false);
        setError(describeJobError(caught));
      },
    );
  };

  return (
    <section className="run-profile" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`} className="panel__section">
        Run a profile
      </h3>
      {load.kind === "loading" && <p className="table__muted">Loading profiles…</p>}
      {load.kind === "error" && (
        <p className="form-error" role="alert">
          {load.message}
        </p>
      )}
      {load.kind === "ready" && profile === null && (
        <p className="table__muted">{NO_PROFILES_MESSAGE}</p>
      )}
      {profile !== null && (
        <>
          <div className="field">
            <label className="field__label" htmlFor={`${id}-profile`}>
              Profile
            </label>
            <select
              id={`${id}-profile`}
              className="field__input"
              value={profile.id}
              onChange={(event) => {
                setChosen(event.target.value);
                setStarted(null);
                setError(null);
              }}
            >
              {usable.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </div>
          {profile.description && <p className="run-profile__description">{profile.description}</p>}
          <p className="run-profile__shuttles">
            <span className="visually-hidden">Shuttles: </span>
            {profile.shuttles.map((name) => (
              <code
                key={name}
                className="run-profile__shuttle"
                data-missing={profile.missing_shuttles.includes(name) || undefined}
                title={profile.missing_shuttles.includes(name) ? "Not installed" : undefined}
              >
                {name}
              </code>
            ))}
          </p>
          <div className="shuttle-card__recent">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={useRecent}
                aria-describedby={`${id}-recent`}
                onChange={(event) => {
                  setUseRecent(event.target.checked);
                }}
              />
              <span>{USE_RECENT_LABEL}</span>
            </label>
            <p className="field__hint" id={`${id}-recent`}>
              {PROFILE_RECENT_HINT}
            </p>
          </div>
          {tooMany && (
            <p className="field__hint" id={`${id}-too-many`}>
              {TOO_MANY_ENTITIES_MESSAGE}
            </p>
          )}
          <div className="button-row">
            <button
              type="button"
              className="button"
              disabled={busy || tooMany}
              aria-describedby={tooMany ? `${id}-too-many` : undefined}
              onClick={() => {
                run(false);
              }}
            >
              {busy ? "Starting…" : `Run on ${target}`}
            </button>
            {allowPivot && (
              <button
                type="button"
                className="button button--ghost"
                disabled={busy || tooMany || !infrastructure}
                aria-describedby={`${id}-pivot`}
                onClick={() => {
                  run(true);
                }}
              >
                {PIVOT_LABEL}
              </button>
            )}
          </div>
          {allowPivot && (
            <p className="field__hint" id={`${id}-pivot`}>
              {infrastructure ? PIVOT_HINT : PIVOT_INFRASTRUCTURE_HINT}
            </p>
          )}
          {started !== null && (
            <p className="shuttle-card__started" role="status">
              {started} Follow it under Jobs.
            </p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </section>
  );
}
