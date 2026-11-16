import type { CSSProperties } from "react";

import { ENTITY_TYPE_INFO, type EntityNode, type Provenance } from "../domain/entities";
import { formatConfidence, formatTimestamp } from "../domain/format";
import type { Connection, TypeCount } from "../graph/view";

export interface SidePanelProps {
  selected: EntityNode | null;
  connections: readonly Connection[];
  summary: { nodes: number; edges: number; types: readonly TypeCount[] };
  onSelect: (node: string) => void;
  onClear: () => void;
}

function TypeChip({ type }: { type: EntityNode["type"] }) {
  const info = ENTITY_TYPE_INFO[type];
  return (
    <span className="type-chip" style={{ "--swatch": info.color } as CSSProperties}>
      <span className="swatch" aria-hidden="true" />
      {info.label}
    </span>
  );
}

function ProvenanceList({ provenance }: { provenance: Provenance }) {
  return (
    <dl className="facts">
      <div className="facts__row">
        <dt>Source</dt>
        <dd>
          <code>{provenance.source}</code>
        </dd>
      </div>
      <div className="facts__row">
        <dt>First seen</dt>
        <dd>
          <time dateTime={provenance.first_seen}>{formatTimestamp(provenance.first_seen)}</time>
        </dd>
      </div>
      <div className="facts__row">
        <dt>Last seen</dt>
        <dd>
          <time dateTime={provenance.last_seen}>{formatTimestamp(provenance.last_seen)}</time>
        </dd>
      </div>
      <div className="facts__row">
        <dt>Confidence</dt>
        <dd>
          <span className="confidence">
            <meter min={0} max={1} value={provenance.confidence} aria-hidden="true" />
            {formatConfidence(provenance.confidence)}
          </span>
        </dd>
      </div>
    </dl>
  );
}

function Overview({ summary }: { summary: SidePanelProps["summary"] }) {
  return (
    <>
      <h2 className="panel__title">Demo investigation</h2>
      <p className="panel__text">
        A fictional attack-surface map of <code>example.org</code>. Every name, address and AS
        number is reserved for documentation, so nothing here points at a real target.
      </p>
      <p className="panel__text">
        {summary.nodes} entities · {summary.edges} relationships
      </p>
      <ul className="type-summary">
        {summary.types.map(({ type, count }) => (
          <li key={type}>
            <TypeChip type={type} />
            <span>{count}</span>
          </li>
        ))}
      </ul>
      <p className="panel__hint">
        Hover a node to trace its threads, click it to see its details and provenance, or use the
        search box.
      </p>
    </>
  );
}

export function SidePanel({ selected, connections, summary, onSelect, onClear }: SidePanelProps) {
  return (
    <aside className="panel" aria-label="Details">
      {selected === null ? (
        <Overview summary={summary} />
      ) : (
        <article aria-labelledby="entity-title">
          <div className="panel__header">
            <TypeChip type={selected.type} />
            <button type="button" className="link-button" onClick={onClear}>
              Close
            </button>
          </div>
          <h2 id="entity-title" className="panel__title panel__title--entity">
            {selected.label}
          </h2>
          <p className="panel__id">
            <code>{selected.id}</code>
          </p>

          {selected.properties && Object.keys(selected.properties).length > 0 && (
            <section>
              <h3 className="panel__section">Properties</h3>
              <dl className="facts">
                {Object.entries(selected.properties).map(([key, value]) => (
                  <div key={key} className="facts__row">
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}

          <section>
            <h3 className="panel__section">Provenance</h3>
            <ProvenanceList provenance={selected} />
          </section>

          <section>
            <h3 className="panel__section">Connections ({connections.length})</h3>
            <ul className="connections">
              {connections.map(({ edge, direction, other }) => (
                <li key={edge.id}>
                  <span className="connections__relation">
                    {direction === "out" ? "→" : "←"} {edge.relation.replaceAll("_", " ")}
                  </span>
                  <button
                    type="button"
                    className="connections__target"
                    style={{ "--swatch": ENTITY_TYPE_INFO[other.type].color } as CSSProperties}
                    onClick={() => {
                      onSelect(other.id);
                    }}
                    title={`${edge.source} · ${formatConfidence(edge.confidence)} confidence`}
                  >
                    <span className="swatch" aria-hidden="true" />
                    {other.label}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </article>
      )}
    </aside>
  );
}
