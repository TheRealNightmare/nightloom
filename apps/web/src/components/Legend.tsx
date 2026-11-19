import type { CSSProperties } from "react";

import { ENTITY_TYPE_INFO, type EntityType } from "../domain/entities";
import type { TypeCount } from "../graph/view";

export interface LegendProps {
  types: readonly TypeCount[];
  hiddenTypes: ReadonlySet<EntityType>;
  onToggle: (type: EntityType) => void;
  onShowAll: () => void;
}

/**
 * Entity-type legend; each entry toggles that type's visibility. After the
 * types, the graph's other marks (see src/graph/encoding.ts): a faint, thin
 * thread is a low-confidence or unconfirmed relationship, a brass outer ring
 * a pinned entity.
 */
export function Legend({ types, hiddenTypes, onToggle, onShowAll }: LegendProps) {
  return (
    <section className="legend" aria-labelledby="legend-title">
      <div className="legend__header">
        <h2 id="legend-title" className="overlay-title">
          Entity types
        </h2>
        {hiddenTypes.size > 0 && (
          <button type="button" className="link-button" onClick={onShowAll}>
            Show all
          </button>
        )}
      </div>
      <ul className="legend__list">
        {types.map(({ type, count }) => {
          const info = ENTITY_TYPE_INFO[type];
          const visible = !hiddenTypes.has(type);
          return (
            <li key={type}>
              <button
                type="button"
                className="legend__item"
                aria-pressed={visible}
                title={visible ? `Hide ${info.label}` : `Show ${info.label}`}
                onClick={() => {
                  onToggle(type);
                }}
                style={{ "--swatch": info.color } as CSSProperties}
              >
                <span className="swatch" aria-hidden="true" />
                <span className="legend__label">{info.label}</span>
                <span className="legend__count">{count}</span>
              </button>
            </li>
          );
        })}
      </ul>
      <ul className="legend__list legend__key" aria-label="Other marks">
        <li className="legend__mark">
          <span className="thread-swatch thread-swatch--faint" aria-hidden="true" />
          Low confidence
        </li>
        <li className="legend__mark">
          <span className="swatch swatch--pinned" aria-hidden="true" />
          Pinned
        </li>
      </ul>
    </section>
  );
}
