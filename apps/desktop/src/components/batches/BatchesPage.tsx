/**
 * AKMS Batches page.
 *
 * Left pane: BatchList (selectable, shows id/title/status).
 * Right pane: BatchDetail with sub-tabs (Overview / Sources / Nodes /
 *   Notebook / Workflows / Runs / Outputs / Settings).
 * Actions: Edit (Overview/Settings tabs PATCH back), Run workflow
 *   (RunWorkflowDialog).
 *
 * Contracts:
 *   - editing a batch posts a PATCH and the next GET reflects the change.
 *   - the Run dialog's "Preview effective prompts" renders the resolved
 *     variants from POST /api/runs/workflow?dry_run=true.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import {
  archiveBatch,
  createBatch,
  deleteBatch,
  getBatch,
  listBatches,
  patchBatch,
  type EditableBatch,
} from "../../api/batches";
import { listProfiles } from "../../api/promptLibrary";
import { listRuns, type RunRecord } from "../../api/runs";
import { listWorkflows } from "../../api/workflows";
import { RunWorkflowDialog } from "./RunWorkflowDialog";
import { StarterBadge, starterRunCopy } from "./StarterBadge";

type TabKey =
  | "overview"
  | "sources"
  | "nodes"
  | "notebook"
  | "workflows"
  | "runs"
  | "outputs"
  | "settings";

const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "sources", label: "Sources" },
  { key: "nodes", label: "Nodes" },
  { key: "notebook", label: "Notebook" },
  { key: "workflows", label: "Workflows" },
  { key: "runs", label: "Runs" },
  { key: "outputs", label: "Outputs" },
  { key: "settings", label: "Settings" },
];

export function BatchesPage() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("overview");
  const [addError, setAddError] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  // Captured once and cleared from local state after first use so the
  // run dialog only auto-opens on the initial navigation, not every
  // re-render. Callers (Workflows → "Run with this workflow") deep-link
  // here with ?workflow_id=...
  const [pendingWorkflowId, setPendingWorkflowId] = useState<string | null>(
    searchParams.get("workflow_id"),
  );

  useEffect(() => {
    const wid = searchParams.get("workflow_id");
    if (wid && wid !== pendingWorkflowId) setPendingWorkflowId(wid);
    // We deliberately don't depend on pendingWorkflowId so flipping it to
    // null inside the consumer doesn't immediately re-arm.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const { data: listData, isLoading: listLoading, error: listError } = useQuery({
    queryKey: ["akms", "batches", "list"],
    queryFn: listBatches,
    staleTime: 10_000,
  });

  const batches = listData?.batches ?? [];
  const effectiveSelected = selectedId ?? batches[0]?.id ?? null;

  const addBatchMut = useMutation({
    mutationFn: () => {
      const stamp = new Date().toISOString().replace(/[-:T.]/g, "").slice(0, 14);
      return createBatch({ title: `New batch ${stamp}` });
    },
    onSuccess: (batch) => {
      setSelectedId(batch.id);
      setAddError(null);
      qc.invalidateQueries({ queryKey: ["akms", "batches", "list"] });
    },
    onError: (e: unknown) => setAddError(errMsg(e)),
  });

  return (
    <main className="flex h-full" aria-label="AKMS Batches">
      <aside
        className="flex w-72 flex-col border-r"
        aria-label="Batch list"
        data-testid="batches-list"
      >
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div>
            <h1 className="text-sm font-semibold">Batches</h1>
            <p className="text-xs text-muted-foreground">
              {listLoading ? "Loading…" : `${batches.length} batches`}
            </p>
          </div>
          <button
            type="button"
            data-testid="batches-add-button"
            className="rounded border px-2 py-0.5 text-xs hover:bg-accent disabled:opacity-40"
            title="Add batch"
            disabled={addBatchMut.isPending}
            onClick={() => addBatchMut.mutate()}
          >
            {addBatchMut.isPending ? "Adding…" : "+ Add"}
          </button>
        </header>
        {addError && (
          <div className="border-b px-4 py-2 text-xs text-red-600">{addError}</div>
        )}
        <ul className="flex-1 overflow-auto" role="listbox" aria-label="Batches">
          {batches.map((b) => (
            <li key={b.id}>
              <button
                type="button"
                role="option"
                aria-selected={effectiveSelected === b.id}
                onClick={() => setSelectedId(b.id)}
                data-testid={`batch-row-${b.id}`}
                className={
                  "w-full border-b px-4 py-2 text-left text-sm hover:bg-accent " +
                  (effectiveSelected === b.id ? "bg-accent" : "")
                }
              >
                <div className="flex items-center gap-1.5 truncate">
                  <span className="truncate font-medium">{b.title}</span>
                  <StarterBadge origin={b.origin} />
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {b.id} · {b.status}
                </div>
              </button>
            </li>
          ))}
          {!listLoading && batches.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted-foreground">No batches.</li>
          )}
          {listError && (
            <li className="px-4 py-3 text-xs text-red-600">
              Failed to load batches: {errMsg(listError)}
            </li>
          )}
        </ul>
      </aside>

      <section className="flex flex-1 flex-col">
        {effectiveSelected ? (
          <BatchDetail
            batchId={effectiveSelected}
            tab={tab}
            onTabChange={setTab}
            onDeleted={() => setSelectedId(null)}
            pendingWorkflowId={pendingWorkflowId}
            onConsumePendingWorkflow={() => setPendingWorkflowId(null)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Select a batch to view details.
          </div>
        )}
      </section>
    </main>
  );
}

// ── Detail pane ──────────────────────────────────────────────────────────────

interface DetailProps {
  batchId: string;
  tab: TabKey;
  onTabChange: (k: TabKey) => void;
  onDeleted: () => void;
  pendingWorkflowId: string | null;
  onConsumePendingWorkflow: () => void;
}

function BatchDetail({
  batchId,
  tab,
  onTabChange,
  onDeleted,
  pendingWorkflowId,
  onConsumePendingWorkflow,
}: DetailProps) {
  const qc = useQueryClient();
  const { data: batch, isLoading } = useQuery({
    queryKey: ["akms", "batch", batchId],
    queryFn: () => getBatch(batchId),
    enabled: !!batchId,
  });

  const archiveMut = useMutation({
    mutationFn: () => archiveBatch(batchId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["akms", "batch", batchId] });
      qc.invalidateQueries({ queryKey: ["akms", "batches", "list"] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteBatch(batchId),
    onSuccess: () => {
      onDeleted();
      qc.invalidateQueries({ queryKey: ["akms", "batches", "list"] });
    },
  });

  const patchMut = useMutation({
    mutationFn: (body: Parameters<typeof patchBatch>[1]) => patchBatch(batchId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["akms", "batch", batchId] });
      qc.invalidateQueries({ queryKey: ["akms", "batches", "list"] });
    },
  });

  const [runDialogOpen, setRunDialogOpen] = useState(false);

  // Auto-open the Run dialog when the user deep-linked here with a
  // workflow_id query param (e.g. from Workflows → "Run with this workflow").
  useEffect(() => {
    if (pendingWorkflowId && batch && !runDialogOpen) {
      setRunDialogOpen(true);
    }
  }, [pendingWorkflowId, batch, runDialogOpen]);

  if (isLoading || !batch) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Loading batch…
      </div>
    );
  }

  return (
    <>
      <header className="flex items-center justify-between border-b px-6 py-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">{batch.title}</h2>
            <StarterBadge origin={batch.origin} />
          </div>
          <p className="text-xs text-muted-foreground">
            {batch.id} · {batch.status}
          </p>
          {batch.origin === "starter" && (
            <p
              data-testid="starter-tier-b-copy"
              className="mt-1 text-xs text-amber-700"
            >
              {starterRunCopy()}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {batch.status !== "archived" && (
            <button
              type="button"
              data-testid="archive-batch-button"
              onClick={() => archiveMut.mutate()}
              disabled={archiveMut.isPending}
              className="rounded border px-3 py-1 text-xs hover:bg-accent disabled:opacity-50"
              title="Mark batch as archived (soft delete)"
            >
              {archiveMut.isPending ? "Archiving…" : "Archive"}
            </button>
          )}
          <button
            type="button"
            data-testid="delete-batch-button"
            onClick={() => {
              if (
                window.confirm(
                  `Delete batch "${batch.title}"? This cannot be undone.`,
                )
              ) {
                deleteMut.mutate();
              }
            }}
            disabled={deleteMut.isPending}
            className="rounded border border-red-300 px-3 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
            title="Hard-delete this batch"
          >
            {deleteMut.isPending ? "Deleting…" : "Delete"}
          </button>
          <button
            type="button"
            className="rounded bg-foreground px-3 py-1 text-sm text-background hover:opacity-90"
            data-testid="run-workflow-button"
            onClick={() => setRunDialogOpen(true)}
          >
            Run workflow…
          </button>
        </div>
      </header>

      <nav
        className="flex gap-1 border-b px-4 py-2 text-sm"
        role="tablist"
        aria-label="Batch sections"
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            aria-controls={`batch-tab-panel-${t.key}`}
            onClick={() => onTabChange(t.key)}
            data-testid={`batch-tab-${t.key}`}
            className={
              "rounded px-3 py-1 " +
              (tab === t.key
                ? "bg-accent font-medium"
                : "hover:bg-accent/50 text-muted-foreground")
            }
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div
        className="flex-1 overflow-auto p-6"
        role="tabpanel"
        id={`batch-tab-panel-${tab}`}
      >
        {tab === "overview" && <OverviewTab batch={batch} onPatch={patchMut.mutate} pending={patchMut.isPending} />}
        {tab === "sources" && <ReadonlyTextTab label="Sources" text={batch.sources_text} />}
        {tab === "nodes" && <NodesTab batch={batch} />}
        {tab === "notebook" && <NotebookTab batch={batch} />}
        {tab === "workflows" && <WorkflowsTab batch={batch} />}
        {tab === "runs" && <RunsTab batch={batch} />}
        {tab === "outputs" && <OutputsTab batch={batch} />}
        {tab === "settings" && <SettingsTab batch={batch} onPatch={patchMut.mutate} pending={patchMut.isPending} />}
      </div>

      {runDialogOpen && (
        <RunWorkflowDialog
          batch={batch}
          defaultWorkflowId={pendingWorkflowId ?? undefined}
          onClose={() => {
            setRunDialogOpen(false);
            if (pendingWorkflowId) onConsumePendingWorkflow();
          }}
        />
      )}
    </>
  );
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

interface PatchableTabProps {
  batch: EditableBatch;
  onPatch: (body: Parameters<typeof patchBatch>[1]) => void;
  pending: boolean;
}

function OverviewTab({ batch, onPatch, pending }: PatchableTabProps) {
  const [title, setTitle] = useState(batch.title);
  const [description, setDescription] = useState(batch.description ?? "");

  const dirty = title !== batch.title || description !== (batch.description ?? "");

  return (
    <form
      data-testid="batch-overview-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!dirty) return;
        onPatch({ title, description });
      }}
      className="flex max-w-2xl flex-col gap-4"
    >
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">Title</span>
        <input
          data-testid="batch-title-input"
          className="rounded border px-2 py-1 text-sm"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">Description</span>
        <textarea
          data-testid="batch-description-input"
          rows={5}
          className="rounded border px-2 py-1 text-sm"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={!dirty || pending}
          data-testid="batch-overview-save"
          className="rounded bg-foreground px-3 py-1 text-sm text-background disabled:opacity-40"
        >
          {pending ? "Saving…" : "Save"}
        </button>
        <span className="text-xs text-muted-foreground">
          Round {batch.round ?? "—"} · {batch.papers.length} papers · {batch.nodes.length} nodes
        </span>
      </div>
    </form>
  );
}

function ReadonlyTextTab({ label, text }: { label: string; text: string }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">{label}</h3>
      {label === "Sources" && (
        <p className="rounded border border-blue-300 bg-blue-50 px-3 py-2 text-xs text-blue-900">
          AKMS batches view sources read-only. To curate sources, create or
          edit the batch from{" "}
          <a
            href="/assistant/batches"
            className="font-medium underline hover:no-underline"
          >
            Assistant → Batches
          </a>{" "}
          — the Zotero picker and discovery flow live there.
        </p>
      )}
      <pre className="whitespace-pre-wrap rounded border bg-muted/30 p-3 text-xs">
        {text || "(empty)"}
      </pre>
    </section>
  );
}

function NodesTab({ batch }: { batch: EditableBatch }) {
  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold">Nodes ({batch.nodes.length})</h3>
      {batch.nodes.length === 0 ? (
        <p className="text-sm text-muted-foreground">No nodes assigned.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {batch.nodes.map((n) => (
            <li key={n.id} className="rounded border px-3 py-2">
              <div className="font-mono text-xs">{n.id}</div>
              {n.title ? <div className="text-sm">{n.title}</div> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function NotebookTab({ batch }: { batch: EditableBatch }) {
  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold">Notebook</h3>
      <p className="text-sm">
        Notebook ID:{" "}
        <code className="rounded bg-muted px-1 font-mono text-xs">
          {batch.notebook_id ?? "(none)"}
        </code>
      </p>
    </section>
  );
}

function WorkflowsTab({ batch }: { batch: EditableBatch }) {
  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold">Workflows</h3>
      <p className="text-sm">
        Default workflow:{" "}
        <code className="rounded bg-muted px-1 font-mono text-xs">
          {batch.default_workflow_id ?? "(none)"}
        </code>
      </p>
      <p className="text-xs text-muted-foreground">
        Use the "Run workflow…" button at the top right to start a run against this batch.
      </p>
    </section>
  );
}

function RunsTab({ batch }: { batch: EditableBatch }) {
  const runsQ = useQuery({
    queryKey: ["runs", "by-batch", batch.id],
    queryFn: () => listRuns({ limit: 200 }),
    refetchInterval: 5_000,
  });

  const runs: RunRecord[] = (runsQ.data ?? []).filter((r) => r.batch_id === batch.id);

  return (
    <section className="flex flex-col gap-2">
      <header className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Runs ({runs.length})</h3>
        <button
          type="button"
          onClick={() => runsQ.refetch()}
          className="rounded border px-2 py-0.5 text-xs hover:bg-accent"
        >
          Refresh
        </button>
      </header>
      {runsQ.isLoading && (
        <p className="text-xs text-muted-foreground">Loading runs…</p>
      )}
      {!runsQ.isLoading && runs.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No runs for this batch yet. Use "Run workflow…" to start one.
        </p>
      )}
      {runs.length > 0 && (
        <ul className="space-y-1 text-xs">
          {runs.map((r) => (
            <li
              key={r.id}
              data-testid={`batch-run-${r.id}`}
              className="grid grid-cols-[1fr_auto_auto] items-center gap-3 rounded border px-3 py-2"
            >
              <div className="truncate">
                <span className="font-mono text-[11px]">{r.id.slice(0, 8)}</span>
                {r.workflow_id && (
                  <span className="ml-2 text-muted-foreground">{r.workflow_id}</span>
                )}
              </div>
              <span className={statusClass(r.status)}>{r.status}</span>
              <span className="text-muted-foreground">{r.started_at?.slice(0, 19) ?? ""}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function statusClass(status: string): string {
  if (status === "succeeded") return "text-emerald-700";
  if (status === "failed") return "text-red-600";
  if (status === "running") return "text-blue-600";
  if (status === "cancelled") return "text-amber-600";
  return "text-muted-foreground";
}

function OutputsTab({ batch }: { batch: EditableBatch }) {
  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold">Outputs</h3>
      <p className="text-sm text-muted-foreground">
        Output folders for {batch.title} are surfaced from the most recent
        run's manifest (per-run output layout).
      </p>
    </section>
  );
}

function SettingsTab({ batch, onPatch, pending }: PatchableTabProps) {
  const [profile, setProfile] = useState(batch.prompt_profile_id ?? "");
  const [workflow, setWorkflow] = useState(batch.default_workflow_id ?? "");

  // Re-sync the form whenever the persisted batch fields change.
  // Without this the local useState init only runs once per mount; after
  // a successful save the patchMutation invalidates and refetches the
  // batch, but the form's `workflow` / `profile` state stays stale —
  // making it look like the save "reverted" when the user navigated
  // away and back.
  useEffect(() => {
    setProfile(batch.prompt_profile_id ?? "");
    setWorkflow(batch.default_workflow_id ?? "");
  }, [batch.id, batch.prompt_profile_id, batch.default_workflow_id]);

  const dirty = profile !== (batch.prompt_profile_id ?? "") || workflow !== (batch.default_workflow_id ?? "");
  const savedClean =
    !dirty &&
    !pending &&
    (batch.default_workflow_id || batch.prompt_profile_id);

  const profilesQ = useQuery({ queryKey: ["prompts", "profiles"], queryFn: listProfiles });
  const workflowsQ = useQuery({
    queryKey: ["workflows", "list"],
    queryFn: () => listWorkflows(false),
  });
  const profiles = profilesQ.data?.profiles ?? [];
  const workflows = workflowsQ.data?.workflows ?? [];

  return (
    <form
      data-testid="batch-settings-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!dirty) return;
        onPatch({
          prompt_profile_id: profile || null,
          default_workflow_id: workflow || null,
        });
      }}
      className="flex max-w-xl flex-col gap-4"
    >
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Default prompt profile
        </span>
        <select
          data-testid="batch-profile-input"
          className="rounded border px-2 py-1 text-sm"
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
        >
          <option value="">(none — use global active)</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.id})
            </option>
          ))}
        </select>
        {profilesQ.isLoading && (
          <span className="text-[10px] text-muted-foreground">Loading profiles…</span>
        )}
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Default workflow
        </span>
        <select
          data-testid="batch-workflow-input"
          className="rounded border px-2 py-1 text-sm"
          value={workflow}
          onChange={(e) => setWorkflow(e.target.value)}
        >
          <option value="">(none)</option>
          {workflows.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name} ({w.id})
            </option>
          ))}
        </select>
        {workflowsQ.isLoading && (
          <span className="text-[10px] text-muted-foreground">Loading workflows…</span>
        )}
      </label>
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={!dirty || pending}
          data-testid="batch-settings-save"
          className="self-start rounded bg-foreground px-3 py-1 text-sm text-background disabled:opacity-40"
        >
          {pending ? "Saving…" : "Save"}
        </button>
        {savedClean && (
          <span
            data-testid="batch-settings-saved-indicator"
            className="text-xs text-emerald-700"
          >
            ✓ Saved.
          </span>
        )}
      </div>
    </form>
  );
}

// ── Run dialog ───────────────────────────────────────────────────────────────
// RunWorkflowDialog lives in ./RunWorkflowDialog.tsx so the Assistant
// wrapper can reuse it; the BatchesPage caller above imports it
// component with no extra props (preserving the AKMS batches default
// of "all workflows visible").

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default BatchesPage;
