import { useEffect, useId, useState, type SubmitEvent } from "react";

import { RequestAbortedError } from "../api/client";
import {
  createProfile,
  deleteProfile,
  listProfiles,
  updateProfile,
  type ProfileInput,
  type ScanProfile,
} from "../api/scans";
import { listAllShuttles, type ShuttleInfo } from "../api/shuttles";
import { useDocumentTitle } from "../app/documentTitle";
import { LoadError, Loading } from "../cases/Feedback";
import { Modal } from "../components/Modal";
import { ENTITY_TYPE_INFO, isEntityType } from "../domain/entities";
import { describeAdminError } from "./errors";

type Editing = { kind: "new" } | { kind: "edit"; profile: ScanProfile };

/** The form for a new profile, or for changing a custom one. */
function ProfileDialog({
  editing,
  shuttles,
  onSaved,
  onClose,
}: {
  editing: Editing;
  shuttles: readonly ShuttleInfo[];
  onSaved: (profile: ScanProfile) => void;
  onClose: () => void;
}) {
  const id = useId();
  const initial = editing.kind === "edit" ? editing.profile : null;
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [chosen, setChosen] = useState<string[]>(initial ? [...initial.shuttles] : []);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Active Shuttles run one at a time; visits run only where a case allows them.
  const passive = shuttles.filter((shuttle) => shuttle.mode !== "active");

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    if (name.trim() === "") {
      setError("Give the profile a name.");
      return;
    }
    if (chosen.length === 0) {
      setError("Choose at least one Shuttle.");
      return;
    }
    const input: ProfileInput = { name, description, shuttles: chosen };
    setPending(true);
    setError(null);
    try {
      onSaved(
        initial === null ? await createProfile(input) : await updateProfile(initial.id, input),
      );
    } catch (caught) {
      setError(describeAdminError(caught));
      setPending(false);
    }
  };

  return (
    <Modal
      title={initial === null ? "New scan profile" : `Edit ${initial.name}`}
      onClose={onClose}
      wide
    >
      <form
        noValidate
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <div className="field">
          <label className="field__label" htmlFor={`${id}-name`}>
            Name
          </label>
          <input
            id={`${id}-name`}
            className="field__input"
            maxLength={60}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
            }}
          />
        </div>
        <div className="field">
          <label className="field__label" htmlFor={`${id}-description`}>
            Description
          </label>
          <textarea
            id={`${id}-description`}
            className="field__input"
            maxLength={300}
            rows={2}
            value={description}
            onChange={(event) => {
              setDescription(event.target.value);
            }}
          />
        </div>
        <fieldset className="field">
          <legend className="field__label">Shuttles</legend>
          <p className="field__hint">
            They run in the order you tick them; each only on entity types it accepts. Active
            Shuttles cannot be in a profile until ownership verification is available.
          </p>
          {passive.map((shuttle) => (
            <label key={shuttle.name} className="checkbox choice">
              <input
                type="checkbox"
                checked={chosen.includes(shuttle.name)}
                onChange={(event) => {
                  const on = event.target.checked;
                  setChosen((current) =>
                    on ? [...current, shuttle.name] : current.filter((n) => n !== shuttle.name),
                  );
                }}
              />
              <span>
                {shuttle.title} <code>{shuttle.name}</code>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="form-error" role="alert">
          {error}
        </div>
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={pending}>
            {pending ? "Saving…" : "Save profile"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

const typeLabel = (type: string) => (isEntityType(type) ? ENTITY_TYPE_INFO[type].label : type);

/** /admin/profiles: built-in and custom scan profiles; admins add, change and delete custom ones. */
export function ProfilesPage() {
  useDocumentTitle("Scan profiles", "Admin");
  const [profiles, setProfiles] = useState<ScanProfile[] | null>(null);
  const [shuttles, setShuttles] = useState<ShuttleInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [deleting, setDeleting] = useState<ScanProfile | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      listProfiles({ signal: controller.signal }),
      listAllShuttles({ signal: controller.signal }),
    ]).then(
      ([loaded, installed]) => {
        setProfiles(loaded);
        setShuttles(installed);
        setLoadError(null);
      },
      (caught: unknown) => {
        if (!(caught instanceof RequestAbortedError)) setLoadError(describeAdminError(caught));
      },
    );
    return () => {
      controller.abort();
    };
  }, [attempt]);

  const remove = async (profile: ScanProfile) => {
    setDeleting(null);
    setError(null);
    setNotice(null);
    try {
      await deleteProfile(profile.id);
      setNotice(`Deleted “${profile.name}”.`);
      setAttempt((n) => n + 1);
    } catch (caught) {
      setError(describeAdminError(caught));
    }
  };

  return (
    <main className="page">
      <div className="page__header">
        <h1 className="page__title">Scan profiles</h1>
        <button
          type="button"
          className="button button--primary"
          disabled={profiles === null}
          onClick={() => {
            setEditing({ kind: "new" });
          }}
        >
          New profile
        </button>
      </div>
      <p className="section__hint">
        A profile runs several Shuttles on the selected entities at once. The built-in ones ship
        with Nightloom; every user can run the ones you add.
      </p>
      {notice !== null && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {profiles === null && loadError !== null ? (
        <LoadError
          message={loadError}
          onRetry={() => {
            setLoadError(null);
            setAttempt((n) => n + 1);
          }}
        />
      ) : (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {profiles === null && loadError === null && <Loading label="Loading profiles…" />}
      {profiles !== null && (
        <div className="table-scroll">
          <table className="table" aria-label="Scan profiles">
            <thead>
              <tr>
                <th scope="col">Profile</th>
                <th scope="col">Shuttles</th>
                <th scope="col">Runs on</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((profile) => (
                <tr key={profile.id}>
                  <td>
                    <strong>{profile.name}</strong>
                    {profile.built_in && <span className="table__muted"> · built in</span>}
                    {profile.description && (
                      <div className="table__muted">{profile.description}</div>
                    )}
                  </td>
                  <td>
                    {profile.shuttles.map((name, index) => (
                      <span key={name}>
                        {index > 0 && ", "}
                        <code>{name}</code>
                        {profile.missing_shuttles.includes(name) && (
                          <span className="table__muted"> (not installed)</span>
                        )}
                      </span>
                    ))}
                  </td>
                  <td>{profile.input_types.map(typeLabel).join(", ") || "—"}</td>
                  <td className="table__actions">
                    {!profile.built_in && (
                      <>
                        <button
                          type="button"
                          className="button button--ghost button--small"
                          aria-label={`Edit ${profile.name}`}
                          onClick={() => {
                            setEditing({ kind: "edit", profile });
                          }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="button button--ghost button--small"
                          aria-label={`Delete ${profile.name}`}
                          onClick={() => {
                            setDeleting(profile);
                          }}
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing !== null && (
        <ProfileDialog
          editing={editing}
          shuttles={shuttles}
          onClose={() => {
            setEditing(null);
          }}
          onSaved={(saved) => {
            setEditing(null);
            setNotice(`Saved “${saved.name}”.`);
            setAttempt((n) => n + 1);
          }}
        />
      )}
      {deleting !== null && (
        <Modal
          title={`Delete “${deleting.name}”?`}
          description="Runs that used it keep its name. This cannot be undone."
          onClose={() => {
            setDeleting(null);
          }}
        >
          <div className="dialog__actions">
            <button
              type="button"
              className="button"
              onClick={() => {
                setDeleting(null);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button--danger"
              onClick={() => {
                void remove(deleting);
              }}
            >
              Delete
            </button>
          </div>
        </Modal>
      )}
    </main>
  );
}
