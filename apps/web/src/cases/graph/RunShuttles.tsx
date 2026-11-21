import { useEffect, useId, useState } from "react";

import { RequestAbortedError } from "../../api/client";
import { getShuttlesFor, type ShuttleInfo } from "../../api/shuttles";
import type { EntityNode, EntityType } from "../../domain/entities";
import { describeCaseError } from "../errors";
import { SensitivityBadge } from "./EntityDetails";
import {
  ACTIVE_SHUTTLE_HINT,
  describeJobError,
  recentResultsHint,
  shuttleBlockedReason,
} from "./jobText";

export const NO_SHUTTLES_MESSAGE = "No installed Shuttle accepts this type of entity.";
export const USE_RECENT_LABEL = "Use recent results";
/** Read by screen readers before a Shuttle's usage note. */
export const USAGE_NOTE_LABEL = "Usage conditions";

/** A small "i" in a circle; decorative, the note's text says it all. */
function InfoIcon() {
  return (
    <svg
      className="shuttle-card__usage-icon"
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      focusable="false"
      data-testid="usage-note-icon"
    >
      <circle cx="8" cy="8" r="6.75" stroke="currentColor" strokeWidth="1.5" />
      <path d="M8 7.25v4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="8" cy="4.9" r="0.95" fill="currentColor" />
    </svg>
  );
}

type Load =
  | { kind: "loading"; type: EntityType }
  | { kind: "error"; type: EntityType; message: string }
  | { kind: "ready"; type: EntityType; shuttles: ShuttleInfo[] };

function ShuttleCard({
  shuttle,
  blocked,
  onRun,
}: {
  shuttle: ShuttleInfo;
  blocked: string | null;
  onRun: (shuttle: ShuttleInfo, options: { fresh: boolean }) => Promise<unknown>;
}) {
  const id = useId();
  const [busy, setBusy] = useState(false);
  const recentHint = recentResultsHint(shuttle);
  const [useRecent, setUseRecent] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);

  const run = () => {
    setBusy(true);
    setError(null);
    setStarted(false);
    onRun(shuttle, { fresh: recentHint !== null && !useRecent }).then(
      () => {
        setBusy(false);
        setStarted(true);
      },
      (caught: unknown) => {
        setBusy(false);
        setError(describeJobError(caught));
      },
    );
  };

  return (
    <li className="shuttle-card" aria-labelledby={`${id}-title`}>
      <div className="shuttle-card__head">
        <strong id={`${id}-title`} className="shuttle-card__name">
          {shuttle.title}
        </strong>
      </div>
      <p className="shuttle-card__description">{shuttle.description}</p>
      <p className="shuttle-card__badges">
        <span className="mode-badge" data-mode={shuttle.mode}>
          {shuttle.mode === "passive" ? "Passive" : "Active"}
        </span>
        {shuttle.network && (
          <span className="mode-badge" data-network="true" title="Makes network requests">
            Network
          </span>
        )}
        <SensitivityBadge sensitivity={shuttle.sensitivity} />
      </p>
      {shuttle.usage_note && (
        <p className="shuttle-card__usage">
          <InfoIcon />
          <span>
            <span className="visually-hidden">{USAGE_NOTE_LABEL}: </span>
            {shuttle.usage_note}
          </span>
        </p>
      )}
      {shuttle.mode === "active" && <p className="field__hint">{ACTIVE_SHUTTLE_HINT}</p>}
      {(shuttle.terms_url ?? shuttle.homepage) && (
        <p className="shuttle-card__links">
          {shuttle.terms_url && (
            <a href={shuttle.terms_url} target="_blank" rel="noopener noreferrer">
              Terms
            </a>
          )}
          {shuttle.homepage && (
            <a href={shuttle.homepage} target="_blank" rel="noopener noreferrer">
              Homepage
            </a>
          )}
        </p>
      )}
      {recentHint !== null && blocked === null && (
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
            {recentHint}
          </p>
        </div>
      )}
      {blocked !== null && (
        <p className="field__hint" id={`${id}-blocked`}>
          {blocked}
        </p>
      )}
      <div className="button-row">
        <button
          type="button"
          className="button"
          disabled={blocked !== null || busy}
          aria-describedby={blocked !== null ? `${id}-blocked` : undefined}
          onClick={run}
        >
          {busy ? "Starting…" : `Run ${shuttle.title}`}
        </button>
        {started && (
          <span className="shuttle-card__started" role="status">
            Queued. Follow it under Jobs.
          </span>
        )}
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}

export interface RunShuttlesProps {
  entity: EntityNode;
  allowPersonEntities: boolean;
  /** `fresh`: run even if recent results are cached (the user unticked "Use recent results"). */
  onRun: (
    shuttle: ShuttleInfo,
    entity: EntityNode,
    options: { fresh: boolean },
  ) => Promise<unknown>;
}

/** The Shuttles that accept the selected entity's type, each with a Run button. */
export function RunShuttles({ entity, allowPersonEntities, onRun }: RunShuttlesProps) {
  const [load, setLoad] = useState<Load>({ kind: "loading", type: entity.type });

  useEffect(() => {
    let current = true;
    getShuttlesFor(entity.type).then(
      (shuttles) => {
        if (current) setLoad({ kind: "ready", type: entity.type, shuttles });
      },
      (caught: unknown) => {
        if (!current || caught instanceof RequestAbortedError) return;
        setLoad({ kind: "error", type: entity.type, message: describeCaseError(caught) });
      },
    );
    return () => {
      current = false;
    };
  }, [entity.type]);

  const shown: Load = load.type === entity.type ? load : { kind: "loading", type: entity.type };

  return (
    <section className="run-shuttles" aria-labelledby="run-shuttles-title">
      <h3 id="run-shuttles-title" className="panel__section">
        Run a Shuttle
      </h3>
      {shown.kind === "loading" && <p className="table__muted">Loading Shuttles…</p>}
      {shown.kind === "error" && (
        <p className="form-error" role="alert">
          {shown.message}
        </p>
      )}
      {shown.kind === "ready" && shown.shuttles.length === 0 && (
        <p className="table__muted">{NO_SHUTTLES_MESSAGE}</p>
      )}
      {shown.kind === "ready" && shown.shuttles.length > 0 && (
        <ul className="shuttle-list">
          {shown.shuttles.map((shuttle) => (
            <ShuttleCard
              key={`${entity.id}|${shuttle.name}`}
              shuttle={shuttle}
              blocked={shuttleBlockedReason(shuttle, allowPersonEntities)}
              onRun={(chosen, options) => onRun(chosen, entity, options)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
