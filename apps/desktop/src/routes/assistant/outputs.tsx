/**
 * Assistant Outputs page — the full outputs browser:
 *
 *   - Left pane: a tree grouped by ``batch_id → workflow_id → run_id``
 *     with status badges (running / succeeded / failed / cancelled /
 *     pending).
 *   - Right pane: four detail tabs (Markdown / Metadata / Prompts /
 *     Logs) for the selected run.
 *   - Top action bar: rerun-same / rerun-different / export /
 *     open-folder / compare.
 *
 * The route smoke test asserts ``getByRole("heading", { level: 1,
 * name: "Outputs" })`` — keep the ``<h1>Outputs</h1>`` exactly. The page
 * uses React Query (``useQuery``), so tests must wrap the route in a
 * ``QueryClientProvider``.
 *
 * Action handlers (rerun-* / compare / export / open-folder) have
 * working defaults; tests can inject their own through the optional
 * ``handlers`` prop to assert only the button-fires-the-call shape.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  exportRun,
  getPrimaryOutput,
  listAssistantOutputs,
  openRunFolder,
  type AssistantOutputRecord,
  type PrimaryOutputResponse,
} from "../../api/assistantOutputs";
import { getEffectivePrompts, rerunRun } from "../../api/runs";
import { getBatch, type EditableBatch } from "../../api/batches";
import { RunWorkflowDialog } from "../../components/batches/RunWorkflowDialog";
import RunComparisonView, {
  type ComparisonRunPayload,
  type EffectivePromptsSnapshot,
} from "../../components/assistant/RunComparisonView";

// ---------------------------------------------------------------------------
// Tree grouping
// ---------------------------------------------------------------------------

interface RunNode {
  run: AssistantOutputRecord;
}

interface WorkflowNode {
  workflow_id: string;
  runs: RunNode[];
}

interface BatchNode {
  batch_id: string;
  workflows: Map<string, WorkflowNode>;
}

/**
 * Group a flat list of run records by ``batch_id → workflow_id``.
 * Stable insertion order (Map preserves it) so the tree renders in the
 * same order across re-fetches when the server-side order is stable.
 */
