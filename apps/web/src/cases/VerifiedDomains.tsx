import { useCallback, useEffect, useId, useState, type SubmitEvent } from "react";

import type { Case } from "../api/cases";
import { RequestAbortedError } from "../api/client";
import {
  addVerification,
  checkVerification,
  listVerifications,
  removeVerification,
  VERIFICATION_METHODS,
  type Verification,
  type VerificationMethod,
} from "../api/ownership";
import { LoadError } from "./Feedback";
import { Modal } from "../components/Modal";
import { describeCaseError } from "./errors";
import {
  METHOD_LABELS,
  proofInstructions,
  STATUS_LABELS,
  statusDetail,
  VERIFIED_DOMAINS_HINT,
} from "./verificationText";

/** How often the list is read again while a check is queued. */
export const CHECK_POLL_MS = 3000;

const BADGE: Record<Verification["status"], string> = {
  pending: "queued",
  verified: "succeeded",
  failed: "failed",
  revoked: "failed",
  expired: "cancelled",
};

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
  if (clipboard === undefined) return null;
  return (
    <button
      type="button"
      className="button button--ghost button--small"
      aria-label={`Copy ${label}`}
      onClick={() => {
        clipboard.writeText(text).then(
          () => {
            setCopied(true);
          },
          () => {
            setCopied(false);
          },
        );
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function ProofDetails({ item }: { item: Verification }) {
  const { proof } = item;
  const where = proof.record_name ?? proof.url ?? "";
  return (
    <div className="verification__proof" data-testid="proof">
      <p className="field__hint">{proofInstructions(item)}</p>
      <dl className="verification__proof-list">
        <dt>{proof.record_name !== null ? `Name (${proof.record_type ?? "TXT"})` : "Address"}</dt>
        <dd>
          <code>{where}</code> <CopyButton text={where} label={`the ${item.domain} proof's name`} />
        </dd>
        <dt>{proof.record_name !== null ? "Value" : "Contents"}</dt>
        <dd>
          <code>{proof.value}</code>{" "}
          <CopyButton text={proof.value} label={`the ${item.domain} proof's value`} />
        </dd>
      </dl>
    </div>
  );
}

/** Owners: the case's verified domains, which active Shuttles may scan. */
export function VerifiedDomains({ value }: { value: Case }) {
  const id = useId();
  const open = value.status === "open";
  const [items, setItems] = useState<Verification[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [domain, setDomain] = useState("");
  const [method, setMethod] = useState<VerificationMethod>("dns_txt");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Verification | null>(null);

  const merge = useCallback((next: Verification) => {
    setItems((current) => {
      const others = (current ?? []).filter((item) => item.id !== next.id);
      return [...others, next].sort((a, b) => a.domain.localeCompare(b.domain));
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listVerifications(value.id, { signal: controller.signal }).then(
      (loaded) => {
        setItems(loaded);
        setLoadError(null);
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setLoadError(describeCaseError(caught));
      },
    );
    return () => {
      controller.abort();
    };
  }, [value.id, attempt]);

  const checking = items?.some((item) => item.checking) ?? false;
  useEffect(() => {
    if (!checking) return;
    const timer = setTimeout(() => {
      setAttempt((n) => n + 1);
    }, CHECK_POLL_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [checking, items]);

  const add = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    if (domain.trim() === "") {
      setError("Enter a domain, such as example.org.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const created = await addVerification(value.id, { domain: domain.trim(), method });
      merge(created);
      setShown(created.id);
      setDomain("");
    } catch (caught) {
      setError(describeCaseError(caught));
    } finally {
      setPending(false);
    }
  };

  const check = async (item: Verification) => {
    setError(null);
    try {
      merge(await checkVerification(value.id, item.id));
    } catch (caught) {
      setError(describeCaseError(caught));
    }
  };

  const remove = async (item: Verification) => {
    setRemoving(null);
    setError(null);
    try {
      await removeVerification(value.id, item.id);
      setItems((current) => (current ?? []).filter((other) => other.id !== item.id));
    } catch (caught) {
      setError(describeCaseError(caught));
    }
  };

  return (
    <section aria-labelledby={`${id}-title`} className="section">
      <h2 id={`${id}-title`} className="section__title">
        Verified domains
      </h2>
      <p className="section__hint">{VERIFIED_DOMAINS_HINT}</p>
      {open && (
        <form
          noValidate
          className="verification__add"
          aria-label="Add a domain to verify"
          onSubmit={(event) => {
            void add(event);
          }}
        >
          <div className="field">
            <label className="field__label" htmlFor={`${id}-domain`}>
              Domain
            </label>
            <input
              id={`${id}-domain`}
              className="field__input"
              value={domain}
              placeholder="example.org"
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => {
                setDomain(event.target.value);
              }}
            />
          </div>
          <fieldset className="field">
            <legend className="field__label">Prove it with</legend>
            {VERIFICATION_METHODS.map((option) => (
              <label key={option} className="checkbox">
                <input
                  type="radio"
                  name={`${id}-method`}
                  value={option}
                  checked={method === option}
                  onChange={() => {
                    setMethod(option);
                  }}
                />
                <span>{METHOD_LABELS[option]}</span>
              </label>
            ))}
          </fieldset>
          <div className="button-row">
            <button type="submit" className="button" disabled={pending}>
              {pending ? "Adding…" : "Add domain"}
            </button>
          </div>
        </form>
      )}
      <div className="form-error" role="alert">
        {error}
      </div>
      {items === null && loadError !== null && (
        <LoadError
          message={loadError}
          onRetry={() => {
            setLoadError(null);
            setAttempt((n) => n + 1);
          }}
        />
      )}
      {items !== null && items.length === 0 && (
        <p className="table__muted">No domains yet. Active Shuttles cannot run in this case.</p>
      )}
      {items !== null && items.length > 0 && (
        <ul className="verification__list" aria-label="Verified domains">
          {items.map((item) => {
            const proofOpen = shown === item.id;
            return (
              <li key={item.id} className="verification" aria-label={item.domain}>
                <div className="verification__head">
                  <strong className="verification__domain">{item.domain}</strong>
                  <span
                    className="job-status"
                    data-status={item.checking ? "running" : BADGE[item.status]}
                  >
                    {item.checking ? "Checking" : STATUS_LABELS[item.status]}
                  </span>
                </div>
                <p className="job__meta">
                  {METHOD_LABELS[item.method]} · {statusDetail(item)}
                  {item.status === "verified" && item.addresses.length > 0 && (
                    <> · also covers {item.addresses.join(", ")}</>
                  )}
                </p>
                <div className="button-row">
                  {open && (
                    <button
                      type="button"
                      className="button button--small"
                      disabled={item.checking}
                      aria-label={`Check ${item.domain} now`}
                      onClick={() => {
                        void check(item);
                      }}
                    >
                      Check now
                    </button>
                  )}
                  <button
                    type="button"
                    className="button button--ghost button--small"
                    aria-expanded={proofOpen}
                    onClick={() => {
                      setShown(proofOpen ? null : item.id);
                    }}
                  >
                    {proofOpen ? "Hide proof" : "Show proof"}
                  </button>
                  <button
                    type="button"
                    className="button button--ghost button--small"
                    aria-label={`Remove ${item.domain}`}
                    onClick={() => {
                      setRemoving(item);
                    }}
                  >
                    Remove
                  </button>
                </div>
                {proofOpen && <ProofDetails item={item} />}
              </li>
            );
          })}
        </ul>
      )}
      {removing !== null && (
        <Modal
          title={`Remove ${removing.domain}?`}
          description="Active Shuttles stop running on it and its subdomains. You can add it again later."
          onClose={() => {
            setRemoving(null);
          }}
        >
          <div className="dialog__actions">
            <button
              type="button"
              className="button"
              onClick={() => {
                setRemoving(null);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                void remove(removing);
              }}
            >
              Remove
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
