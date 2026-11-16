import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import type { User } from "../api/client";
import { useAuth } from "../auth/context";
import { ROLE_LABELS } from "../auth/messages";
import { ChangePasswordDialog } from "./ChangePasswordDialog";

export interface UserMenuProps {
  user: User;
}

/**
 * Top-bar account menu (menu button pattern): the user's name and role,
 * "Change password" and "Sign out". Escape closes it and returns focus to
 * the button.
 */
export function UserMenu({ user }: UserMenuProps) {
  const { logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const menuId = useId();
  const triggerId = useId();

  const items = () => [
    ...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []),
  ];

  // Focus the first item when the menu opens.
  useEffect(() => {
    if (open) items()[0]?.focus();
  }, [open]);

  // Close on a click outside.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  };

  const onMenuKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    const list = items();
    const index = list.indexOf(document.activeElement as HTMLElement);
    const move = (to: number) => {
      event.preventDefault();
      list[(to + list.length) % list.length]?.focus();
    };
    switch (event.key) {
      case "ArrowDown":
        move(index + 1);
        break;
      case "ArrowUp":
        move(index - 1);
        break;
      case "Home":
        move(0);
        break;
      case "End":
        move(list.length - 1);
        break;
      case "Escape":
        event.preventDefault();
        close(true);
        break;
      case "Tab":
        close(false);
        break;
    }
  };

  const signOut = async () => {
    setSigningOut(true);
    // The provider signs out locally even if this fails; this component unmounts.
    await logout();
  };

  return (
    <div className="user-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        className="user-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => {
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          } else if (event.key === "Escape" && open) {
            event.preventDefault();
            close(true);
          }
        }}
      >
        <span className="user-menu__name">{user.display_name}</span>
        <span className="role-badge" data-role={user.role}>
          {ROLE_LABELS[user.role]}
        </span>
        <span className="user-menu__caret" aria-hidden="true" />
      </button>
      {open && (
        <div className="user-menu__popup">
          <p className="user-menu__email" title={user.email}>
            {user.email}
          </p>
          <ul
            ref={menuRef}
            id={menuId}
            role="menu"
            aria-labelledby={triggerId}
            className="user-menu__list"
            onKeyDown={onMenuKeyDown}
          >
            <li role="none">
              <button
                type="button"
                role="menuitem"
                tabIndex={-1}
                className="user-menu__item"
                onClick={() => {
                  setOpen(false);
                  setDialogOpen(true);
                }}
              >
                Change password
              </button>
            </li>
            <li role="none">
              <button
                type="button"
                role="menuitem"
                tabIndex={-1}
                className="user-menu__item"
                disabled={signingOut}
                onClick={() => {
                  void signOut();
                }}
              >
                Sign out
              </button>
            </li>
          </ul>
        </div>
      )}
      {dialogOpen && (
        <ChangePasswordDialog
          onClose={() => {
            setDialogOpen(false);
            triggerRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}
