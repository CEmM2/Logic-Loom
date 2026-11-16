/**
 * Runs page.
 *
 * Two-pane layout mirroring BatchesPage and WorkflowsPage:
 *   Left — runs list (id, label, workflow, status, started_at) with
 *          per-row multi-select checkboxes plus a Compare action.
 *   Right —
 *     · one selected run → run detail with status pill, live log tail,
 *       metadata, rerun button.
 *     · two or more selected runs → side-by-side comparison view
 *       rendering each run's metadata, prompt-hash, and last log lines
 *       in parallel columns.
 *
 * Live tail: when a single run is viewed and its status is non-terminal
 * (or while we want to confirm a terminal record), we open a browser-
 * native EventSource via ``api/runs::openRunStream``. The stream
 * route emits two named events:
 *   - ``log``  — JSON-encoded ``LogEvent`` (ts, level, line)
 *   - ``done`` — JSON-encoded terminal ``RunRecord``
 * EventSource cleanup is the main correctness risk; we close the handle in
 * the ``useEffect`` cleanup AND explicitly on ``done`` events so the
 * browser does not retry indefinitely after the backend completes the
 * stream. We also de-register the run from the ``GlobalRunStatus`` store
 * on terminal status.
 *
 * Comparison view scope: per the dispatch brief, we do not diff text
 * content in the UI — we render the metadata and tail lines for each
 * selected run side by side. The prompt-hash badge on each column makes
 * "two runs that only differ in prompts" trivially obvious.
 *
 * Rerun: ``rerunRun`` POSTs to ``/api/runs/{run_id}/rerun``. On success
 * we push the new ``run_id`` into the ``GlobalRunStatus`` store so the
 * global bar at the bottom of the shell reflects the live job, then
 * invalidate the runs list query.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  clearRuns,
  deleteRun,
  isTerminal,
  listRuns,
  openRunStream,
  rerunRun,
  type LogEvent,
  type RerunResponse,
  type RunRecord,
  type RunStatus,
} from "../../api/runs";
import { useGlobalRunStatusStore } from "../GlobalRunStatus";

// ── Constants ──────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<RunStatus, string> = {
  pending: "bg-slate-100 text-slate-700",
  running: "bg-amber-100 text-amber-800",
  succeeded: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-zinc-200 text-zinc-700",
};

const LOG_BUFFER_CAP = 500;

// ── Page ───────────────────────────────────────────────────────────────────

export type RunsMode = "all" | "zsum" | "akms" | "assistant";

const ASSISTANT_WORKFLOW_PREFIX = "assistant.";

function modeFilter(mode: RunsMode): (run: RunRecord) => boolean {
  switch (mode) {
    case "zsum":
      return (r) => r.kind === "zsum";
    case "akms":
      // Workflow runs that are NOT Assistant workflows. We treat any
      // workflow_id starting with "assistant." as Assistant (matches the
      // backend convention in `_is_assistant_workflow_id`).
      return (r) =>
        r.kind === "workflow" &&
        !(r.workflow_id ?? "").startsWith(ASSISTANT_WORKFLOW_PREFIX);
    case "assistant":
      return (r) =>
        r.kind === "workflow" &&
        (r.workflow_id ?? "").startsWith(ASSISTANT_WORKFLOW_PREFIX);
    case "all":
    default:
      return () => true;
  }
}

export interface RunsPageProps {
  mode?: RunsMode;
}

export function RunsPage({ mode = "all" }: RunsPageProps = {}) {
  const qc = useQueryClient();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [primaryId, setPrimaryId] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["runs", "list", mode],
    queryFn: () =>
      listRuns({
        // For zsum-only views we can push the filter to the backend; the
        // workflow vs. assistant split has no backend kind, so we always
        // fetch "workflow" then split client-side.
        kind: mode === "zsum" ? "zsum" : undefined,
        limit: 100,
      }),
    staleTime: 5_000,
  });

  const filter = useMemo(() => modeFilter(mode), [mode]);
  const runs: RunRecord[] = useMemo(
    () => (listQ.data ?? []).filter(filter),
    [listQ.data, filter],
  );

  // Compute the effective "primary" run: either the explicit user pick,
  // the single selected row in the comparison checkboxes, or the first
  // row in the list. This collapses the three sources into one id so
  // the right pane never reads stale state.
  const effectivePrimary =
    primaryId && runs.some((r) => r.id === primaryId)
      ? primaryId
      : selectedIds.length === 1 && runs.some((r) => r.id === selectedIds[0])
        ? selectedIds[0]
        : (runs[0]?.id ?? null);

  const primary = useMemo<RunRecord | null>(
    () => runs.find((r) => r.id === effectivePrimary) ?? null,
    [runs, effectivePrimary],
  );

  const selectedRuns = useMemo<RunRecord[]>(
    () => runs.filter((r) => selectedIds.includes(r.id)),
    [runs, selectedIds],
  );

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const addRun = useGlobalRunStatusStore((s) => s.addRun);
  const removeRun = useGlobalRunStatusStore((s) => s.removeRun);

  const rerunMut = useMutation({
    mutationFn: (runId: string) => rerunRun(runId),
    onSuccess: (resp: RerunResponse, sourceId: string) => {
      if (resp?.run_id) {
        addRun({ id: resp.run_id, label: `Rerun of ${sourceId}` });
      }
      qc.invalidateQueries({ queryKey: ["runs", "list"] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: (runId: string) => deleteRun(runId),
    onSuccess: (_resp, runId) => {
      setSelectedIds((prev) => prev.filter((x) => x !== runId));
      setPrimaryId((prev) => (prev === runId ? null : prev));
      qc.invalidateQueries({ queryKey: ["runs", "list"] });
    },
  });

  const clearMut = useMutation({
    mutationFn: (statuses: RunStatus[] | undefined) => clearRuns(statuses),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs", "list"] });
    },
  });

  const comparing = selectedRuns.length >= 2;

  return (
    <main className="flex h-full" aria-label="Runs" data-testid="runs-page">
      {/* ── Left: runs list ─────────────────────────────────────────── */}
      <aside
        className="flex w-80 flex-col border-r"
        aria-label="Runs list"
        data-testid="runs-list"
      >
        <header className="flex flex-col gap-2 border-b px-4 py-3">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-sm font-semibold">Runs</h1>
              <p className="text-xs text-muted-foreground">
                {listQ.isLoading
                  ? "Loading…"
                  : `${runs.length} runs · ${selectedIds.length} selected`}
              </p>
            </div>
            <button
              type="button"
              data-testid="runs-clear-selection"
              disabled={selectedIds.length === 0}
              onClick={() => setSelectedIds([])}
              className="rounded border px-2 py-0.5 text-xs hover:bg-accent disabled:opacity-40"
            >
              Clear
            </button>
          </div>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              data-testid="runs-clear-completed"
              disabled={clearMut.isPending}
              onClick={() => clearMut.mutate(["succeeded"])}
              className="rounded border px-2 py-0.5 text-[11px] hover:bg-accent disabled:opacity-40"
              title="Delete all succeeded runs"
            >
              Clear completed
            </button>
            <button
              type="button"
              data-testid="runs-clear-failed"
              disabled={clearMut.isPending}
              onClick={() => clearMut.mutate(["failed", "cancelled"])}
              className="rounded border px-2 py-0.5 text-[11px] hover:bg-accent disabled:opacity-40"
              title="Delete all failed and cancelled runs"
            >
              Clear failed
            </button>
            <button
              type="button"
              data-testid="runs-clear-all"
              disabled={clearMut.isPending}
              onClick={() => clearMut.mutate(undefined)}
              className="rounded border border-red-300 px-2 py-0.5 text-[11px] text-red-700 hover:bg-red-50 disabled:opacity-40"
              title="Delete every terminal run (succeeded, failed, cancelled)"
            >
              Clear all terminal
            </button>
          </div>
        </header>
        <ul
          className="flex-1 overflow-auto"
          role="listbox"
          aria-label="Runs"
        >
          {runs.map((r) => (
            <RunRow
              key={r.id}
              run={r}
              selected={selectedIds.includes(r.id)}
              primary={effectivePrimary === r.id}
              onSelectPrimary={() => {
                setPrimaryId(r.id);
              }}
              onToggleSelect={() => toggleSelect(r.id)}
              onDelete={
                isTerminal(r.status) ? () => deleteMut.mutate(r.id) : undefined
              }
              deletePending={deleteMut.isPending && deleteMut.variables === r.id}
            />
          ))}
          {runs.length === 0 && !listQ.isLoading && (
            <li className="px-4 py-3 text-xs text-muted-foreground">
              No runs yet.
            </li>
          )}
          {listQ.error && (
            <li className="px-4 py-3 text-xs text-red-600">
              Failed to load runs: {errMsg(listQ.error)}
            </li>
          )}
        </ul>
      </aside>

      {/* ── Right: detail or comparison ──────────────────────────────── */}
      <section
        className="flex flex-1 flex-col overflow-hidden"
        aria-label={comparing ? "Run comparison" : "Run detail"}
      >
        {comparing ? (
          <RunComparisonView runs={selectedRuns} />
        ) : primary ? (
          <RunDetailPane
            key={primary.id}
            run={primary}
            onRerun={() => rerunMut.mutate(primary.id)}
            rerunPending={rerunMut.isPending}
            onTerminal={(id) => removeRun(id)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Select a run to inspect.
          </div>
        )}
      </section>
    </main>
  );
}

