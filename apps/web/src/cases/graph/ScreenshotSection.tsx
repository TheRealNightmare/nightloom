import { useEffect, useState } from "react";

import { RequestAbortedError } from "../../api/client";
import { evidenceUrl, isEvidenceName, similarPages, type SimilarPage } from "../../api/evidence";
import type { EntityNode } from "../../domain/entities";
import { RelativeTime } from "../RelativeTime";

export const LOOKS_LIKE_HINT =
  "Pages in this case whose screenshots look alike (a perceptual hash at most 10 of 64 bits " +
  "apart): a copied login page, a staging site, a shared template.";

function similarity(distance: number): string {
  if (distance === 0) return "looks the same";
  return `${String(distance)} bit${distance === 1 ? "" : "s"} apart`;
}

/** A page's screenshot (kept as evidence) and the case's pages that look like it. */
export function ScreenshotSection({
  caseId,
  entity,
  revision,
  onFocusEntity,
}: {
  caseId: string;
  entity: EntityNode;
  revision: number;
  onFocusEntity?: (key: string) => void;
}) {
  const name = entity.properties?.["screenshot"];
  const takenAt = entity.properties?.["screenshot_at"];
  const hasShot = isEvidenceName(name);
  // What was loaded, and for which entity and graph revision (older answers are ignored).
  const [loaded, setLoaded] = useState<{
    for: string;
    items: SimilarPage[] | null;
    failed: boolean;
  } | null>(null);
  const current = `${entity.id}@${String(revision)}`;

  useEffect(() => {
    if (!hasShot) return;
    const controller = new AbortController();
    similarPages(caseId, entity.id, { signal: controller.signal }).then(
      (items) => {
        setLoaded({ for: current, items, failed: false });
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setLoaded({ for: current, items: null, failed: true });
      },
    );
    return () => {
      controller.abort();
    };
  }, [caseId, entity.id, hasShot, current]);

  const similar = loaded?.for === current ? loaded.items : null;
  const failed = loaded?.for === current && loaded.failed;

  if (!hasShot) return null;
  return (
    <section aria-label="Screenshot">
      <h3 className="panel__section">Screenshot</h3>
      <a href={evidenceUrl(caseId, name)} target="_blank" rel="noopener noreferrer">
        <img
          className="screenshot"
          src={evidenceUrl(caseId, name)}
          alt={`Screenshot of ${entity.label}`}
          loading="lazy"
          width={1280}
          height={800}
        />
      </a>
      {typeof takenAt === "string" && (
        <p className="panel__text">
          Taken <RelativeTime iso={takenAt} />.
        </p>
      )}
      <h3 className="panel__section">Looks like</h3>
      <p className="panel__text">{LOOKS_LIKE_HINT}</p>
      {failed && <p className="table__muted">The look-alike pages could not be loaded.</p>}
      {similar !== null && similar.length === 0 && (
        <p className="table__muted">No other page in this case looks like it.</p>
      )}
      {similar !== null && similar.length > 0 && (
        <ul className="looks-like" aria-label="Pages that look alike">
          {similar.map((item) => (
            <li key={item.key}>
              {onFocusEntity ? (
                <button
                  type="button"
                  className="button button--ghost button--small looks-like__item"
                  onClick={() => {
                    onFocusEntity(item.key);
                  }}
                >
                  {item.value}
                </button>
              ) : (
                <span className="looks-like__item">{item.value}</span>
              )}{" "}
              <span className="job__meta">{similarity(item.distance)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
