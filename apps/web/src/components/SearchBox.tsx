import { useId, useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";

import { ENTITY_TYPE_INFO, type EntityNode, type EntityType } from "../domain/entities";
import { searchNodes } from "../graph/view";

export interface SearchBoxProps {
  /** The entities to search (e.g. those the filters show, collapsed groups included). */
  nodes: readonly EntityNode[];
  hiddenTypes?: ReadonlySet<EntityType>;
  onPick: (node: string) => void;
}

const NO_TYPES: ReadonlySet<EntityType> = new Set();

/** Finds an entity by label and focuses it (combobox with a listbox popup). */
export function SearchBox({ nodes, hiddenTypes = NO_TYPES, onPick }: SearchBoxProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const optionId = (index: number) => `${listId}-option-${index}`;

  const results = useMemo(
    () => searchNodes(nodes, query, hiddenTypes),
    [nodes, query, hiddenTypes],
  );
  const open = query.trim() !== "";
  const activeIndex = Math.min(active, Math.max(results.length - 1, 0));

  const pick = (node: EntityNode) => {
    onPick(node.id);
    setQuery("");
    setActive(0);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (results.length > 0) setActive((activeIndex + 1) % results.length);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (results.length > 0) setActive((activeIndex - 1 + results.length) % results.length);
        break;
      case "Enter": {
        const choice = results[activeIndex];
        if (choice) {
          event.preventDefault();
          pick(choice);
        }
        break;
      }
      case "Escape":
        setQuery("");
        setActive(0);
        break;
    }
  };

  return (
    <div className="search">
      <label htmlFor={`${listId}-input`} className="visually-hidden">
        Search entities
      </label>
      <input
        id={`${listId}-input`}
        className="search__input"
        type="search"
        placeholder="Search entities…"
        autoComplete="off"
        spellCheck={false}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && results.length > 0 ? optionId(activeIndex) : undefined}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
      />
      <ul
        id={listId}
        role="listbox"
        aria-label="Matching entities"
        className="search__results"
        hidden={!open}
      >
        {results.map((node, index) => (
          <li
            key={node.id}
            id={optionId(index)}
            role="option"
            aria-selected={index === activeIndex}
            className="search__option"
            // mousedown (not click) so the input keeps focus until the pick.
            onMouseDown={(event) => {
              event.preventDefault();
              pick(node);
            }}
            style={{ "--swatch": ENTITY_TYPE_INFO[node.type].color } as CSSProperties}
          >
            <span className="swatch" aria-hidden="true" />
            <span className="search__label">{node.label}</span>
            <span className="search__type">{ENTITY_TYPE_INFO[node.type].label}</span>
          </li>
        ))}
        {open && results.length === 0 && (
          <li className="search__empty" role="presentation">
            No matching entities
          </li>
        )}
      </ul>
    </div>
  );
}
