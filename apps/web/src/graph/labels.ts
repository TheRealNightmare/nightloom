/**
 * Shorter labels for the canvas only. The side panel, search and the
 * announcements keep the full value; search still matches it.
 *
 * - A certificate shows its subject's common name (`subject_cn`) when it has
 *   one, else the first 8 and last 4 characters of its fingerprint
 *   ("c73e72dd…41a3").
 * - Any other label longer than {@link MAX_CANVAS_LABEL} characters is cut
 *   in the middle, so both ends (a URL's host and its last path segment,
 *   say) stay readable.
 */
import type { EntityNode } from "../domain/entities";

export const MAX_CANVAS_LABEL = 32;
export const ELLIPSIS = "…";

/** `text` cut in the middle to at most `max` characters (the ellipsis counts as one). */
export function middleTruncate(text: string, max = MAX_CANVAS_LABEL): string {
  // Code points, so a cut never splits a surrogate pair.
  const chars = Array.from(text);
  if (chars.length <= max) return text;
  if (max <= 1) return ELLIPSIS;
  const keep = max - 1;
  const head = Math.ceil(keep / 2);
  const tail = keep - head;
  return `${chars.slice(0, head).join("")}${ELLIPSIS}${tail > 0 ? chars.slice(-tail).join("") : ""}`;
}

/** "c73e72dd…41a3": the first 8 and last 4 characters of a fingerprint. */
export function shortFingerprint(fingerprint: string): string {
  const value = fingerprint.trim();
  if (value.length <= 13) return value;
  return `${value.slice(0, 8)}${ELLIPSIS}${value.slice(-4)}`;
}

/** The label drawn next to the entity's knot. */
export function canvasLabel(entity: Pick<EntityNode, "type" | "label" | "properties">): string {
  if (entity.type === "certificate") {
    const cn = entity.properties?.["subject_cn"];
    if (typeof cn === "string" && cn.trim() !== "") return middleTruncate(cn.trim());
    return shortFingerprint(entity.label);
  }
  return middleTruncate(entity.label);
}
