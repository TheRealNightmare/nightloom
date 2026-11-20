import { Modal } from "../components/Modal";
import { SHORTCUTS, type ShortcutGroup } from "./keyboard";

/** Every graph control, by input device (the "?" button and key). */
export function ShortcutsDialog({
  onClose,
  groups = SHORTCUTS,
}: {
  onClose: () => void;
  /** The 3D view has its own list. */
  groups?: readonly ShortcutGroup[];
}) {
  return (
    <Modal title="Graph controls" onClose={onClose} wide>
      <div className="shortcuts">
        {groups.map((group) => (
          <section key={group.title} className="shortcuts__group">
            <h3 className="panel__section">{group.title}</h3>
            <dl className="shortcuts__list">
              {group.shortcuts.map((shortcut) => (
                <div key={shortcut.description} className="shortcuts__row">
                  <dt>
                    {shortcut.keys.map((key, index) => (
                      <span key={key}>
                        {index > 0 && <span className="shortcuts__or"> or </span>}
                        <kbd>{key}</kbd>
                      </span>
                    ))}
                  </dt>
                  <dd>{shortcut.description}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
      <div className="button-row dialog__actions">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
