/**
 * Assistant Batches sub-tab.
 *
 * Two-pane layout. Left: BatchList. Right: tabbed detail with Overview,
 * Sources, Notebook, Workflow defaults, Prompt defaults, Run history,
 * and the Send-to-AKMS button.
 *
 * Each tab owns its own PATCH (or sources/{add,remove}) mutation; the
 * BatchesPage owns the queries and invalidates them on every mutation
 * success so the next render sees fresh server state.
 */
import { useEffect, useMemo, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  addAssistantBatchSources,
  archiveAssistantBatch,
  createAssistantBatch,
  deleteAssistantBatch,
  duplicateAssistantBatch,
  getAssistantBatch,
  listAssistantBatches,
  patchAssistantBatch,
  removeAssistantBatchSources,
  type AssistantBatch,
  type BatchSourceItem,
  type PatchAssistantBatchBody,
} from "../../api/assistantBatches";
import { BatchList } from "../../components/assistant/batches/BatchList";
import { BatchNotebookPanel } from "../../components/assistant/batches/BatchNotebookPanel";
import { BatchOverviewEditor } from "../../components/assistant/batches/BatchOverviewEditor";
import { BatchPromptDefaults } from "../../components/assistant/batches/BatchPromptDefaults";
import { BatchRunHistory } from "../../components/assistant/batches/BatchRunHistory";
import { BatchSourcesTable } from "../../components/assistant/batches/BatchSourcesTable";
import { BatchWorkflowDefaults } from "../../components/assistant/batches/BatchWorkflowDefaults";
import { SendBatchToWorkflowButton } from "../../components/assistant/batches/SendBatchToWorkflowButton";
import { RelatedPaperPanel } from "../../components/assistant/RelatedPaperPanel";
import { discoveredPaperToSourceItem } from "../../api/assistantDiscover";

type DetailTabKey =
  | "overview"
  | "sources"
  | "notebook"
  | "workflows"
  | "prompts"
  | "related"
  | "runs";

const DETAIL_TABS: { key: DetailTabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "sources", label: "Sources" },
  { key: "notebook", label: "Notebook" },
  { key: "workflows", label: "Workflows" },
  { key: "prompts", label: "Prompts" },
  { key: "related", label: "Related" },
  { key: "runs", label: "Runs" },
];

const LIST_KEY = ["assistant", "batches", "list"] as const;
const DETAIL_KEY = (id: string) =>
  ["assistant", "batches", "detail", id] as const;

