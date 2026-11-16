export const GRAPH_EMPTY_MESSAGE =
  "No entities yet. Data collection (Shuttles) arrives in the next milestone.";

/** A case's graph. Deliberately empty until Shuttles exist: never demo data inside a case. */
export function GraphTab() {
  return (
    <section className="empty-state empty-state--graph" aria-label="Graph">
      <p className="empty-state__text">{GRAPH_EMPTY_MESSAGE}</p>
    </section>
  );
}
