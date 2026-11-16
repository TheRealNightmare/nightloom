const dateFormat = new Intl.DateTimeFormat("en", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

/** `2026-11-02T09:30:00Z` → `Nov 2, 2026, 9:30 AM UTC`; unparseable input is returned as is. */
export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : `${dateFormat.format(date)} UTC`;
}

/** 0.856 → `86%` (clamped to 0–100%). */
export function formatConfidence(confidence: number): string {
  return `${Math.round(Math.min(Math.max(confidence, 0), 1) * 100)}%`;
}