export default function AssistantBatchesRoute() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<DetailTabKey>("overview");
  const [newTitle, setNewTitle] = useState("");

  const listQuery = useQuery({
    queryKey: LIST_KEY,
    queryFn: listAssistantBatches,
    staleTime: 5_000,
  });

  const batches = listQuery.data?.batches ?? [];
  const effectiveSelectedId =
    selectedId ?? (batches.length > 0 ? batches[0].id : null);

  useEffect(() => {
    if (selectedId === null && batches.length > 0) {
      setSelectedId(batches[0].id);
    }
  }, [selectedId, batches]);

  const detailQuery = useQuery({
    enabled: !!effectiveSelectedId,
    queryKey: effectiveSelectedId
      ? DETAIL_KEY(effectiveSelectedId)
      : ["assistant", "batches", "detail", "none"],
    queryFn: () =>
      effectiveSelectedId
        ? getAssistantBatch(effectiveSelectedId)
        : Promise.reject(new Error("no selected batch")),
  });

  const selected: AssistantBatch | undefined = detailQuery.data;

  function invalidateAll() {
    qc.invalidateQueries({ queryKey: LIST_KEY });
    if (effectiveSelectedId) {
      qc.invalidateQueries({ queryKey: DETAIL_KEY(effectiveSelectedId) });
    }
  }

  const createMutation = useMutation({
    mutationFn: (title: string) => createAssistantBatch({ title }),
    onSuccess: (b) => {
      setSelectedId(b.id);
      setNewTitle("");
      invalidateAll();
    },
  });

  const patchMutation = useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: PatchAssistantBatchBody;
    }) => patchAssistantBatch(id, patch),
    // The PATCH response *is* the new authoritative state — prime the
    // detail cache with it and force a refetch of the LIST query so the
    // sidebar reflects updated_at / title changes.
    onSuccess: (updated) => {
      qc.setQueryData(DETAIL_KEY(updated.id), updated);
      qc.invalidateQueries({ queryKey: LIST_KEY });
      // Belt + suspenders: force a refetch of the detail query as well
      // in case anything downstream depends on a fresh server snapshot
      // (e.g. send-to-AKMS). React Query will reuse the primed cache
      // until the refetch lands so the form stays stable in the gap.
      qc.refetchQueries({ queryKey: DETAIL_KEY(updated.id), exact: true });
    },
  });

  const archiveMutation = useMutation({
    mutationFn: (id: string) => archiveAssistantBatch(id),
    onSuccess: invalidateAll,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAssistantBatch(id),
    onSuccess: (_removed, removedId) => {
      setSelectedId((prev) => (prev === removedId ? null : prev));
      invalidateAll();
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: (id: string) => duplicateAssistantBatch(id),
    onSuccess: (clone) => {
      setSelectedId(clone.id);
      invalidateAll();
    },
  });

  const addSourcesMutation = useMutation({
    mutationFn: ({
      id,
      sources,
    }: {
      id: string;
      sources: BatchSourceItem[];
    }) => addAssistantBatchSources(id, sources),
    onSuccess: invalidateAll,
  });

  const removeSourcesMutation = useMutation({
    mutationFn: ({ id, ids }: { id: string; ids: string[] }) =>
      removeAssistantBatchSources(id, ids),
    onSuccess: invalidateAll,
  });

  const headerActionsDisabled =
    !effectiveSelectedId ||
    archiveMutation.isPending ||
    duplicateMutation.isPending;

  const detailLoading = !!effectiveSelectedId && detailQuery.isLoading;

  const tabBody = useMemo(() => {
    if (!selected) {
      return (
        <div
          className="p-6 text-sm text-muted-foreground"
          data-testid="batch-detail-empty"
        >
          {detailLoading
            ? "Loading batch…"
            : "Select a batch from the list to edit it."}
        </div>
      );
    }
    if (tab === "overview") {
      return (
        <BatchOverviewEditor
          batch={selected}
          saving={patchMutation.isPending}
          onSave={(patch) =>
            patchMutation.mutate({ id: selected.id, patch })
          }
        />
      );
    }
    if (tab === "sources") {
      const busy =
        addSourcesMutation.isPending || removeSourcesMutation.isPending;
      return (
        <BatchSourcesTable
          batch={selected}
          busy={busy}
          onAdd={(sources) =>
            addSourcesMutation.mutate({ id: selected.id, sources })
          }
          onRemove={(ids) =>
            removeSourcesMutation.mutate({ id: selected.id, ids })
          }
        />
      );
    }
    if (tab === "notebook") {
      return (
        <BatchNotebookPanel
          batch={selected}
          saving={patchMutation.isPending}
          onSave={(notebook_id) =>
            patchMutation.mutate({
              id: selected.id,
              patch: { notebook_id },
            })
          }
        />
      );
    }
    if (tab === "workflows") {
      return (
        <BatchWorkflowDefaults
          batch={selected}
          saving={patchMutation.isPending}
          saveError={
            patchMutation.isError
              ? patchMutation.error instanceof Error
                ? patchMutation.error.message
                : String(patchMutation.error)
              : null
          }
          onSave={(default_workflow_id) => {
            patchMutation.reset();
            patchMutation.mutate({
              id: selected.id,
              patch: { default_workflow_id },
            });
          }}
        />
      );
    }
    if (tab === "prompts") {
      return (
        <BatchPromptDefaults
          batch={selected}
          saving={patchMutation.isPending}
          saveError={
            patchMutation.isError
              ? patchMutation.error instanceof Error
                ? patchMutation.error.message
                : String(patchMutation.error)
              : null
          }
          onSave={(prompt_profile_id, prompt_overrides) => {
            patchMutation.reset();
            patchMutation.mutate({
              id: selected.id,
              patch: { prompt_profile_id, prompt_overrides },
            });
          }}
        />
      );
    }
    if (tab === "related") {
      return (
        <RelatedPaperPanel
          batch={selected}
          onAddOne={(paper) =>
            addSourcesMutation.mutate({
              id: selected.id,
              sources: [discoveredPaperToSourceItem(paper)],
            })
          }
        />
      );
    }
    return <BatchRunHistory batch={selected} />;
  }, [
    selected,
    tab,
    detailLoading,
    patchMutation,
    addSourcesMutation,
    removeSourcesMutation,
  ]);

  return (
    <main className="flex h-full flex-col gap-4 p-6" data-testid="assistant-batches-route">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">Batches</h1>
        <p className="text-sm text-muted-foreground max-w-prose">
          Group papers from Zotero, discovery, or manual inputs into
          source batches that workflows consume.
        </p>
      </header>

      <section className="flex gap-2 rounded-lg border bg-card p-3">
        <input
          data-testid="new-batch-title"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          placeholder="New batch title…"
          className="flex-1 rounded border px-2 py-1 text-sm"
        />
        <button
          type="button"
          data-testid="new-batch-create"
          disabled={!newTitle.trim() || createMutation.isPending}
          onClick={() => createMutation.mutate(newTitle.trim())}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          {createMutation.isPending ? "Creating…" : "New batch"}
        </button>
      </section>

      <section className="grid flex-1 min-h-0 grid-cols-[18rem,1fr] gap-4 overflow-hidden">
        <div className="overflow-y-auto rounded-lg border bg-card">
          <BatchList
            batches={batches}
            selectedId={effectiveSelectedId}
            isLoading={listQuery.isLoading}
            onSelect={(id) => {
              setSelectedId(id);
              setTab("overview");
            }}
          />
        </div>

        <div className="flex min-h-0 flex-col rounded-lg border bg-card">
          {effectiveSelectedId && (
            <div className="flex items-center justify-between border-b px-4 py-2">
              <nav
                role="tablist"
                aria-label="Batch detail tabs"
                className="flex gap-1"
              >
                {DETAIL_TABS.map((t) => {
                  const active = t.key === tab;
                  return (
                    <button
                      key={t.key}
                      type="button"
                      role="tab"
                      data-testid={`tab-${t.key}`}
                      aria-selected={active}
                      onClick={() => setTab(t.key)}
                      className={
                        "rounded px-2 py-1 text-xs " +
                        (active
                          ? "bg-muted font-medium"
                          : "text-muted-foreground hover:bg-muted/50")
                      }
                    >
                      {t.label}
                    </button>
                  );
                })}
              </nav>
              <div className="flex gap-2">
                <button
                  type="button"
                  data-testid="batch-action-duplicate"
                  disabled={headerActionsDisabled}
                  onClick={() =>
                    effectiveSelectedId &&
                    duplicateMutation.mutate(effectiveSelectedId)
                  }
                  className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                >
                  Duplicate
                </button>
                <button
                  type="button"
                  data-testid="batch-action-archive"
                  disabled={headerActionsDisabled}
                  onClick={() =>
                    effectiveSelectedId &&
                    archiveMutation.mutate(effectiveSelectedId)
                  }
                  className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                >
                  Archive
                </button>
                <button
                  type="button"
                  data-testid="batch-action-delete"
                  disabled={headerActionsDisabled || deleteMutation.isPending}
                  onClick={() => {
                    if (!effectiveSelectedId) return;
                    const ok = window.confirm(
                      "Delete this batch permanently? This cannot be undone — the batch's source list and research-memory index entries will be removed. Use Archive instead if you only want to hide it.",
                    );
                    if (ok) deleteMutation.mutate(effectiveSelectedId);
                  }}
                  className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  Delete
                </button>
                {effectiveSelectedId && (
                  <SendBatchToWorkflowButton batchId={effectiveSelectedId} />
                )}
              </div>
            </div>
          )}
          <div className="flex-1 overflow-y-auto p-4">{tabBody}</div>
        </div>
      </section>
    </main>
  );
}
