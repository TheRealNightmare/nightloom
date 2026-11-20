import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type SubmitEvent,
} from "react";

import { RateLimitedError, RequestAbortedError } from "../../api/client";
import {
  createView,
  deleteView,
  getView,
  listViews,
  updateView,
  VIEW_LIMITS,
  type SavedView,
  type ViewSummary,
} from "../../api/views";
import { Modal } from "../../components/Modal";
import { useSavedViewControls } from "../../graph/savedViewContext";
import {
  nameLength,
  snapshotToState,
  stateToSnapshot,
  viewLink,
  viewNameError,
  type ViewSnapshot,
} from "../../graph/viewSnapshot";
import { describeCaseError } from "../errors";
import { FormField } from "../FormField";
import { RelativeTime } from "../RelativeTime";

export const EMPTY_VIEWS = "No saved views yet.";
export const EMPTY_VIEWS_HINT = "Save the current view to share it with this case's members.";
export const VIEW_UNAVAILABLE =
  "That saved view isn't available. It may have been deleted, or it belongs to another case.";
export const NOT_DRAWN = "The graph isn't drawn yet. Try again in a moment.";
export const TOO_MANY_CHANGES = "Too many changes to saved views. Try again in a moment.";
/** How long a plain message stays ("Applied", with its Undo, stays until dismissed). */
export const TOAST_MS = 10_000;

export interface SavedViewsProps {
  caseId: string;
  /** Whether the user may save views (editors and owners, in an open case). */
  canSave: boolean;
  isOwner: boolean;
  /** The signed-in user (creators may change their own views). */
  userId: string | null;
  /** Whether the case has this entity (pins of others are skipped). */
  hasEntity: (id: string) => boolean;
  /** A view to apply once the graph is drawn (`?view=` in the URL). */
  deepLinkId: string | null;
  /** Called once the deep link was taken: remove it from the URL. */
  onDeepLinkTaken: () => void;
}

type Dialog =
  | { kind: "save" }
  | { kind: "rename"; view: ViewSummary }
  | { kind: "delete"; view: ViewSummary }
  | null;

type Toast =
  { kind: "applied"; name: string; previous: ViewSnapshot } | { kind: "message"; text: string };

function describe(error: unknown): string {
  if (error instanceof RateLimitedError) return TOO_MANY_CHANGES;
  return describeCaseError(error);
}

function authorText(view: ViewSummary): string {
  return view.created_by?.display_name ?? "a former member";
}

/**
 * The Views menu of a case graph: named views of the graph (camera, filters,
 * expanded groups and pins), shared with the case's members. Everyone may
 * apply one; editors and owners save views, and a view's creator or an owner
 * updates, renames or deletes it. Applying replaces the user's own pins with
 * the view's and can be undone from the message that follows.
 */
