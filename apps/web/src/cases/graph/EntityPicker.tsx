import { useId, useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";

import { ENTITY_TYPE_INFO, type EntityNode } from "../../domain/entities";
import { searchNodes } from "../../graph/view";

const MAX_OPTIONS = 8;

export interface EntityPickerProps {
  label: string;
  nodes: readonly EntityNode[];
  /** The chosen entity's id, or null. */
  value: string | null;
  onChange: (id: string | null) => void;
  error?: string | undefined;
}

/** Picks one of the case's entities by typing part of its value (a combobox). */
export function EntityPicker({ label, nodes, value, onChange, error }: EntityPickerProps) {
  const chosen = useMemo(() => nodes.find((node) => node.id === value) ?? null, [nodes, value]);
  const [query, setQuery] = useState(chosen?.label ?? "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const id = useId();
  const listId = `${id}-list`;
  const optionId = (index: number) => `${id}-option-${index}`;

  const results = useMemo(() => {
    if (chosen !== null && query === chosen.label) return [];
    return query.trim() === ""
      ? nodes.slice(0, MAX_OPTIONS)
      : searchNodes(nodes, query, new Set(), MAX_OPTIONS);
  }, [nodes, query, chosen]);
  const expanded = open && (results.length > 0 || query.trim() !== "");
  const activeIndex = Math.min(active, Math.max(results.length - 1, 0));

  const pick = (node: EntityNode) => {
    onChange(node.id);
    setQuery(node.label);
    setOpen(false);
    setActive(0);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setOpen(true);
        if (results.length > 0) setActive((activeIndex + 1) % results.length);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (results.length > 0) setActive((activeIndex - 1 + results.length) % results.length);
        break;
      case "Enter": {
        const choice = expanded ? results[activeIndex] : undefined;
        if (choice) {
          event.preventDefault();
          pick(choice);
        }
        break;
      }
      case "Escape":
        if (expanded) {
          // Close the list, not the dialog around it.
          event.preventDefault();
          event.nativeEvent.stopImmediatePropagation();
          setOpen(false);
        }
        break;
    }
  };

  const describedBy = [chosen ? `${id}-chosen` : null, error ? `${id}-error` : null]
    .filter((part) => part !== null)
    .join(" ");

  return (
    <div className="field picker">
      <label className="field__label" htmlFor={`${id}-input`}>
        {label}
      </label>
      <input
        id={`${id}-input`}
        className="field__input"
        type="text"
        placeholder="Type to search this case's entities"
        autoComplete="off"
        spellCheck={false}
        role="combobox"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy === "" ? undefined : describedBy}
        aria-activedescendant={expanded && results.length > 0 ? optionId(activeIndex) : undefined}
        value={query}
        onFocus={() => {
          setOpen(true);
        }}
        onBlur={() => {
          setOpen(false);
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          setActive(0);
          if (value !== null) onChange(null);
        }}
        onKeyDown={onKeyDown}
      />
      <ul
        id={listId}
        role="listbox"
        aria-label={`${label} options`}
        className="search__results picker__results"
        hidden={!expanded}
      >
        {results.map((node, index) => (
          <li
            key={node.id}
            id={optionId(index)}
            role="option"
            aria-selected={index === activeIndex}
            className="search__option"
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
        {expanded && results.length === 0 && (
          <li className="search__empty" role="presentation">
            No matching entities
          </li>
        )}
      </ul>
      {chosen && (
        <p id={`${id}-chosen`} className="field__hint">
          {ENTITY_TYPE_INFO[chosen.type].label}: <code>{chosen.id}</code>
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="field__error">
          {error}
        </p>
      )}
    </div>
  );
}
