import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { RequestAbortedError } from "../api/client";
import { DEFAULT_GRAPH_LIMIT, getCaseGraph, getSchema, type Schema } from "../api/graph";
import { TypeSummary, type GraphSummary } from "../components/SidePanel";
import type { EntityNode, InvestigationGraph } from "../domain/entities";
import type { FocusRequest } from "../graph/GraphView";
import { useCase } from "./caseContext";
import { describeCaseError } from "./errors";
import { Loading } from "./Feedback";
import { AddEntityDialog } from "./graph/AddEntityDialog";
import { AddRelationshipDialog } from "./graph/AddRelationshipDialog";
import { DeleteEntityDialog } from "./graph/DeleteEntityDialog";
import { EntityDetails } from "./graph/EntityDetails";
import {
  toEntityNode,
  toInvestigationGraph,
  toRelationEdge,
  withEntity,
  withoutEntity,
  withRelationship,
} from "./graph/mapping";
import { graphSummaryText, readOnlyReason } from "./graph/text";

// sigma and graphology are loaded only when there is a graph to draw.
const GraphView = lazy(() => import("../graph/GraphView"));

export const GRAPH_EMPTY_MESSAGE = "No entities yet.";
export const GRAPH_EMPTY_HINT =
  "Add what you already know by hand. Automated collection (Shuttles) comes next.";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; schema: Schema; data: InvestigationGraph; truncated: boolean };

type Dialog =
  | { kind: "add_entity" }
  | { kind: "add_relationship" }
  | { kind: "delete"; entity: EntityNode }
  | null;

function CaseOverview({ summary }: { summary: GraphSummary }) {
  return (
    <>
      <h2 className="panel__title">Case graph</h2>
      <TypeSummary types={summary.types} />
      <p className="panel__hint">
        Click an entity to see its key, provenance and connections, or find one with the search box.
      </p>
    </>
  );
}

const overview = (summary: GraphSummary) => <CaseOverview summary={summary} />;

/** A case's graph: what the API returns, never demo data. Editors add and delete by hand. */
export function GraphTab() {
  const { case: current } = useCase();
  const caseId = current.id;
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [generation, setGeneration] = useState(0);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getCaseGraph(caseId, { limit: DEFAULT_GRAPH_LIMIT }, { signal: controller.signal }),
      getSchema(),
    ]).then(
      ([graph, schema]) => {
        setLoad({
          kind: "ready",
          schema,
          data: toInvestigationGraph(graph),
          truncated: graph.truncated,
        });
      },
      (caught: unknown) => {
        if (caught instanceof RequestAbortedError) return;
        setLoad({ kind: "error", message: describeCaseError(caught) });
      },
    );
    return () => {
      controller.abort();
    };
  }, [caseId, generation]);

  const update = useCallback((change: (data: InvestigationGraph) => InvestigationGraph) => {
    setLoad((previous) =>
      previous.kind === "ready" ? { ...previous, data: change(previous.data) } : previous,
    );
    setRevision((n) => n + 1);
  }, []);

  const closeDialog = useCallback(() => {
    setDialog(null);
  }, []);

  const reason = readOnlyReason(current);
  const canEdit = reason === null;
  // A case can be closed (or a role changed) while a dialog is open.
  const openDialog = canEdit ? dialog : null;

  const renderEntity = useMemo(() => {
    if (load.kind !== "ready") return undefined;
    const { schema } = load;
    return (entity: EntityNode) => (
      <EntityDetails
        caseId={caseId}
        schema={schema}
        entity={entity}
        revision={revision}
        canEdit={canEdit}
        onDelete={(target) => {
          setDialog({ kind: "delete", entity: target });
        }}
      />
    );
  }, [load, caseId, revision, canEdit]);

  if (load.kind === "loading") return <Loading label="Loading graph…" />;
  if (load.kind === "error") {
    return (
      <div className="graph-error">
        <p className="form-error" role="alert">
          {load.message}
        </p>
        <button
          type="button"
          className="button"
          onClick={() => {
            setLoad({ kind: "loading" });
            setGeneration((n) => n + 1);
          }}
        >
          Try again
        </button>
      </div>
    );
  }

  const { data, schema, truncated } = load;
  const empty = data.nodes.length === 0;

  return (
    <section className="case-graph" aria-label="Graph">
      <div className="case-graph__bar">
        <p className="case-graph__summary" role="status" aria-live="polite">
          {graphSummaryText(data.nodes.length, data.edges.length)}
        </p>
        {canEdit && !empty && (
          <div className="button-row">
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                setNotice(null);
                setDialog({ kind: "add_entity" });
              }}
            >
              Add entity
            </button>
            <button
              type="button"
              className="button"
              disabled={data.nodes.length < 2}
              title={data.nodes.length < 2 ? "Add at least two entities first." : undefined}
              onClick={() => {
                setNotice(null);
                setDialog({ kind: "add_relationship" });
              }}
            >
              Add relationship
            </button>
          </div>
        )}
      </div>
      {reason !== null && <p className="case-graph__readonly">{reason}</p>}
      {truncated && (
        <p className="warning case-graph__truncated" role="status">
          This graph is larger than one view can show: only the first{" "}
          {DEFAULT_GRAPH_LIMIT.toLocaleString("en")} entities (ordered by key) and at most five
          relationships per entity are drawn.
        </p>
      )}
      {notice !== null && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}

      {empty ? (
        <div className="empty-state empty-state--graph">
          <h2 className="empty-state__title">{GRAPH_EMPTY_MESSAGE}</h2>
          <p className="empty-state__text">{GRAPH_EMPTY_HINT}</p>
          {canEdit && (
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                setDialog({ kind: "add_entity" });
              }}
            >
              Add your first entity
            </button>
          )}
        </div>
      ) : (
        <Suspense fallback={<Loading label="Loading graph view…" />}>
          <GraphView
            className="workspace--case"
            data={data}
            overview={overview}
            {...(renderEntity ? { renderEntity } : {})}
            focus={focus}
            onSelectionChange={setSelectedId}
          />
        </Suspense>
      )}

      {openDialog?.kind === "add_entity" && (
        <AddEntityDialog
          caseId={caseId}
          schema={schema}
          allowPersonEntities={current.allow_person_entities}
          onCancel={closeDialog}
          onCreated={(created) => {
            const existed = data.nodes.some((node) => node.id === created.key);
            update((previous) => withEntity(previous, toEntityNode(created)));
            setDialog(null);
            setNotice(
              existed
                ? `“${created.value}” was already in this case; your observation was added to it.`
                : null,
            );
            setFocus({ id: created.key });
          }}
        />
      )}
      {openDialog?.kind === "add_relationship" && (
        <AddRelationshipDialog
          caseId={caseId}
          schema={schema}
          nodes={data.nodes}
          initialSource={selectedId}
          onCancel={closeDialog}
          onCreated={(created) => {
            update((previous) => withRelationship(previous, toRelationEdge(created)));
            setDialog(null);
          }}
        />
      )}
      {openDialog?.kind === "delete" && (
        <DeleteEntityDialog
          caseId={caseId}
          entity={openDialog.entity}
          onCancel={closeDialog}
          onDeleted={(id) => {
            update((previous) => withoutEntity(previous, id));
            setDialog(null);
          }}
        />
      )}
    </section>
  );
}