export function groupRunsByBatch(
  runs: ReadonlyArray<AssistantOutputRecord>,
): BatchNode[] {
  const byBatch = new Map<string, BatchNode>();
  for (const r of runs) {
    const bid = r.batch_id ?? "(no-batch)";
    const wid = r.workflow_id ?? "(no-workflow)";
    let b = byBatch.get(bid);
    if (!b) {
      b = { batch_id: bid, workflows: new Map() };
      byBatch.set(bid, b);
    }
    let w = b.workflows.get(wid);
    if (!w) {
      w = { workflow_id: wid, runs: [] };
      b.workflows.set(wid, w);
    }
    w.runs.push({ run: r });
  }
  return Array.from(byBatch.values());
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  // Three-tier visual: running (pulsing), succeeded (green), failed
  // (red), cancelled (grey), pending (yellow).
  const tone = (() => {
    switch (status) {
      case "running":
        return "bg-blue-100 text-blue-800 animate-pulse";
      case "succeeded":
        return "bg-green-100 text-green-800";
      case "failed":
        return "bg-red-100 text-red-800";
      case "cancelled":
        return "bg-gray-100 text-gray-800";
      default:
        return "bg-yellow-100 text-yellow-800";
    }
  })();
  return (
    <span
      data-testid={`status-badge-${status}`}
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${tone}`}
    >
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Detail pane (four tabs)
// ---------------------------------------------------------------------------

type DetailTab = "markdown" | "metadata" | "prompts" | "logs";

interface DetailPaneProps {
  run: AssistantOutputRecord;
}

function DetailPane({ run }: DetailPaneProps) {
  const [tab, setTab] = useState<DetailTab>("markdown");

  const primaryQ = useQuery({
    queryKey: ["assistant-output-primary", run.id],
    queryFn: () => getPrimaryOutput(run.id),
    retry: false,
    enabled: tab === "markdown",
  });
  const promptsQ = useQuery({
    queryKey: ["assistant-output-prompts", run.id],
    queryFn: () => getEffectivePrompts(run.id),
    retry: false,
    enabled: tab === "prompts",
  });

  const tabs: Array<[DetailTab, string]> = [
    ["markdown", "Markdown"],
    ["metadata", "Metadata"],
    ["prompts", "Prompts"],
    ["logs", "Logs"],
  ];

  return (
    <div className="flex flex-col gap-3" data-testid="detail-pane">
      <div className="flex gap-2 border-b">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            data-testid={`detail-tab-${key}`}
            className={`px-3 py-2 text-sm ${
              tab === key
                ? "border-b-2 border-primary font-semibold"
                : "text-muted-foreground"
            }`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "markdown" && (
        <MarkdownTab query={primaryQ} runId={run.id} />
      )}
      {tab === "metadata" && <MetadataTab run={run} />}
      {tab === "prompts" && <PromptsTab data={promptsQ.data} isLoading={promptsQ.isLoading} error={promptsQ.error} />}
      {tab === "logs" && <LogsTab run={run} />}
    </div>
  );
}

function MarkdownTab({
  query,
  runId,
}: {
  query: ReturnType<typeof useQuery<PrimaryOutputResponse>>;
  runId: string;
}) {
  if (query.isLoading) {
    return <div className="text-sm text-muted-foreground">Loading…</div>;
  }
  if (query.error) {
    return (
      <div
        data-testid="markdown-empty"
        className="text-sm text-muted-foreground"
      >
        No primary output for run {runId} yet.
      </div>
    );
  }
  if (!query.data) {
    return <div className="text-sm text-muted-foreground">No data.</div>;
  }
  return (
    <article
      data-testid="markdown-body"
      className="prose prose-sm max-w-none whitespace-pre-wrap"
    >
      {query.data.content}
    </article>
  );
}

function MetadataTab({ run }: { run: AssistantOutputRecord }) {
  // The registry record is the canonical metadata source for the page.
  // A richer metadata.json reader can be added here if needed.
  return (
    <pre
      data-testid="metadata-body"
      className="overflow-auto rounded bg-muted p-3 text-xs"
    >
      {JSON.stringify(run, null, 2)}
    </pre>
  );
}

function PromptsTab({
  data,
  isLoading,
  error,
}: {
  data: unknown;
  isLoading: boolean;
  error: unknown;
}) {
  if (isLoading) {
    return <div className="text-sm text-muted-foreground">Loading…</div>;
  }
  if (error) {
    return (
      <div data-testid="prompts-empty" className="text-sm text-muted-foreground">
        No prompt snapshot available.
      </div>
    );
  }
  return (
    <pre
      data-testid="prompts-body"
      className="overflow-auto rounded bg-muted p-3 text-xs"
    >
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function LogsTab({ run }: { run: AssistantOutputRecord }) {
  // The registry surfaces the last buffered lines on the record itself.
  // Live tailing via SSE lives in AKMS-bundle's Runs page; the Assistant
  // outputs page shows the terminal snapshot.
  const lines = run.last_lines ?? [];
  if (lines.length === 0) {
    return (
      <div data-testid="logs-empty" className="text-sm text-muted-foreground">
        No log lines captured.
      </div>
    );
  }
  return (
    <pre
      data-testid="logs-body"
      className="overflow-auto rounded bg-muted p-3 text-xs"
    >
      {lines.join("\n")}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Action bar
// ---------------------------------------------------------------------------

interface ActionBarProps {
  run: AssistantOutputRecord | null;
  onRerunSame: (run: AssistantOutputRecord) => void;
  onRerunDifferent: (run: AssistantOutputRecord) => void;
  onCompare: (run: AssistantOutputRecord) => void;
  onExport: (run: AssistantOutputRecord) => void;
  onOpenFolder: (run: AssistantOutputRecord) => void;
}

function ActionBar({
  run,
  onRerunSame,
  onRerunDifferent,
  onCompare,
  onExport,
  onOpenFolder,
}: ActionBarProps) {
  const disabled = run === null;
  const actions: Array<[string, string, (r: AssistantOutputRecord) => void]> = [
    ["rerun-same", "Rerun (same)", onRerunSame],
    ["rerun-different", "Rerun (different)", onRerunDifferent],
    ["compare", "Compare", onCompare],
    ["export", "Export", onExport],
    ["open-folder", "Open folder", onOpenFolder],
  ];
  return (
    <div className="flex flex-wrap gap-2" data-testid="action-bar">
      {actions.map(([key, label, handler]) => (
        <button
          key={key}
          type="button"
          data-testid={`action-${key}`}
          disabled={disabled}
          className="rounded border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
          onClick={() => run && handler(run)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tree (left pane)
// ---------------------------------------------------------------------------

interface OutputsTreeProps {
  batches: BatchNode[];
  selectedRunId: string | null;
  onSelect: (run: AssistantOutputRecord) => void;
}

function OutputsTree({ batches, selectedRunId, onSelect }: OutputsTreeProps) {
  return (
    <ul className="space-y-1 text-sm" data-testid="outputs-tree">
      {batches.map((b) => (
        <li key={b.batch_id} data-testid={`batch-node-${b.batch_id}`}>
          <div className="font-medium">{b.batch_id}</div>
          <ul className="ml-3 mt-1 space-y-1">
            {Array.from(b.workflows.values()).map((w) => (
              <li
                key={w.workflow_id}
                data-testid={`workflow-node-${w.workflow_id}`}
              >
                <div className="text-muted-foreground">{w.workflow_id}</div>
                <ul className="ml-3 mt-1 space-y-1">
                  {w.runs.map(({ run }) => (
                    <li key={run.id}>
                      <button
                        type="button"
                        data-testid={`run-node-${run.id}`}
                        className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-muted ${
                          selectedRunId === run.id ? "bg-muted" : ""
                        }`}
                        onClick={() => onSelect(run)}
                      >
                        <span className="truncate">{run.id}</span>
                        <StatusBadge status={run.status} />
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Main route
// ---------------------------------------------------------------------------

/**
 * Test-hook surface: the route ships default rerun / compare / export
 * handlers; the test suite passes its own handlers in via the optional
 * ``handlers`` prop (added for testability — production callers omit it).
 */
export interface AssistantOutputsHandlers {
  onRerunSame?: (run: AssistantOutputRecord) => void | Promise<void>;
  onRerunDifferent?: (run: AssistantOutputRecord) => void | Promise<void>;
  onCompare?: (run: AssistantOutputRecord) => void | Promise<void>;
}

export interface AssistantOutputsRouteProps {
  handlers?: AssistantOutputsHandlers;
}

export default function AssistantOutputsRoute(
  props: AssistantOutputsRouteProps = {},
) {
  const runsQ = useQuery({
    queryKey: ["assistant-outputs"],
    queryFn: listAssistantOutputs,
    retry: false,
  });
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  // Rerun-different dialog + compare overlay state.
  const [rerunDifferentRun, setRerunDifferentRun] =
    useState<AssistantOutputRecord | null>(null);
  const [compareLeftRun, setCompareLeftRun] =
    useState<AssistantOutputRecord | null>(null);
  const [compareRightRun, setCompareRightRun] =
    useState<AssistantOutputRecord | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);

  const batches = useMemo(
    () => groupRunsByBatch(runsQ.data ?? []),
    [runsQ.data],
  );
  const selectedRun = useMemo(
    () => (runsQ.data ?? []).find((r) => r.id === selectedRunId) ?? null,
    [runsQ.data, selectedRunId],
  );

  // Default handlers (overridable via composition through `handlers`).
  const defaultRerunSame = async (run: AssistantOutputRecord) => {
    try {
      await rerunRun(run.id, {});
      runsQ.refetch();
    } catch (err) {
      // Swallow + log: the action bar is the only path to this and
      // production wiring uses the handlers prop. Tests assert against
      // the call, not the resulting UI.
      // eslint-disable-next-line no-console
      console.error("rerun-same failed", err);
    }
  };

  const defaultExport = async (run: AssistantOutputRecord) => {
    try {
      const blob = await exportRun(run.id);
      // Browser-style download — production callers may opt for Tauri
      // save-dialog flows but the FE-only default works in the Vite dev
      // shell.
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${run.id}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("export failed", err);
    }
  };

  const defaultOpenFolder = async (run: AssistantOutputRecord) => {
    try {
      const res = await openRunFolder(run.id);
      // FE-only path: surfaces the absolute path so the user can copy
      // it; Tauri's shell.open is wired by the consuming shell.
      // eslint-disable-next-line no-console
      console.info("run folder:", res.path);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("open-folder failed", err);
    }
  };

  return (
    <main className="flex h-full flex-col gap-4 p-8">
      <header>
        <h1 className="text-2xl font-semibold">Outputs</h1>
        <p className="max-w-prose text-sm text-muted-foreground">
          Browse markdown, JSON, and PDF outputs produced by past workflow
          runs.
        </p>
      </header>

      <ActionBar
        run={selectedRun}
        onRerunSame={props.handlers?.onRerunSame ?? defaultRerunSame}
        onRerunDifferent={
          props.handlers?.onRerunDifferent ??
          ((run) => setRerunDifferentRun(run))
        }
        onCompare={
          props.handlers?.onCompare ??
          ((run) => {
            // Two-click flow: first compare click stakes left; second
            // compare click on a different run stakes right + opens
            // the view. Same id => friendly error.
            if (compareLeftRun === null) {
              setCompareLeftRun(run);
              setCompareError(null);
            } else if (compareLeftRun.id === run.id) {
              setCompareError(
                "Cannot compare a run to itself. Select a different run.",
              );
            } else {
              setCompareRightRun(run);
              setCompareError(null);
            }
          })
        }
        onExport={defaultExport}
        onOpenFolder={defaultOpenFolder}
      />

      {compareError && (
        <div
          role="alert"
          data-testid="compare-error"
          className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900"
        >
          {compareError}
        </div>
      )}
      {compareLeftRun && !compareRightRun && !compareError && (
        <div
          data-testid="compare-pending-right"
          className="rounded border bg-muted/30 p-3 text-sm"
        >
          Compare: <code>{compareLeftRun.id}</code> staked. Click
          "Compare" again on a different run to open the comparison
          view.{" "}
          <button
            type="button"
            data-testid="compare-cancel"
            className="ml-2 text-xs underline"
            onClick={() => {
              setCompareLeftRun(null);
              setCompareError(null);
            }}
          >
            Cancel
          </button>
        </div>
      )}

      <div className="grid flex-1 grid-cols-[300px,1fr] gap-4 overflow-hidden">
        <aside className="overflow-auto rounded border bg-card p-3">
          {runsQ.isLoading && (
            <div className="text-sm text-muted-foreground">Loading runs…</div>
          )}
          {runsQ.error && (
            <div className="text-sm text-red-600">
              Failed to load outputs.
            </div>
          )}
          {!runsQ.isLoading && batches.length === 0 && !runsQ.error && (
            <div data-testid="outputs-empty" className="text-sm text-muted-foreground">
              No Assistant runs yet.
            </div>
          )}
          {batches.length > 0 && (
            <OutputsTree
              batches={batches}
              selectedRunId={selectedRunId}
              onSelect={(run) => setSelectedRunId(run.id)}
            />
          )}
        </aside>
        <section className="overflow-auto rounded border bg-card p-3">
          {selectedRun ? (
            <DetailPane run={selectedRun} />
          ) : (
            <div data-testid="detail-empty" className="text-sm text-muted-foreground">
              Select a run to view details.
            </div>
          )}
        </section>
      </div>

      {rerunDifferentRun && (
        <RerunDifferentOverlay
          run={rerunDifferentRun}
          onClose={() => setRerunDifferentRun(null)}
        />
      )}

      {compareLeftRun && compareRightRun && (
        <ComparisonOverlay
          leftRun={compareLeftRun}
          rightRun={compareRightRun}
          onClose={() => {
            setCompareLeftRun(null);
            setCompareRightRun(null);
            setCompareError(null);
          }}
        />
      )}
    </main>
  );
}

