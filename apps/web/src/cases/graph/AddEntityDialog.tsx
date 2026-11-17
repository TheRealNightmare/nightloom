import { useId, useMemo, useRef, useState, type SubmitEvent } from "react";

import { ConflictError, ForbiddenError, ValidationError } from "../../api/client";
import { createEntity, type ApiEntity, type Schema } from "../../api/graph";
import { Modal } from "../../components/Modal";
import type { EntityType } from "../../domain/entities";
import { describeCaseError, FORBIDDEN_MESSAGE } from "../errors";
import { FormField } from "../FormField";
import { counterText, length } from "../validation";
import { entityTypeInfo, isTypeAllowed, typesBySensitivity } from "./rules";

export const PERSON_ENTITIES_MESSAGE = "This case does not allow person-related entities.";
export const PERSON_TYPES_DISABLED_HINT =
  "Person-related types are disabled because this case does not allow person entities. A case owner can allow them in Settings.";

export interface AddEntityDialogProps {
  caseId: string;
  schema: Schema;
  allowPersonEntities: boolean;
  onCreated: (entity: ApiEntity) => void;
  onCancel: () => void;
}

type Errors = Partial<Record<"value" | "note" | "phone_region" | "form", string>>;

function errorsFrom(caught: unknown, personRelated: boolean): Errors {
  if (caught instanceof ValidationError) {
    const errors: Errors = {};
    for (const { field, message } of caught.messages) {
      const key = field === "note" || field === "phone_region" ? field : "value";
      errors[key] = errors[key] === undefined ? message : `${errors[key]} ${message}`;
    }
    return errors;
  }
  if (caught instanceof ForbiddenError) {
    return { form: personRelated ? PERSON_ENTITIES_MESSAGE : FORBIDDEN_MESSAGE };
  }
  if (caught instanceof ConflictError) return { form: caught.message };
  return { form: describeCaseError(caught) };
}

/** Adds one entity by hand. The server normalizes the value and explains what it refuses. */
export function AddEntityDialog({
  caseId,
  schema,
  allowPersonEntities,
  onCreated,
  onCancel,
}: AddEntityDialogProps) {
  const groups = useMemo(() => typesBySensitivity(schema), [schema]);
  const firstAllowed =
    schema.entity_types.find((item) => isTypeAllowed(schema, item.type, allowPersonEntities))
      ?.type ?? "domain";
  const [type, setType] = useState<EntityType>(firstAllowed);
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [region, setRegion] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [pending, setPending] = useState(false);
  const valueRef = useRef<HTMLInputElement>(null);
  const selectRef = useRef<HTMLSelectElement>(null);
  const personHintId = useId();

  const info = entityTypeInfo(schema, type);
  const noteMax = schema.manual_defaults.note_max_length;
  const hasDisabled = !allowPersonEntities && groups.some((g) => g.sensitivity === "person");

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const local: Errors = {};
    if (value.trim() === "") local.value = "Enter a value.";
    if (length(note) > noteMax) local.note = `A note is at most ${String(noteMax)} characters.`;
    if (type === "phone" && region.trim() !== "" && !/^[A-Za-z]{2}$/.test(region.trim())) {
      local.phone_region = "Use a two-letter country code, e.g. GB.";
    }
    setErrors(local);
    if (Object.keys(local).length > 0) {
      valueRef.current?.focus();
      return;
    }
    setPending(true);
    try {
      const entity = await createEntity(caseId, {
        type,
        value: value.trim(),
        note: note.trim() === "" ? null : note.trim(),
        ...(type === "phone" && region.trim() !== ""
          ? { phone_region: region.trim().toUpperCase() }
          : {}),
      });
      onCreated(entity);
    } catch (caught) {
      setPending(false);
      setErrors(errorsFrom(caught, info?.sensitivity === "person"));
      valueRef.current?.focus();
    }
  };

  return (
    <Modal title="Add entity" onClose={onCancel} initialFocus={selectRef} wide>
      <form
        noValidate
        aria-busy={pending}
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <div className="field">
          <label className="field__label" htmlFor={`${personHintId}-type`}>
            Type
          </label>
          <select
            ref={selectRef}
            id={`${personHintId}-type`}
            className="field__input"
            value={type}
            aria-describedby={hasDisabled ? personHintId : undefined}
            onChange={(event) => {
              setType(event.target.value as EntityType);
              setErrors({});
            }}
          >
            {groups.map((group) => (
              <optgroup key={group.sensitivity} label={group.label}>
                {group.types.map((item) => {
                  const allowed = isTypeAllowed(schema, item.type, allowPersonEntities);
                  return (
                    <option key={item.type} value={item.type} disabled={!allowed}>
                      {allowed
                        ? item.display_name
                        : `${item.display_name} (not allowed in this case)`}
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </select>
          {hasDisabled && (
            <p id={personHintId} className="field__hint">
              {PERSON_TYPES_DISABLED_HINT}
            </p>
          )}
        </div>

        <FormField
          label="Value"
          hint={info ? `${info.hint} Example: ${info.example}` : undefined}
          error={errors.value}
        >
          {(control) => (
            <input
              {...control}
              ref={valueRef}
              name="value"
              className="field__input"
              autoComplete="off"
              spellCheck={false}
              placeholder={info ? `e.g. ${info.example}` : undefined}
              value={value}
              onChange={(event) => {
                setValue(event.target.value);
              }}
            />
          )}
        </FormField>

        {type === "phone" && (
          <FormField
            label="Phone region (optional)"
            hint="Two-letter country code for numbers in national format, e.g. GB."
            error={errors.phone_region}
          >
            {(control) => (
              <input
                {...control}
                name="phone_region"
                className="field__input field__input--short"
                autoComplete="off"
                maxLength={2}
                value={region}
                onChange={(event) => {
                  setRegion(event.target.value);
                }}
              />
            )}
          </FormField>
        )}

        <FormField
          label="Note (optional)"
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

        <p className="field__hint">
          Recorded as a manual observation (Admiralty {schema.manual_defaults.admiralty}, confidence{" "}
          {Math.round(schema.manual_defaults.confidence * 100)}%).
        </p>

        {errors.form !== undefined && (
          <p className="form-error" role="alert">
            {errors.form}
          </p>
        )}
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={pending}>
            {pending ? "Adding…" : "Add entity"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
