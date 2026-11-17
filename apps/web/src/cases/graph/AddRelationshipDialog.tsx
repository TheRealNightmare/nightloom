import { Fragment, useId, useMemo, useRef, useState, type SubmitEvent } from "react";

import { ConflictError, ForbiddenError, ValidationError } from "../../api/client";
import { createRelationship, type ApiRelationship, type Schema } from "../../api/graph";
import { Modal } from "../../components/Modal";
import { ENTITY_TYPE_INFO, relationLabel, type EntityNode } from "../../domain/entities";
import { describeCaseError, FORBIDDEN_MESSAGE } from "../errors";
import { FormField } from "../FormField";
import { counterText, length } from "../validation";
import { EntityPicker } from "./EntityPicker";
import { relationsFor } from "./rules";

export const PERSON_RELATIONSHIP_MESSAGE =
  "This case does not allow person-related entities, so they cannot be connected.";

export interface AddRelationshipDialogProps {
  caseId: string;
  schema: Schema;
  nodes: readonly EntityNode[];
  /** Pre-filled source, e.g. the selected entity. */
  initialSource?: string | null;
  onCreated: (relationship: ApiRelationship) => void;
  onCancel: () => void;
}

type Errors = Partial<Record<"source" | "target" | "type" | "note" | "form", string>>;

function errorsFrom(caught: unknown, personRelated: boolean): Errors {
  if (caught instanceof ValidationError) {
    const note = caught.messages.filter((m) => m.field === "note");
    const other = caught.messages.filter((m) => m.field !== "note");
    return {
      ...(note.length > 0 ? { note: note.map((m) => m.message).join(" ") } : {}),
      ...(other.length > 0 ? { form: other.map((m) => m.message).join(" ") } : {}),
    };
  }
  if (caught instanceof ForbiddenError) {
    return { form: personRelated ? PERSON_RELATIONSHIP_MESSAGE : FORBIDDEN_MESSAGE };
  }
  if (caught instanceof ConflictError) return { form: caught.message };
  return { form: describeCaseError(caught) };
}

/**
 * Connects two of the case's entities. Only the relationship types the
 * schema allows for that pair of entity types are offered.
 */
export function AddRelationshipDialog({
  caseId,
  schema,
  nodes,
  initialSource = null,
  onCreated,
  onCancel,
}: AddRelationshipDialogProps) {
  const [sourceId, setSourceId] = useState<string | null>(initialSource);
  const [targetId, setTargetId] = useState<string | null>(null);
  const [chosenType, setChosenType] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [pending, setPending] = useState(false);
  // Remounts the pickers after a swap so their text follows the new values.
  const [swaps, setSwaps] = useState(0);
  const typeId = useId();
  const submitRef = useRef<HTMLButtonElement>(null);

  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const source = sourceId === null ? undefined : byId.get(sourceId);
  const target = targetId === null ? undefined : byId.get(targetId);
  const allowed = useMemo(
    () => (source && target ? relationsFor(schema, source.type, target.type) : []),
    [schema, source, target],
  );
  // Keep the chosen type while it stays allowed; otherwise the first allowed one.
  const relation = allowed.find((item) => item.type === chosenType) ?? allowed[0];
  const noteMax = schema.manual_defaults.note_max_length;

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const local: Errors = {};
    if (!source) local.source = "Choose the entity the relationship starts from.";
    if (!target) local.target = "Choose the entity it points to.";
    if (source && target) {
      if (source.id === target.id) local.target = "Choose two different entities.";
      else if (!relation) local.type = "No relationship type connects these.";
    }
    if (relation?.note_required && note.trim() === "") {
      local.note = `${relationLabel(relation.type)} needs a note explaining the link.`;
    }
    if (length(note) > noteMax) local.note = `A note is at most ${String(noteMax)} characters.`;
    setErrors(local);
    if (Object.keys(local).length > 0 || !source || !target || !relation) return;
    setPending(true);
    try {
      onCreated(
        await createRelationship(caseId, {
          type: relation.type,
          source_key: source.id,
          target_key: target.id,
          note: note.trim() === "" ? null : note.trim(),
        }),
      );
    } catch (caught) {
      setPending(false);
      const personRelated = source.sensitivity === "person" || target.sensitivity === "person";
      setErrors(errorsFrom(caught, personRelated));
      submitRef.current?.focus();
    }
  };

  return (
    <Modal title="Add relationship" onClose={onCancel} wide>
      <form
        noValidate
        aria-busy={pending}
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <Fragment key={swaps}>
          <EntityPicker
            label="From"
            nodes={nodes}
            value={sourceId}
            onChange={(id) => {
              setSourceId(id);
              setErrors({});
            }}
            error={errors.source}
          />
          <EntityPicker
            label="To"
            nodes={nodes}
            value={targetId}
            onChange={(id) => {
              setTargetId(id);
              setErrors({});
            }}
            error={errors.target}
          />
        </Fragment>
        <div className="button-row picker__swap">
          <button
            type="button"
            className="link-button"
            onClick={() => {
              setSourceId(targetId);
              setTargetId(sourceId);
              setSwaps((n) => n + 1);
            }}
          >
            Swap direction
          </button>
        </div>

        <div className="field">
          <label className="field__label" htmlFor={typeId}>
            Relationship
          </label>
          <select
            id={typeId}
            className="field__input"
            disabled={allowed.length === 0}
            value={relation?.type ?? ""}
            aria-describedby={`${typeId}-hint`}
            aria-invalid={errors.type ? true : undefined}
            onChange={(event) => {
              setChosenType(event.target.value);
            }}
          >
            {allowed.length === 0 && <option value="">Choose both entities first</option>}
            {allowed.map((item) => (
              <option key={item.type} value={item.type}>
                {relationLabel(item.type)}
                {item.note_required ? " (needs a note)" : ""}
              </option>
            ))}
          </select>
          <p id={`${typeId}-hint`} className="field__hint">
            {source && target
              ? `Types allowed from ${ENTITY_TYPE_INFO[source.type].label} to ${ENTITY_TYPE_INFO[target.type].label}.`
              : "Only the types allowed between the two entities' types are offered."}
          </p>
          {errors.type && <p className="field__error">{errors.type}</p>}
        </div>

        <FormField
          label={relation?.note_required ? "Note (required)" : "Note (optional)"}
          hint={
            relation?.note_required
              ? "Say why these two are linked; prefer a more specific type when one fits."
              : undefined
          }
          error={errors.note}
          extra={counterText(length(note), noteMax)}
        >
          {(control) => (
            <textarea
              {...control}
              name="note"
              className="field__input field__textarea"
              rows={2}
              value={note}
              onChange={(event) => {
                setNote(event.target.value);
              }}
            />
          )}
        </FormField>

        {errors.form !== undefined && (
          <p className="form-error" role="alert">
            {errors.form}
          </p>
        )}
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            ref={submitRef}
            type="submit"
            className="button button--primary"
            disabled={pending}
          >
            {pending ? "Adding…" : "Add relationship"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
