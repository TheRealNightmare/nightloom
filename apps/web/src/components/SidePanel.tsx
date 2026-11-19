import type { CSSProperties, ReactNode } from "react";

import {
  ENTITY_TYPE_INFO,
  relationLabel,
  type EntityNode,
  type PropertyValue,
  type Provenance,
} from "../domain/entities";
import { formatConfidence, formatTimestamp } from "../domain/format";
import type { Connection, TypeCount } from "../graph/view";

/** What the panel shows about the whole graph when nothing is selected. */
export interface GraphSummary {
  nodes: number;
  edges: number;
  types: readonly TypeCount[];
}

export interface SidePanelProps {
  selected: EntityNode | null;
  connections: readonly Connection[];
  /** Shown when nothing is selected. */
  overview: ReactNode;
  /** The selected entity's details; defaults to its id, properties and provenance. */
  renderEntity?: (entity: EntityNode) => ReactNode;
  /** Shown under the entity's details (e.g. collapsing a group around it). */
  extra?: ReactNode;
  onSelect: (node: string) => void;
  onClear: () => void;
}

export function TypeChip({ type }: { type: EntityNode["type"] }) {
  const info = ENTITY_TYPE_INFO[type];
  return (
    <span className="type-chip" style={{ "--swatch": info.color } as CSSProperties}>
      <span className="swatch" aria-hidden="true" />
      {info.label}
    </span>
  );
}

export function TypeSummary({ types }: { types: GraphSummary["types"] }) {
  return (
    <ul className="type-summary">
      {types.map(({ type, count }) => (
        <li key={type}>
          <TypeChip type={type} />
          <span>{count}</span>
        </li>
      ))}
    </ul>
  );
}

function formatProperty(value: PropertyValue): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}

export function PropertiesSection({
  properties,
}: {
  properties: EntityNode["properties"] | undefined;
}) {
  if (!properties || Object.keys(properties).length === 0) return null;
  return (
    <section>
      <h3 className="panel__section">Properties</h3>
      <dl className="facts">
        {Object.entries(properties).map(([key, value]) => (
          <div key={key} className="facts__row">
            <dt>{key.replaceAll("_", " ")}</dt>
            <dd>{formatProperty(value)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function Timestamp({ iso }: { iso: string }) {
  return <time dateTime={iso}>{formatTimestamp(iso)}</time>;
}

export function Confidence({ value }: { value: number }) {
  return (
    <span className="confidence">
      <meter min={0} max={1} value={value} aria-hidden="true" />
      {formatConfidence(value)}
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
          <Timestamp iso={provenance.first_seen} />
        </dd>
      </div>
      <div className="facts__row">
        <dt>Last seen</dt>
        <dd>
          <Timestamp iso={provenance.last_seen} />
        </dd>
      </div>
      <div className="facts__row">
        <dt>Confidence</dt>
        <dd>
          <Confidence value={provenance.confidence} />
        </dd>
      </div>
    </dl>
  );
}

/** The default details: id, properties and provenance. */
export function EntityBody({ entity }: { entity: EntityNode }) {
  return (
    <>
      <p className="panel__id">
        <code>{entity.id}</code>
      </p>
      <PropertiesSection properties={entity.properties} />
      <section>
        <h3 className="panel__section">Provenance</h3>
        <ProvenanceList provenance={entity} />
      </section>
    </>
  );
}

const defaultRenderEntity = (entity: EntityNode) => <EntityBody entity={entity} />;

export function SidePanel({
  selected,
  connections,
  overview,
  renderEntity = defaultRenderEntity,
  extra = null,
  onSelect,
  onClear,
}: SidePanelProps) {
  return (
    <aside className="panel" aria-label="Details">
      {selected === null ? (
        overview
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

          {renderEntity(selected)}
          {extra}

          <section>
            <h3 className="panel__section">Connections ({connections.length})</h3>
            <ul className="connections">
              {connections.map(({ edge, direction, other }) => (
                <li key={edge.id}>
                  <span className="connections__relation">
                    {direction === "out" ? "→" : "←"} {relationLabel(edge.relation)}
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