// ── List row ───────────────────────────────────────────────────────────────

function RunRow({
  run,
  selected,
  primary,
  onSelectPrimary,
  onToggleSelect,
  onDelete,
  deletePending,
}: {
  run: RunRecord;
  selected: boolean;
  primary: boolean;
  onSelectPrimary: () => void;
  onToggleSelect: () => void;
  onDelete?: () => void;
  deletePending?: boolean;
}) {
  const promptHash = (run.prompt_hash ?? "").slice(0, 8);
  return (
    <li>
      <div
        data-testid={`run-row-${run.id}`}
        className={
          "flex items-start gap-2 border-b px-3 py-2 text-xs hover:bg-accent " +
          (primary ? "bg-accent" : "")
        }
      >
        <input
          type="checkbox"
          aria-label={`Select run ${run.id} for comparison`}
          data-testid={`run-select-${run.id}`}
          checked={selected}
          onChange={onToggleSelect}
          className="mt-0.5"
        />
        <button
          type="button"
          role="option"
          aria-selected={primary}
          onClick={onSelectPrimary}
          data-testid={`run-open-${run.id}`}
          className="flex-1 text-left"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-medium">
              {run.run_label || run.id}
            </span>
            <StatusPill status={run.status} />
          </div>
          <div className="truncate font-mono text-[10px] text-muted-foreground">
            {run.workflow_id || run.stage}
            {promptHash ? ` · #${promptHash}` : ""}
          </div>
          <div className="truncate font-mono text-[10px] text-muted-foreground">
            {run.started_at}
          </div>
        </button>
        {onDelete && (
          <button
            type="button"
            aria-label={`Delete run ${run.id}`}
            data-testid={`run-delete-${run.id}`}
            disabled={deletePending}
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            className="mt-0.5 rounded border border-red-300 px-1.5 py-0 text-[10px] text-red-700 hover:bg-red-50 disabled:opacity-40"
            title="Delete this run"
          >
            ✕
          </button>
        )}
      </div>
    </li>
  );
}

