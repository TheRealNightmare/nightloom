import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { can } from "../api/cases";
import { RequestAbortedError } from "../api/client";
import { DEFAULT_GRAPH_LIMIT, getCaseGraph, getSchema, type Schema } from "../api/graph";
import type { ScanProfile } from "../api/scans";
import { isActive, type Job, type ShuttleInfo } from "../api/shuttles";
import { TypeSummary, type GraphSummary } from "../components/SidePanel";
import type { EntityNode, InvestigationGraph } from "../domain/entities";
import { useAuth } from "../auth/context";
import type { FocusRequest } from "../graph/GraphView";
import { isViewMode, MODE_PARAM, storedMode, type ViewMode } from "../graph/mode";
import { useUrlFilters } from "../graph/useUrlFilters";
import { VIEW_PARAM } from "../graph/viewSnapshot";
import { useCase } from "./caseContext";
import { describeCaseError } from "./errors";
import { Loading } from "./Feedback";
import { AddEntityDialog } from "./graph/AddEntityDialog";
import { AddRelationshipDialog } from "./graph/AddRelationshipDialog";
import { caseLayout } from "./graph/caseLayout";
import { DeleteEntityDialog } from "./graph/DeleteEntityDialog";
import { EntityDetails } from "./graph/EntityDetails";
import { ExportDialog } from "./graph/ExportDialog";
import { FORMAT_LABELS } from "./graph/exportText";
import { CertificateAlerts } from "./graph/CertificateAlerts";
import { JobsPanel, RunningJobChip } from "./graph/JobsPanel";
import {
  toEntityNode,
  toInvestigationGraph,
  toRelationEdge,
  withEntity,
  withoutEntity,
  withRelationship,
} from "./graph/mapping";
import { RunProfile } from "./graph/RunProfile";
import { RunShuttles } from "./graph/RunShuttles";
import { SavedViews } from "./graph/SavedViews";
import { graphSummaryText, readOnlyReason } from "./graph/text";
import { useCaseJobs } from "./graph/useCaseJobs";
import { useCaseRuns, type RunOptions } from "./graph/useCaseRuns";

// sigma and graphology are loaded only when there is a graph to draw.
const GraphView = lazy(() => import("../graph/GraphView"));

export const GRAPH_EMPTY_MESSAGE = "No entities yet.";
export const GRAPH_EMPTY_HINT =
  "Add what you already know by hand, then select an entity and run Shuttles (such as DNS) on it to collect more.";
/** How long a finished job's results stay highlighted in the graph. */
export const PULSE_MS = 4000;

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; schema: Schema; data: InvestigationGraph; truncated: boolean };

