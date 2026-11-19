import { useRef, useState, type SubmitEvent } from "react";

import {
  ContentTooLargeError,
  ForbiddenError,
  RateLimitedError,
  ValidationError,
} from "../../api/client";
import {
  checkExport,
  downloader,
  EXPORT_FORMATS,
  EXPORT_REASON_MAX_LENGTH,
  EXPORT_REASON_MIN_LENGTH,
  exportUrl,
  type ExportFormat,
  type ExportRequest,
} from "../../api/export";
import { Modal } from "../../components/Modal";
import { describeCaseError } from "../errors";
import { FormField } from "../FormField";
import { FORMAT_LABELS, OWNER_ONLY_MESSAGE, REASON_TOO_SHORT, REDACTION_NOTE } from "./exportText";

export interface ExportDialogProps {
  caseId: string;
  /** Case owners may include person-related data, with a reason. */
  canIncludePersonData: boolean;
  onClose: () => void;
  /** The download was started. */
  onStarted: (format: ExportFormat) => void;
}

function describeExportError(error: unknown): string {
  if (error instanceof RateLimitedError) {
    if (error.detail) return error.detail;
    const minutes = Math.ceil((error.retryAfterSeconds ?? 60) / 60);
    return `Too many exports. Try again in ${String(minutes)} minute${minutes === 1 ? "" : "s"}.`;
  }
  if (error instanceof ContentTooLargeError) {
    return error.detail ?? "This case is too large to export.";
  }
  if (error instanceof ForbiddenError) return OWNER_ONLY_MESSAGE;
  if (error instanceof ValidationError) return error.messages.map((m) => m.message).join(" ");
  return describeCaseError(error);
}

/** Choose a format (and, for owners, whether to include person data), then download. */
export function ExportDialog({
  caseId,
  canIncludePersonData,
  onClose,
  onStarted,
}: ExportDialogProps) {
  const [format, setFormat] = useState<ExportFormat>("json");
  const [includePersonData, setIncludePersonData] = useState(false);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const firstRef = useRef<HTMLInputElement>(null);

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    setError(null);
    const including = canIncludePersonData && includePersonData;
    if (including && reason.trim().length < EXPORT_REASON_MIN_LENGTH) {
      setReasonError(REASON_TOO_SHORT);
      return;
    }
    setReasonError(undefined);
    const request: ExportRequest = {
      format,
      includePersonData: including,
      ...(including ? { reason } : {}),
    };
    setPending(true);
    try {
      await checkExport(caseId, request);
    } catch (caught) {
      setPending(false);
      setError(describeExportError(caught));
      return;
    }
    downloader.start(exportUrl(caseId, request));
    setPending(false);
    onStarted(format);
  };

  return (
    <Modal
      title="Export case"
      description={REDACTION_NOTE}
      onClose={onClose}
      initialFocus={firstRef}
      wide
    >
      <form
        noValidate
        aria-busy={pending}
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <fieldset className="choices">
          <legend className="field__label">Format</legend>
          {EXPORT_FORMATS.map((value, index) => (
            <label key={value} className="checkbox choice">
              <input
                ref={index === 0 ? firstRef : undefined}
                type="radio"
                name="export-format"
                value={value}
                checked={format === value}
                onChange={() => {
                  setFormat(value);
                }}
              />
              <span className="choice__text">
                {FORMAT_LABELS[value].label}
                <span className="field__hint">{FORMAT_LABELS[value].hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {canIncludePersonData && (
          <>
            <label className="checkbox choice">
              <input
                type="checkbox"
                checked={includePersonData}
                onChange={(event) => {
                  setIncludePersonData(event.target.checked);
                  setReasonError(undefined);
                }}
              />
              <span className="choice__text">
                Include person-related data
                <span className="field__hint">
                  Exports the real values. Your reason is recorded in the case's activity.
                </span>
              </span>
            </label>
            {includePersonData && (
              <FormField
                label="Reason"
                hint={`Why this export needs person-related data (at least ${String(EXPORT_REASON_MIN_LENGTH)} characters).`}
                error={reasonError}
              >
                {(control) => (
                  <textarea
                    {...control}
                    className="field__input field__textarea"
                    rows={3}
                    maxLength={EXPORT_REASON_MAX_LENGTH}
                    value={reason}
                    onChange={(event) => {
                      setReason(event.target.value);
                    }}
                  />
                )}
              </FormField>
            )}
          </>
        )}

        {error !== null && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={pending}>
            {pending ? "Checking…" : "Download"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
