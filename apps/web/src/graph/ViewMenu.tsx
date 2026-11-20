import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import type { WheelSetting } from "./wheel";

export interface ViewMenuProps {
  minimap: boolean;
  onMinimapChange: (shown: boolean) => void;
  wheel: WheelSetting;
  onWheelChange: (setting: WheelSetting) => void;
  pinnedCount: number;
  /** Asks for confirmation first (the caller opens the dialog). */
  onUnpinAll: () => void;
}

const WHEEL_LABELS: Record<WheelSetting, string> = {
  auto: "Automatic",
  zoom: "Zoom",
  pan: "Pan",
};

/**
 * The View menu: the minimap, what the scroll wheel does, and unpinning
 * everything. A menu button with a list of menu items; arrow keys move
 * between them, Escape closes it and returns focus to the button.
 */
export function ViewMenu({
  minimap,
  onMinimapChange,
  wheel,
  onWheelChange,
  pinnedCount,
  onUnpinAll,
}: ViewMenuProps) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const wheelLabelId = useId();

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLElement>('[role^="menuitem"]')?.focus();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  const close = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = [
      ...(menuRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([disabled])') ??
        []),
    ];
    const index = items.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape" || event.key === "Tab") {
      if (event.key === "Escape") event.preventDefault();
      close();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      (event.key === "Home" ? items[0] : items.at(-1))?.focus();
    }
  };

  return (
    <div className="view-menu">
      <button
        ref={buttonRef}
        type="button"
        className="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => {
          setOpen((current) => !current);
        }}
      >
        View
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          className="menu"
          role="menu"
          tabIndex={-1}
          aria-label="View"
          onKeyDown={onKeyDown}
        >
          <button
            type="button"
            className="menu__item"
            role="menuitemcheckbox"
            aria-checked={minimap}
            onClick={() => {
              onMinimapChange(!minimap);
            }}
          >
            <span className="menu__check" aria-hidden="true">
              {minimap ? "✓" : ""}
            </span>
            Minimap
          </button>
          <div className="menu__separator" role="separator" />
          <div role="group" aria-labelledby={wheelLabelId}>
            <p id={wheelLabelId} className="menu__label">
              Scroll wheel
            </p>
            {(["auto", "zoom", "pan"] as const).map((setting) => (
              <button
                key={setting}
                type="button"
                className="menu__item"
                role="menuitemradio"
                aria-checked={wheel === setting}
                onClick={() => {
                  onWheelChange(setting);
                }}
              >
                <span className="menu__check" aria-hidden="true">
                  {wheel === setting ? "•" : ""}
                </span>
                {WHEEL_LABELS[setting]}
              </button>
            ))}
          </div>
          <div className="menu__separator" role="separator" />
          <button
            type="button"
            className="menu__item"
            role="menuitem"
            disabled={pinnedCount === 0}
            onClick={() => {
              setOpen(false);
              onUnpinAll();
            }}
          >
            <span className="menu__check" aria-hidden="true" />
            Unpin all{pinnedCount > 0 ? ` (${pinnedCount.toLocaleString("en")})` : ""}…
          </button>
        </div>
      )}
    </div>
  );
}
