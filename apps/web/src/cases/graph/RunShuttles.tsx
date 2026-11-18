import { useEffect, useId, useState } from "react";

import { RequestAbortedError } from "../../api/client";
import { getShuttlesFor, type ShuttleInfo } from "../../api/shuttles";
import type { EntityNode, EntityType } from "../../domain/entities";
import { describeCaseError } from "../errors";
import { SensitivityBadge } from "./EntityDetails";
import { describeJobError, shuttleBlockedReason } from "./jobText";

export const NO_SHUTTLES_MESSAGE = "No installed Shuttle accepts this type of entity.";

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
  onRun: (shuttle: ShuttleInfo) => Promise<unknown>;
}) {
  const id = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);

  const run = () => {
    setBusy(true);
    setError(null);
    setStarted(false);
    onRun(shuttle).then(
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
        <strong id={`${id}-title`}>{shuttle.title}</strong>
        <span className="shuttle-card__badges">
          <span className="mode-badge" data-mode={shuttle.mode}>
            {shuttle.mode === "passive" ? "Passive" : "Active"}
          </span>
          {shuttle.network && (
            <span className="mode-badge" data-network="true" title="Makes network requests">
              Network
            </span>
          )}
          <SensitivityBadge sensitivity={shuttle.sensitivity} />
        </span>
      </div>
      <p className="shuttle-card__description">{shuttle.description}</p>
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
      {blocked !== null && (
        <p className="field__hint" id={`${id}-blocked`}>
          {blocked}
        </p>
      )}
      <div className="button-row">
        <button
          type="button"
          className="button button--primary"
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
  onRun: (shuttle: ShuttleInfo, entity: EntityNode) => Promise<unknown>;
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
        Run Shuttle
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
              onRun={(chosen) => onRun(chosen, entity)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
