import { useEffect, useRef, type KeyboardEvent } from "react";

import { ENTITY_TYPE_INFO, type EntityType } from "../domain/entities";
import type { Point } from "./geometry";

export interface NodeContextMenuProps {
  /** Where it opens (pixels in the canvas). */
  at: Point;
  /** The canvas's size, to keep the menu inside it. */
  bounds: { width: number; height: number };
  label: string;
  type: EntityType;
  pinned: boolean;
  onTogglePin: () => void;
  onSelectNeighbours: () => void;
  onHideType: () => void;
  onClose: () => void;
}

const MENU_WIDTH = 220;
const MENU_HEIGHT = 140;

/**
 * A node's menu (right-click, or long press on a touch screen): pin or
 * unpin, select its neighbours, hide its type. Focus moves into it; arrow
 * keys move between items; Escape or a click elsewhere closes it.
 */
export function NodeContextMenu({
  at,
  bounds,
  label,
  type,
  pinned,
  onTogglePin,
  onSelectNeighbours,
  onHideType,
  onClose,
}: NodeContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) closeRef.current();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  const choose = (action: () => void) => () => {
    onClose();
    action();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const index = items.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
    }
  };

  const maxX = bounds.width - MENU_WIDTH - 8;
  const maxY = bounds.height - MENU_HEIGHT - 8;

  return (
    <div
      ref={menuRef}
      className="menu menu--context"
      role="menu"
      tabIndex={-1}
      aria-label={`${ENTITY_TYPE_INFO[type].label} ${label}`}
      style={{ left: Math.max(8, Math.min(at.x, maxX)), top: Math.max(8, Math.min(at.y, maxY)) }}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => {
        event.preventDefault();
      }}
    >
      <button type="button" className="menu__item" role="menuitem" onClick={choose(onTogglePin)}>
        {pinned ? "Unpin" : "Pin"}
      </button>
      <button
        type="button"
        className="menu__item"
        role="menuitem"
        onClick={choose(onSelectNeighbours)}
      >
        Select neighbours
      </button>
      <button type="button" className="menu__item" role="menuitem" onClick={choose(onHideType)}>
        Hide {ENTITY_TYPE_INFO[type].plural}
      </button>
    </div>
  );
}
