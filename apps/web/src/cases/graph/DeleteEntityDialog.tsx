import { useRef, useState } from "react";

import { ConflictError } from "../../api/client";
import { deleteEntity } from "../../api/graph";
import { Modal } from "../../components/Modal";
import type { EntityNode } from "../../domain/entities";
import { describeCaseError } from "../errors";

export interface DeleteEntityDialogProps {
  caseId: string;
  entity: EntityNode;
  onDeleted: (id: string) => void;
  onCancel: () => void;
}

/** Confirms deleting an entity (and its relationships) from the case. */
export function DeleteEntityDialog({
  caseId,
  entity,
  onDeleted,
  onCancel,
}: DeleteEntityDialogProps) {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const confirm = async () => {
    if (pending) return;
    setPending(true);
    try {
      await deleteEntity(caseId, entity.id);
      onDeleted(entity.id);
    } catch (caught) {
      setPending(false);
      setError(caught instanceof ConflictError ? caught.message : describeCaseError(caught));
    }
  };

  return (
    <Modal
      title="Delete entity?"
      description={
        <>
          “{entity.label}” and all its relationships are removed from this case. The deletion is
          recorded in the case's activity and cannot be undone.
        </>
      }
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
          {pending ? "Deleting…" : "Delete entity"}
        </button>
      </div>
    </Modal>
  );
}
