import { formatRelative, formatTimestamp } from "../domain/format";

/** "3 hours ago", with the exact UTC time on hover. */
export function RelativeTime({ iso }: { iso: string }) {
  return (
    <time dateTime={iso} title={formatTimestamp(iso)}>
      {formatRelative(iso)}
    </time>
  );
}