export function SavedViews({
  caseId,
  canSave,
  isOwner,
  userId,
  hasEntity,
  deepLinkId,
  onDeepLinkTaken,
}: SavedViewsProps) {
  const controls = useSavedViewControls();
  const [open, setOpen] = useState(false);
  const [views, setViews] = useState<ViewSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [more, setMore] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [announcement, setAnnouncement] = useState("");
  // A deep-linked view, fetched and waiting for the URL to drop it.
  const deepViewRef = useRef<SavedView | null>(null);
  const [deepViewArrived, setDeepViewArrived] = useState(0);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  // Promise callbacks use the latest controls (filters live in the URL, which changes).
  const controlsRef = useRef(controls);
  useEffect(() => {
    controlsRef.current = controls;
  });

  const mayChange = (view: ViewSummary) =>
    canSave && (isOwner || (userId !== null && view.created_by?.id === userId));

  const announce = useCallback((text: string) => {
    // Cleared first, so the same text twice is announced twice.
    setAnnouncement("");
    setTimeout(() => {
      setAnnouncement(text);
    }, 50);
  }, []);

  const reload = useCallback(async () => {
    try {
      setViews(await listViews(caseId));
      setLoadError(null);
    } catch (caught) {
      setLoadError(describe(caught));
    }
  }, [caseId]);

  // The list is loaded each time the menu opens (others may have saved views).
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    listViews(caseId, { signal: controller.signal }).then(
      (listed) => {
        setViews(listed);
        setLoadError(null);
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setLoadError(describe(caught));
      },
    );
    return () => {
      controller.abort();
    };
  }, [open, caseId]);

  const close = useCallback((focusButton = true) => {
    setOpen(false);
    setMore(null);
    if (focusButton) buttonRef.current?.focus();
  }, []);

  // V on the graph opens the menu.
  const [seenRequest, setSeenRequest] = useState(controls.openRequest);
  if (controls.openRequest !== seenRequest) {
    setSeenRequest(controls.openRequest);
    setOpen(true);
  }

  // Focus the first item when the menu opens (the panel itself when it has none).
  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])');
    (first ?? panel)?.focus();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panelRef.current?.contains(target) && !buttonRef.current?.contains(target)) {
        setOpen(false);
        setMore(null);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open, views]);

  // Messages go away by themselves; one with Undo waits for the user.
  useEffect(() => {
    if (toast?.kind !== "message") return;
    const timer = setTimeout(() => {
      setToast(null);
    }, TOAST_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [toast]);

  const applyView = useCallback(
    (view: SavedView) => {
      const current = controlsRef.current;
      const previous = current.capture();
      current.apply(stateToSnapshot(view.state, hasEntity));
      setToast({ kind: "applied", name: view.name, previous });
      announce(`Applied view “${view.name}”.`);
    },
    [hasEntity, announce],
  );

  const applyById = async (summary: ViewSummary) => {
    close();
    try {
      applyView(await getView(caseId, summary.id));
    } catch (caught) {
      setToast({ kind: "message", text: describe(caught) });
    }
  };

  const undo = (previous: ViewSnapshot, name: string) => {
    controlsRef.current.apply(previous);
    setToast(null);
    announce(`Undone: the view from before “${name}” is back.`);
  };

  // A deep link: once the graph is drawn, take it out of the URL and fetch the view…
  const takenLink = useRef<string | null>(null);
  useEffect(() => {
    if (deepLinkId === null || !controls.ready || takenLink.current === deepLinkId) return;
    takenLink.current = deepLinkId;
    onDeepLinkTaken();
    getView(caseId, deepLinkId).then(
      (view) => {
        deepViewRef.current = view;
        setDeepViewArrived((n) => n + 1);
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setToast({ kind: "message", text: VIEW_UNAVAILABLE });
      },
    );
  }, [deepLinkId, controls.ready, caseId, onDeepLinkTaken]);
  // …and apply it once the URL no longer has it (the filters go into the same URL).
  useEffect(() => {
    const view = deepViewRef.current;
    if (view === null || deepLinkId !== null) return;
    deepViewRef.current = null;
    applyView(view);
  }, [deepViewArrived, deepLinkId, applyView]);

  const currentState = () => snapshotToState(controlsRef.current.capture());

  const save = async (name: string): Promise<string | null> => {
    const state = currentState();
    if (state === null) return NOT_DRAWN;
    try {
      const created = await createView(caseId, name, state);
      setDialog(null);
      announce(`Saved view “${created.name}”.`);
      await reload();
      return null;
    } catch (caught) {
      return describe(caught);
    }
  };

  const rename = async (view: ViewSummary, name: string): Promise<string | null> => {
    try {
      const renamed = await updateView(caseId, view.id, { name });
      setDialog(null);
      announce(`Renamed the view to “${renamed.name}”.`);
      await reload();
      return null;
    } catch (caught) {
      return describe(caught);
    }
  };

  const updateWithCurrent = async (view: ViewSummary) => {
    close();
    const state = currentState();
    if (state === null) {
      setToast({ kind: "message", text: NOT_DRAWN });
      return;
    }
    try {
      await updateView(caseId, view.id, { state });
      const text = `Updated “${view.name}” with the current view.`;
      setToast({ kind: "message", text });
      announce(text);
    } catch (caught) {
      setToast({ kind: "message", text: describe(caught) });
    }
  };

  const copyLink = async (view: ViewSummary) => {
    close();
    const link = viewLink(window.location, view.id);
    try {
      await navigator.clipboard.writeText(link);
      const text = `Link to “${view.name}” copied.`;
      setToast({ kind: "message", text });
      announce(text);
    } catch {
      setToast({ kind: "message", text: `Couldn't copy the link. Copy it from here: ${link}` });
    }
  };

  const remove = async (view: ViewSummary): Promise<string | null> => {
    try {
      await deleteView(caseId, view.id);
      setDialog(null);
      announce(`Deleted view “${view.name}”.`);
      await reload();
      return null;
    } catch (caught) {
      return describe(caught);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const panel = panelRef.current;
    if (!panel) return;
    const items = [...panel.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])')];
    const active = document.activeElement as HTMLElement | null;
    const index = active ? items.indexOf(active) : -1;
    const inSubmenu = active?.closest("[data-submenu]") ?? null;
    if (event.key === "Escape") {
      event.preventDefault();
      if (inSubmenu && more !== null) {
        setMore(null);
        panel.querySelector<HTMLElement>(`[data-more="${more}"]`)?.focus();
        return;
      }
      close();
    } else if (event.key === "Tab") {
      close(false);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (items.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      (event.key === "Home" ? items[0] : items.at(-1))?.focus();
    } else if (event.key === "ArrowRight" && active?.dataset["more"] !== undefined) {
      event.preventDefault();
      setMore(active.dataset["more"]);
    } else if (event.key === "ArrowLeft" && inSubmenu && more !== null) {
      event.preventDefault();
      setMore(null);
      panel.querySelector<HTMLElement>(`[data-more="${more}"]`)?.focus();
    }
  };

  // Opening a submenu moves the focus into it.
  useEffect(() => {
    if (more === null) return;
    panelRef.current
      ?.querySelector<HTMLElement>('[data-submenu] [role="menuitem"]:not([disabled])')
      ?.focus();
  }, [more]);

  const empty = views !== null && views.length === 0;

  return (
    <div className="view-menu saved-views">
      <button
        ref={buttonRef}
        type="button"
        className="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-keyshortcuts="V"
        onClick={() => {
          if (open) close(false);
          else setOpen(true);
        }}
      >
        Views
      </button>
      {open && (
        // The panel handles the menu's keys for everything inside it.
        // eslint-disable-next-line jsx-a11y/no-static-element-interactions
        <div
          ref={panelRef}
          className="menu saved-views__panel"
          tabIndex={-1}
          aria-label="Saved views"
          onKeyDown={onKeyDown}
        >
          {views === null && loadError === null && (
            <p className="menu__label saved-views__status" role="status">
              Loading saved views…
            </p>
          )}
          {loadError !== null && (
            <p className="form-error saved-views__status" role="alert">
              {loadError}
            </p>
          )}
          {empty && (
            <p className="saved-views__empty">
              {EMPTY_VIEWS}
              {canSave ? ` ${EMPTY_VIEWS_HINT}` : ""}
            </p>
          )}
          <div id={menuId} role="menu" aria-label="Saved views" className="saved-views__menu">
            {(views ?? []).map((view) => (
              <div key={view.id} role="group" aria-label={view.name} className="saved-views__row">
                <button
                  type="button"
                  className="menu__item saved-views__apply"
                  role="menuitem"
                  aria-label={`Apply ${view.name}`}
                  onClick={() => {
                    void applyById(view);
                  }}
                >
                  <span className="saved-views__name">{view.name}</span>
                  <span className="saved-views__meta">
                    by {authorText(view)} · <RelativeTime iso={view.updated_at} />
                  </span>
                </button>
                {mayChange(view) && (
                  <button
                    type="button"
                    className="menu__item saved-views__more"
                    role="menuitem"
                    aria-haspopup="menu"
                    aria-expanded={more === view.id}
                    aria-label={`More actions for ${view.name}`}
                    data-more={view.id}
                    onClick={() => {
                      setMore((current) => (current === view.id ? null : view.id));
                    }}
                  >
                    <span aria-hidden="true">⋯</span>
                  </button>
                )}
                {more === view.id && (
                  <div
                    role="menu"
                    aria-label={`Actions for ${view.name}`}
                    className="saved-views__submenu"
                    data-submenu=""
                  >
                    <button
                      type="button"
                      className="menu__item"
                      role="menuitem"
                      onClick={() => {
                        void updateWithCurrent(view);
                      }}
                    >
                      Update with current view
                    </button>
                    <button
                      type="button"
                      className="menu__item"
                      role="menuitem"
                      onClick={() => {
                        close();
                        setDialog({ kind: "rename", view });
                      }}
                    >
                      Rename…
                    </button>
                    <button
                      type="button"
                      className="menu__item"
                      role="menuitem"
                      onClick={() => {
                        void copyLink(view);
                      }}
                    >
                      Copy link
                    </button>
                    <button
                      type="button"
                      className="menu__item saved-views__danger"
                      role="menuitem"
                      onClick={() => {
                        close();
                        setDialog({ kind: "delete", view });
                      }}
                    >
                      Delete…
                    </button>
                  </div>
                )}
              </div>
            ))}
            {canSave && (
              <>
                {(views?.length ?? 0) > 0 && <div className="menu__separator" role="separator" />}
                <button
                  type="button"
                  className="menu__item saved-views__save"
                  role="menuitem"
                  disabled={!controls.ready || (views?.length ?? 0) >= VIEW_LIMITS.viewsPerCase}
                  onClick={() => {
                    close();
                    setDialog({ kind: "save" });
                  }}
                >
                  Save current view…
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {toast !== null && (
        <div className="saved-views__toast" role="status">
          <span>{toast.kind === "applied" ? `Applied “${toast.name}”` : toast.text}</span>
          {toast.kind === "applied" && (
            <button
              type="button"
              className="link-button"
              onClick={() => {
                undo(toast.previous, toast.name);
              }}
            >
              Undo
            </button>
          )}
          <button
            type="button"
            className="link-button"
            aria-label="Dismiss"
            onClick={() => {
              setToast(null);
            }}
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
      )}
      <p className="visually-hidden" aria-live="polite" data-testid="views-announcer">
        {announcement}
      </p>

      {dialog?.kind === "save" && (
        <ViewNameDialog
          title="Save current view"
          description="Saves the camera, filters, expanded groups and pinned entities. Everyone in this case can apply it."
          submitLabel="Save view"
          pendingLabel="Saving…"
          onSubmit={save}
          onCancel={() => {
            setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "rename" && (
        <ViewNameDialog
          title="Rename view"
          initialName={dialog.view.name}
          submitLabel="Rename"
          pendingLabel="Renaming…"
          onSubmit={(name) => rename(dialog.view, name)}
          onCancel={() => {
            setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "delete" && (
        <DeleteViewDialog
          view={dialog.view}
          onConfirm={() => remove(dialog.view)}
          onCancel={() => {
            setDialog(null);
          }}
        />
      )}
    </div>
  );
}

interface ViewNameDialogProps {
  title: string;
  description?: string;
  initialName?: string;
  submitLabel: string;
  pendingLabel: string;
  /** Resolves with an error for the name field (the dialog stays open), or null when done. */
  onSubmit: (name: string) => Promise<string | null>;
  onCancel: () => void;
}

/** Asks for a view's name (1–80 characters, unique in the case). */
export function ViewNameDialog({
  title,
  description,
  initialName = "",
  submitLabel,
  pendingLabel,
  onSubmit,
  onCancel,
}: ViewNameDialogProps) {
  const [name, setName] = useState(initialName);
  const [error, setError] = useState<string | undefined>(undefined);
  const [touched, setTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const fieldRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    setTouched(true);
    const problem = viewNameError(name);
    setError(problem ?? undefined);
    if (problem !== null) {
      fieldRef.current?.focus();
      return;
    }
    setPending(true);
    const result = await onSubmit(name.trim());
    if (!mounted.current) return;
    setPending(false);
    if (result !== null) {
      setError(result);
      fieldRef.current?.focus();
    }
  };

  return (
    <Modal
      title={title}
      {...(description === undefined ? {} : { description })}
      onClose={onCancel}
      initialFocus={fieldRef}
    >
      <form
        noValidate
        aria-busy={pending}
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <FormField
          label="Name"
          error={error}
          extra={`${String(nameLength(name.trim()))} / ${String(VIEW_LIMITS.nameMax)}`}
        >
          {(control) => (
            <input
              {...control}
              ref={fieldRef}
              name="view_name"
              className="field__input"
              autoComplete="off"
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                if (touched) setError(viewNameError(event.target.value) ?? undefined);
              }}
            />
          )}
        </FormField>
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={pending}>
            {pending ? pendingLabel : submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** Confirms deleting a view, by name. */
function DeleteViewDialog({
  view,
  onConfirm,
  onCancel,
}: {
  view: ViewSummary;
  onConfirm: () => Promise<string | null>;
  onCancel: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const confirm = async () => {
    if (pending) return;
    setPending(true);
    const result = await onConfirm();
    if (result !== null) {
      setPending(false);
      setError(result);
    }
  };

  return (
    <Modal
      title={`Delete “${view.name}”?`}
      description="The view is removed for everyone in this case. Pins people already applied stay where they are."
      onClose={onCancel}
      initialFocus={cancelRef}
    >
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog__actions">
        <button ref={cancelRef} type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="button button--danger"
          disabled={pending}
          onClick={() => {
            void confirm();
          }}
        >
          {pending ? "Deleting…" : "Delete view"}
        </button>
      </div>
    </Modal>
  );
}
