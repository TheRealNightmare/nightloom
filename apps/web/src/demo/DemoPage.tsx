import type { GraphSummary } from "../components/SidePanel";
import { TypeSummary } from "../components/SidePanel";
import type { InvestigationGraph } from "../domain/entities";
import { GraphView } from "../graph/GraphView";
import { sampleGraph } from "./sampleGraph";
import { useDocumentTitle } from "../app/documentTitle";

export interface DemoPageProps {
  data?: InvestigationGraph;
}

function DemoOverview({ summary }: { summary: GraphSummary }) {
  return (
    <>
      <h2 className="panel__title">Demo investigation</h2>
      <p className="panel__text">
        A fictional attack-surface map of <code>example.org</code>. Every name, address and AS
        number is reserved for documentation, so nothing here points at a real target.
      </p>
      <p className="panel__text">
        {summary.nodes} entities · {summary.edges} relationships
      </p>
      <TypeSummary types={summary.types} />
      <p className="panel__hint">
        Hover a node to trace its threads, click it to see its details and provenance, or use the
        search box.
      </p>
    </>
  );
}

const overview = (summary: GraphSummary) => <DemoOverview summary={summary} />;

/** The built-in demo investigation (fictional data), at `/demo`. Never shown inside a case. */
export function DemoPage({ data = sampleGraph }: DemoPageProps) {
  useDocumentTitle("Demo graph");
  return (
    <main className="demo-page">
      <GraphView data={data} overview={overview} />
    </main>
  );
}