// ---------------------------------------------------------------------------
// Rerun-different overlay
// ---------------------------------------------------------------------------

/**
 * Open the existing ``RunWorkflowDialog`` pre-filled with the source
 * run's workflow_inputs + prompt_overrides + workflow_id.
 *
 * The source data comes from two places:
 *   1. The run's ``metadata.json`` — gives us prompt_overrides +
 *      workflow_inputs (written by the assistant runner).
 *   2. The batches API — gives us the live ``EditableBatch`` so the
 *      dialog has the title / nodes / overrides context it needs.
 *
 * Until both fetches resolve the overlay shows a loading state.
 */
function RerunDifferentOverlay({
  run,
  onClose,
}: {
  run: AssistantOutputRecord;
  onClose: () => void;
}) {
  // Fetch the live batch so the dialog has its full context.
  const batchQ = useQuery({
    queryKey: ["assistant-rerun-different-batch", run.batch_id],
    queryFn: () =>
      run.batch_id ? getBatch(run.batch_id) : Promise.reject(new Error("no batch_id")),
    enabled: !!run.batch_id,
    retry: false,
  });

  // The metadata.json read happens server-side via the
  // ``GET /api/runs/{id}/effective-prompts`` route OR via reading
  // the run's metadata directly. We fall back to merging the run's
  // own ``prompt_overrides`` (if surfaced on the registry record)
  // into the batch's overrides.
  const promptsQ = useQuery({
    queryKey: ["assistant-rerun-different-prompts", run.id],
    queryFn: () => getEffectivePrompts(run.id),
    enabled: true,
    retry: false,
  });

  const batchForDialog = useMemo<EditableBatch | null>(() => {
    if (!batchQ.data) return null;
    const live = batchQ.data;
    // Pre-fill default_workflow_id with the source run's workflow.
    // Layer the source run's prompt_overrides on top of the batch's.
    const sourceOverrides: Record<string, string> =
      (run.prompt_overrides as Record<string, string>) ?? {};
    return {
      ...live,
      default_workflow_id: run.workflow_id ?? live.default_workflow_id,
      prompt_overrides: { ...live.prompt_overrides, ...sourceOverrides },
    };
  }, [batchQ.data, run]);

  if (batchQ.isLoading || promptsQ.isLoading) {
    return (
      <div
        role="dialog"
        aria-modal="true"
        data-testid="rerun-different-loading"
        className="fixed inset-0 z-40 flex items-center justify-center bg-black/40"
      >
        <div className="rounded bg-background p-4 text-sm">
          Loading source run context…
        </div>
      </div>
    );
  }
  if (batchQ.error || !batchForDialog) {
    return (
      <div
        role="dialog"
        aria-modal="true"
        data-testid="rerun-different-error"
        className="fixed inset-0 z-40 flex items-center justify-center bg-black/40"
      >
        <div className="rounded bg-background p-4 text-sm text-red-700">
          Failed to load source batch.
          <button
            type="button"
            className="ml-3 rounded border px-2 py-0.5 text-xs"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    );
  }
  return (
    <RunWorkflowDialog
      batch={batchForDialog}
      onClose={onClose}
      titleOverride={`Rerun ${run.workflow_id ?? "workflow"} with different settings`}
    />
  );
}

