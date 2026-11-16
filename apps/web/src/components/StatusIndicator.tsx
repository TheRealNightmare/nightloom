import { useId } from "react";

import type { ReadinessView } from "../api/useReadiness";

const LABELS: Record<ReadinessView["kind"], string> = {
  checking: "Checking API…",
  ready: "Ready",
  degraded: "Degraded",
  offline: "API offline",
};

function describe(readiness: ReadinessView): string[] {
  switch (readiness.kind) {
    case "checking":
      return ["Contacting the API…"];
    case "ready":
      return ["All dependencies are reachable."];
    case "degraded":
      return [
        "The API is up, but these checks failed:",
        ...readiness.failed.map(({ name, error }) => `${name}: ${error}`),
      ];
    case "offline":
      return ["The API cannot be reached.", readiness.reason];
  }
}

export interface StatusIndicatorProps {
  readiness: ReadinessView;
  onRecheck: () => void;
}

/** API readiness pill. Hover or focus it for details; click to check again. */
export function StatusIndicator({ readiness, onRecheck }: StatusIndicatorProps) {
  const tooltipId = useId();
  const label = LABELS[readiness.kind];
  const details = describe(readiness);

  return (
    <div className="status">
      <button
        type="button"
        className="status__button"
        data-state={readiness.kind}
        aria-describedby={tooltipId}
        onClick={onRecheck}
      >
        <span className="status__dot" aria-hidden="true" />
        <span className="visually-hidden">API status:</span> {label}
      </button>
      <div role="tooltip" id={tooltipId} className="status__tooltip">
        <ul>
          {details.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <p className="status__hint">Checked every 15 s · click to check now</p>
      </div>
    </div>
  );
}