type Dialog =
  | { kind: "add_entity" }
  | { kind: "add_relationship" }
  | { kind: "delete"; entity: EntityNode }
  | { kind: "export" }
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
  const [jobsOpen, setJobsOpen] = useState(false);
  const [pulse, setPulse] = useState<ReadonlySet<string> | null>(null);
  // Filters live in the query string: shareable, and they survive a reload.
  const [filters, updateFilters] = useUrlFilters();
  const layout = useMemo(() => caseLayout(caseId), [caseId]);
  // `?view=<id>` opens a saved view; it is taken out of the URL once applied.
  const [params, setParams] = useSearchParams();
  const deepLinkId = params.get(VIEW_PARAM);
  const takeDeepLink = useCallback(() => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete(VIEW_PARAM);
        return next;
      },
      { replace: true },
    );
  }, [setParams]);
  // 2D or 3D: in the URL (shareable), else the remembered choice.
  const modeParam = params.get(MODE_PARAM);
  const mode: ViewMode = isViewMode(modeParam) ? modeParam : storedMode();
  const changeMode = useCallback(
    (next: ViewMode) => {
      setParams(
        (current) => {
          const updated = new URLSearchParams(current);
          updated.set(MODE_PARAM, next);
          return updated;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  const { state: auth } = useAuth();
  const userId = auth.status === "signed_in" ? auth.user.id : null;

  // A finished job may have changed the graph: load it again (the view syncs
  // incrementally), and highlight what a successful job created or updated.
  const onJobFinished = useCallback(
    (job: Job) => {
      const { stats } = job;
      const changed =
        stats.entities_created +
          stats.entities_updated +
          stats.relationships_created +
          stats.relationships_updated >
        0;
      if (!changed) return;
      getCaseGraph(caseId, { limit: DEFAULT_GRAPH_LIMIT }).then(
        (graph) => {
          setLoad((previous) =>
            previous.kind === "ready"
              ? { ...previous, data: toInvestigationGraph(graph), truncated: graph.truncated }
              : previous,
          );
          setRevision((n) => n + 1);
          if (job.status === "succeeded" && stats.entities_touched.length > 0) {
            setPulse(new Set(stats.entities_touched));
          }
        },
        () => {
          setNotice("A job finished, but the graph could not be reloaded. Reload the page.");
        },
      );
    },
    [caseId],
  );
  const jobs = useCaseJobs(caseId, onJobFinished);
  const { start: startJob } = jobs;
  const runs = useCaseRuns(caseId, jobs.refresh);
  const { start: startRun } = runs;

  useEffect(() => {
    if (pulse === null) return;
    const timer = setTimeout(() => {
      setPulse(null);
    }, PULSE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [pulse]);

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
  // A case can be closed (or a role changed) while a dialog is open. Anyone may export.
  const openDialog = canEdit || dialog?.kind === "export" ? dialog : null;

  const allowPersonEntities = current.allow_person_entities;
  const allowAutoPivot = current.allow_auto_pivot;
  const runShuttle = useCallback(
    async (shuttle: ShuttleInfo, entity: EntityNode, options: { fresh?: boolean } = {}) => {
      const job = await startJob(shuttle.name, entity.id, options);
      setJobsOpen(true);
      return job;
    },
    [startJob],
  );
  const runProfile = useCallback(
    async (profile: ScanProfile, entityKeys: string[], options: RunOptions = {}) => {
      const run = await startRun(profile.id, entityKeys, options);
      setJobsOpen(true);
      return run;
    },
    [startRun],
  );
  // Runs a cached job's Shuttle again on the same entity, asking the source.
  const runFresh = useCallback(
    (job: Job) => startJob(job.shuttle_name, job.input_key, { fresh: true }),
    [startJob],
  );

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
      >
        {canEdit && (
          <RunProfile entities={[entity]} onRun={runProfile} allowPivot={allowAutoPivot} />
        )}
        {canEdit && (
          <RunShuttles
            entity={entity}
            allowPersonEntities={allowPersonEntities}
            onRun={runShuttle}
          />
        )}
      </EntityDetails>
    );
  }, [
    load,
    caseId,
    revision,
    canEdit,
    allowPersonEntities,
    allowAutoPivot,
    runShuttle,
    runProfile,
  ]);
  const renderSelection = useMemo(
    () =>
      canEdit
        ? (entities: readonly EntityNode[]) => (
            <RunProfile entities={entities} onRun={runProfile} allowPivot={allowAutoPivot} />
          )
        : undefined,
    [canEdit, allowAutoPivot, runProfile],
  );

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
  const entityIds = new Set(data.nodes.map((node) => node.id));
  const savedViews = (
    <SavedViews
      caseId={caseId}
      canSave={canEdit}
      isOwner={current.my_role === "owner"}
      userId={userId}
      hasEntity={(id) => entityIds.has(id)}
      deepLinkId={deepLinkId}
      onDeepLinkTaken={takeDeepLink}
    />
  );

  const summaryText = (
    <p className="case-graph__summary" role="status" aria-live="polite">
      {graphSummaryText(data.nodes.length, data.edges.length)}
    </p>
  );
  const actions = (
    <div className="button-row case-graph__actions">
      <button
        type="button"
        className="button"
        aria-expanded={jobsOpen}
        aria-controls="case-jobs"
        onClick={() => {
          setJobsOpen((open) => !open);
        }}
      >
        Jobs
        {jobs.activeCount > 0 && (
          <span className="count-badge" aria-label={`${String(jobs.activeCount)} active`}>
            {jobs.activeCount}
          </span>
        )}
      </button>
      {!empty && (
        <button
          type="button"
          className="button"
          onClick={() => {
            setNotice(null);
            setDialog({ kind: "export" });
          }}
        >
          Export
        </button>
      )}
      {canEdit && !empty && (
        <>
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
        </>
      )}
    </div>
  );
  const canCancel = can(current.my_role, "run_shuttles");
  // Newest first: the chip over the graph shows the latest job still under way.
  const running = jobs.jobs.find(isActive);

  return (
    <section className="case-graph" aria-label="Case graph">
      {/* With a graph, the summary and actions sit in the graph's toolbar. */}
      {empty && (
        <div className="case-graph__bar">
          {summaryText}
          {actions}
        </div>
      )}
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
      <CertificateAlerts
        caseId={caseId}
        canAcknowledge={canCancel}
        onShow={(keys) => {
          setPulse(new Set(keys));
        }}
      />
      {jobsOpen && (
        <div id="case-jobs">
          <JobsPanel
            jobs={jobs.jobs}
            loading={jobs.loading}
            error={jobs.error}
            hasMore={jobs.hasMore}
            loadingMore={jobs.loadingMore}
            // Editors and owners may cancel, also while the case is closed.
            canCancel={canCancel}
            onCancel={jobs.cancel}
            runs={runs.runs}
            onCancelRun={runs.cancel}
            // Running again needs an open case, like any new job.
            {...(canEdit ? { onRunFresh: runFresh } : {})}
            onLoadMore={jobs.loadMore}
            onClose={() => {
              setJobsOpen(false);
            }}
          />
        </div>
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
            // One graph, and one set of pins, per case.
            key={caseId}
            className="workspace--case"
            data={data}
            overview={overview}
            {...(renderEntity ? { renderEntity } : {})}
            {...(renderSelection ? { renderSelection } : {})}
            focus={focus}
            onSelectionChange={setSelectedId}
            {...(pulse ? { pulse } : {})}
            filters={filters}
            onFiltersChange={updateFilters}
            layout={layout}
            savedViews={savedViews}
            mode={mode}
            onModeChange={changeMode}
            grades={schema.admiralty}
            toolbar={
              <>
                {summaryText}
                {actions}
              </>
            }
            {...(running
              ? {
                  overlay: (
                    <RunningJobChip
                      job={running}
                      canCancel={canCancel}
                      onCancel={jobs.cancel}
                      onOpen={() => {
                        setJobsOpen(true);
                      }}
                    />
                  ),
                }
              : {})}
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
      {openDialog?.kind === "export" && (
        <ExportDialog
          caseId={caseId}
          canIncludePersonData={can(current.my_role, "export_person_data")}
          onClose={closeDialog}
          onStarted={(format) => {
            setDialog(null);
            setNotice(`Your ${FORMAT_LABELS[format].label} export is downloading.`);
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