function StatusPill({ status }: { status: RunStatus }) {
  const cls = STATUS_COLORS[status] ?? "bg-slate-100 text-slate-700";
  return (
    <span
      data-testid={`run-status-${status}`}
      className={
        "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase " +
        cls
      }
    >
      {status}
    </span>
  );
}

// ── Detail pane ────────────────────────────────────────────────────────────

interface RunDetailPaneProps {
  run: RunRecord;
  onRerun: () => void;
  rerunPending: boolean;
  onTerminal: (id: string) => void;
}

function RunDetailPane({
  run,
  onRerun,
  rerunPending,
  onTerminal,
}: RunDetailPaneProps) {
  // Live log buffer. Seed it from the last_lines snapshot the backend
  // already returns on the GET so the pane is not blank before the
  // first SSE event arrives.
  const seed = useMemo<string[]>(
    () => (run.last_lines || []).slice(-LOG_BUFFER_CAP),
    [run.id],
    // We deliberately depend on the run id only; we don't want the
    // buffer to be reset by every list refetch that returns the same
    // run with updated last_lines.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  );
  const [logs, setLogs] = useState<string[]>(seed);
  const [streamStatus, setStreamStatus] = useState<RunStatus>(run.status);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // Reset the buffer whenever we switch to a different run.
    setLogs(seed);
    setStreamStatus(run.status);
  }, [run.id, seed, run.status]);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;

    // Don't open a stream for already-terminal runs — the backend
    // would just emit ``done`` and close. The seeded last_lines is
    // enough; opening a stream wastes a connection.
    if (isTerminal(run.status)) {
      return () => {
        // Nothing to clean up.
      };
    }

    (async () => {
      try {
        es = await openRunStream(run.id);
        if (cancelled) {
          es.close();
          return;
        }
        esRef.current = es;

        const append = (line: string) => {
          setLogs((prev) => {
            const next = [...prev, line];
            if (next.length > LOG_BUFFER_CAP) {
              return next.slice(next.length - LOG_BUFFER_CAP);
            }
            return next;
          });
        };

        const onLog = (e: MessageEvent) => {
          try {
            const ev = JSON.parse(e.data) as LogEvent;
            append(ev.line ?? String(e.data));
          } catch {
            append(String(e.data));
          }
        };
        const onDone = (e: MessageEvent) => {
          try {
            const record = JSON.parse(e.data) as Partial<RunRecord>;
            if (record?.status) {
              setStreamStatus(record.status as RunStatus);
            }
          } catch {
            // ignore parse errors — the close path is what matters.
          }
          // Terminal status → close the EventSource explicitly so the
          // browser's default reconnect does not fire.
          if (es) {
            es.close();
            esRef.current = null;
          }
          onTerminal(run.id);
        };
        const onMessage = (e: MessageEvent) => {
          // Plain ``data:`` events (no event-type) — treat as log lines.
          // This is the path the test-stub EventSource exercises.
          try {
            const parsed = JSON.parse(e.data) as Partial<LogEvent> & {
              status?: RunStatus;
            };
            if (parsed?.status && isTerminal(parsed.status)) {
              setStreamStatus(parsed.status);
              if (es) {
                es.close();
                esRef.current = null;
              }
              onTerminal(run.id);
              return;
            }
            append(parsed.line ?? String(e.data));
          } catch {
            append(String(e.data));
          }
        };

        es.addEventListener("log", onLog as EventListener);
        es.addEventListener("done", onDone as EventListener);
        es.onmessage = onMessage;
        es.onerror = () => {
          // Backend hung up — close so we don't reconnect-loop.
          if (es) {
            es.close();
            esRef.current = null;
          }
        };
      } catch {
        // openRunStream itself failed — nothing to clean up.
      }
    })();

    return () => {
      cancelled = true;
      if (es) {
        es.close();
      }
      esRef.current = null;
    };
  }, [run.id, run.status, onTerminal]);

  const displayStatus = isTerminal(streamStatus) ? streamStatus : run.status;

  return (
    <div
      className="flex flex-1 flex-col overflow-auto p-5"
      data-testid="run-detail"
    >
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">
            {run.run_label || run.id}
          </h2>
          <p className="text-xs text-muted-foreground">
            <code className="font-mono">{run.id}</code> · workflow:{" "}
            <code className="font-mono">
              {run.workflow_id || run.stage}
            </code>{" "}
            · started: <code className="font-mono">{run.started_at}</code>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusPill status={displayStatus} />
          <button
            type="button"
            data-testid="run-rerun-button"
            onClick={onRerun}
            disabled={rerunPending}
            className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-40"
            title="Re-run with the same parameters"
          >
            {rerunPending ? "Re-running…" : "Rerun"}
          </button>
        </div>
      </header>

      <section
        className="mb-3 rounded border bg-muted/20 p-3 text-xs"
        data-testid="run-metadata"
      >
        <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
          Metadata
        </h3>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[11px]">
          <dt className="text-muted-foreground">kind</dt>
          <dd>{run.kind}</dd>
          <dt className="text-muted-foreground">batch_id</dt>
          <dd>{run.batch_id ?? "—"}</dd>
          <dt className="text-muted-foreground">workflow_id</dt>
          <dd>{run.workflow_id ?? "—"}</dd>
          <dt className="text-muted-foreground">prompt_hash</dt>
          <dd data-testid="run-prompt-hash">{run.prompt_hash ?? "—"}</dd>
          <dt className="text-muted-foreground">exit_code</dt>
          <dd>{run.exit_code ?? "—"}</dd>
          <dt className="text-muted-foreground">ended_at</dt>
          <dd>{run.ended_at ?? "—"}</dd>
        </dl>
      </section>

      <section
        className="flex-1 rounded border bg-black/95 p-3 font-mono text-[11px] text-emerald-200"
        aria-label="Run log tail"
        data-testid="run-log-tail"
      >
        <h3 className="mb-2 text-xs font-semibold uppercase text-emerald-400">
          Log tail
        </h3>
        {logs.length === 0 ? (
          <p className="text-emerald-700">No log lines yet.</p>
        ) : (
          <pre className="whitespace-pre-wrap break-all">
            {logs.join("\n")}
          </pre>
        )}
      </section>
    </div>
  );
}