// ---------------------------------------------------------------------------
// Comparison overlay
// ---------------------------------------------------------------------------

function ComparisonOverlay({
  leftRun,
  rightRun,
  onClose,
}: {
  leftRun: AssistantOutputRecord;
  rightRun: AssistantOutputRecord;
  onClose: () => void;
}) {
  const leftPrompts = useQuery({
    queryKey: ["assistant-compare-prompts", leftRun.id],
    queryFn: () => getEffectivePrompts(leftRun.id),
    retry: false,
  });
  const rightPrompts = useQuery({
    queryKey: ["assistant-compare-prompts", rightRun.id],
    queryFn: () => getEffectivePrompts(rightRun.id),
    retry: false,
  });
  const leftPrimary = useQuery({
    queryKey: ["assistant-compare-primary", leftRun.id],
    queryFn: () => getPrimaryOutput(leftRun.id),
    retry: false,
  });
  const rightPrimary = useQuery({
    queryKey: ["assistant-compare-primary", rightRun.id],
    queryFn: () => getPrimaryOutput(rightRun.id),
    retry: false,
  });

  const leftPayload: ComparisonRunPayload = {
    record: leftRun,
    prompts: (leftPrompts.data as EffectivePromptsSnapshot | undefined) ?? null,
    primary: leftPrimary.data?.content ?? null,
  };
  const rightPayload: ComparisonRunPayload = {
    record: rightRun,
    prompts:
      (rightPrompts.data as EffectivePromptsSnapshot | undefined) ?? null,
    primary: rightPrimary.data?.content ?? null,
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      data-testid="run-comparison-overlay"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-5xl overflow-auto rounded bg-background p-5 shadow-lg">
        <div className="mb-3 flex justify-end">
          <button
            type="button"
            data-testid="run-comparison-close"
            className="rounded border px-2 py-0.5 text-xs"
            onClick={onClose}
          >
            Close
          </button>
        </div>
        <RunComparisonView left={leftPayload} right={rightPayload} />
      </div>
    </div>
  );
}
