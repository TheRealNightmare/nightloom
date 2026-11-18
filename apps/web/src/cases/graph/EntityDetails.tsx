import { useEffect, useId, useState, type ReactNode } from "react";

import { RequestAbortedError } from "../../api/client";
import { getEntity, type Schema } from "../../api/graph";
import { Confidence, PropertiesSection, Timestamp } from "../../components/SidePanel";
import type { EntityNode, Sensitivity } from "../../domain/entities";
import { describeAdmiralty, SENSITIVITY_LABELS } from "./rules";

export function SensitivityBadge({ sensitivity }: { sensitivity: Sensitivity }) {
  return (
    <span className="sensitivity-badge" data-sensitivity={sensitivity}>
      {SENSITIVITY_LABELS[sensitivity]}
    </span>
  );
}

/** "B2" with what it means on hover and focus (and as the accessible description). */
export function AdmiraltyRating({ schema, rating }: { schema: Schema; rating: string }) {
  const id = useId();
  const meaning = describeAdmiralty(schema, rating);
  if (meaning === null) return <code>{rating}</code>;
  return (
    <span className="admiralty">
      <button type="button" className="admiralty__code" aria-describedby={id}>
        {rating}
      </button>
      <span id={id} role="tooltip" className="admiralty__tooltip">
        Admiralty rating of the latest observation. Source reliability {meaning.reliability}.
        Information credibility {meaning.credibility}.
      </span>
    </span>
  );
}

type CopyState = "idle" | "copied" | "failed";

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<CopyState>("idle");
  useEffect(() => {
    if (state === "idle") return;
    const timer = setTimeout(() => {
      setState("idle");
    }, 2000);
    return () => {
      clearTimeout(timer);
    };
  }, [state]);

  const copy = async () => {
    try {
      // `clipboard` is missing outside secure contexts (plain-HTTP deployments).
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
      if (!navigator.clipboard) throw new Error("no clipboard");
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  };

  return (
    <button
      type="button"
      className="link-button copy-button"
      aria-label={label}
      onClick={() => {
        void copy();
      }}
    >
      <span aria-live="polite">
        {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
      </span>
    </button>
  );
}

function Neighbours({
  caseId,
  entityId,
  revision,
}: {
  caseId: string;
  entityId: string;
  revision: number;
}) {
  const [count, setCount] = useState<{ id: string; value: number | null } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getEntity(caseId, entityId, { signal: controller.signal }).then(
      (detail) => {
        setCount({ id: entityId, value: detail.neighbours });
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setCount({ id: entityId, value: null });
      },
    );
    return () => {
      controller.abort();
    };
  }, [caseId, entityId, revision]);

  const current = count?.id === entityId ? count : null;
  if (current === null) return <span className="table__muted">Loading…</span>;
  if (current.value === null) return <span className="table__muted">Unavailable</span>;
  return <span data-testid="neighbour-count">{current.value}</span>;
}

export interface EntityDetailsProps {
  caseId: string;
  schema: Schema;
  entity: EntityNode;
  /** Changes when the graph does, so the neighbour count is fetched again. */
  revision: number;
  canEdit: boolean;
  onDelete: (entity: EntityNode) => void;
  /** Shown after the provenance (the Run Shuttle section for editors). */
  children?: ReactNode;
}

/** The selected case entity in the side panel: key, sensitivity, provenance, neighbours. */
export function EntityDetails({
  caseId,
  schema,
  entity,
  revision,
  canEdit,
  onDelete,
  children,
}: EntityDetailsProps) {
  const details = entity.details;
  return (
    <>
      <p className="panel__id entity-key">
        <code>{entity.id}</code>
        <CopyButton text={entity.id} label="Copy key" />
      </p>
      <dl className="facts">
        {entity.sensitivity && (
          <div className="facts__row">
            <dt>Sensitivity</dt>
            <dd>
              <SensitivityBadge sensitivity={entity.sensitivity} />
            </dd>
          </div>
        )}
        <div className="facts__row">
          <dt>Neighbours</dt>
          <dd>
            <Neighbours caseId={caseId} entityId={entity.id} revision={revision} />
          </dd>
        </div>
      </dl>

      <PropertiesSection properties={entity.properties} />

      <section>
        <h3 className="panel__section">Provenance</h3>
        <dl className="facts">
          <div className="facts__row">
            <dt>First seen</dt>
            <dd>
              <Timestamp iso={entity.first_seen} />
            </dd>
          </div>
          <div className="facts__row">
            <dt>Last seen</dt>
            <dd>
              <Timestamp iso={entity.last_seen} />
            </dd>
          </div>
          {details && (
            <div className="facts__row">
              <dt>Observations</dt>
              <dd>{details.observation_count}</dd>
            </div>
          )}
          <div className="facts__row">
            <dt>Sources</dt>
            <dd>
              {(details?.sources ?? [entity.source]).map((source) => (
                <code key={source} className="source-tag">
                  {source}
                </code>
              ))}
            </dd>
          </div>
          <div className="facts__row">
            <dt>Max confidence</dt>
            <dd>
              <Confidence value={entity.confidence} />
            </dd>
          </div>
          {details?.admiralty && (
            <div className="facts__row">
              <dt>Admiralty</dt>
              <dd>
                <AdmiraltyRating schema={schema} rating={details.admiralty} />
              </dd>
            </div>
          )}
          {details && details.evidence_refs.length > 0 && (
            <div className="facts__row">
              <dt>Evidence</dt>
              <dd>
                {details.evidence_refs.map((ref) => (
                  <code key={ref} className="source-tag">
                    {ref}
                  </code>
                ))}
              </dd>
            </div>
          )}
          {details?.note && (
            <div className="facts__row">
              <dt>Note</dt>
              <dd className="facts__note">{details.note}</dd>
            </div>
          )}
        </dl>
      </section>

      {children}

      {canEdit && (
        <div className="button-row panel__actions">
          <button
            type="button"
            className="button button--danger"
            onClick={() => {
              onDelete(entity);
            }}
          >
            Delete entity
          </button>
        </div>
      )}
    </>
  );
}