// ── Comparison view ───────────────────────────────────────────────────────

function RunComparisonView({ runs }: { runs: RunRecord[] }) {
  return (
    <div
      className="flex flex-1 overflow-auto"
      data-testid="run-comparison-view"
    >
      {runs.map((run) => (
        <div
          key={run.id}
          data-testid={`run-comparison-pane-${run.id}`}
          className="flex w-1/2 min-w-[280px] flex-col gap-2 border-r p-4 text-xs last:border-r-0"
        >
          <header className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-semibold">
              {run.run_label || run.id}
            </h3>
            <StatusPill status={run.status} />
          </header>
          <p className="truncate font-mono text-[10px] text-muted-foreground">
            {run.id}
          </p>

          <section className="rounded border bg-muted/20 p-2">
            <h4 className="mb-1 text-[10px] font-semibold uppercase text-muted-foreground">
              Metadata
            </h4>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-2 gap-y-0.5 font-mono text-[10px]">
              <dt className="text-muted-foreground">workflow</dt>
              <dd className="truncate">
                {run.workflow_id || run.stage}
              </dd>
              <dt className="text-muted-foreground">batch</dt>
              <dd className="truncate">{run.batch_id ?? "—"}</dd>
              <dt className="text-muted-foreground">started</dt>
              <dd className="truncate">{run.started_at}</dd>
              <dt className="text-muted-foreground">ended</dt>
              <dd className="truncate">{run.ended_at ?? "—"}</dd>
              <dt className="text-muted-foreground">prompt#</dt>
              <dd
                className="truncate"
                data-testid={`run-comparison-prompt-hash-${run.id}`}
              >
                {run.prompt_hash ?? "—"}
              </dd>
              <dt className="text-muted-foreground">exit</dt>
              <dd>{run.exit_code ?? "—"}</dd>
            </dl>
          </section>

          <section className="flex-1 rounded border bg-black/95 p-2 font-mono text-[10px] text-emerald-200">
            <h4 className="mb-1 text-[10px] font-semibold uppercase text-emerald-400">
              Last log lines
            </h4>
            {run.last_lines && run.last_lines.length > 0 ? (
              <pre className="whitespace-pre-wrap break-all">
                {run.last_lines.join("\n")}
              </pre>
            ) : (
              <p className="text-emerald-700">No log lines.</p>
            )}
          </section>
        </div>
      ))}
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default RunsPage;
