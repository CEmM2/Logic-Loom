/**
 * Assistant Discover sub-tab.
 *
 * The live OpenAlex / Semantic Scholar
 * search page. Calls `/api/assistant/discover/search` on submit,
 * surfaces ZoteroStatusBadge + PdfAvailabilityBadge per row, and lets
 * the user add results to an existing batch (AddToBatchDialog) or
 * create a fresh batch from the current result set
 * (CreateBatchFromResultsDialog).
 *
 * No hardcoded `/assistant/*` URL strings — `ASSISTANT_ROUTE_BATCHES`
 * is consumed from `./routes` for the cross-tab link.
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  discoverSearch,
  type DiscoverProviderError,
  type DiscoverSearchFilters,
  type DiscoverSearchResponse,
  type DiscoveredPaperWithMatch,
} from "../../api/assistantDiscover";
import {
  listAssistantBatches,
  type AssistantBatch,
} from "../../api/assistantBatches";

import { DiscoverySearchBar } from "../../components/assistant/discover/DiscoverySearchBar";
import { DiscoveryFilters } from "../../components/assistant/discover/DiscoveryFilters";
import { DiscoveryResultsTable } from "../../components/assistant/discover/DiscoveryResultsTable";
import { AddToBatchDialog } from "../../components/assistant/discover/AddToBatchDialog";
import { CreateBatchFromResultsDialog } from "../../components/assistant/discover/CreateBatchFromResultsDialog";

import { ASSISTANT_ROUTE_BATCHES } from "./routes";

const PROVIDER_NAMES: Record<string, string> = {
  openalex: "OpenAlex",
  semantic_scholar: "Semantic Scholar",
};

export function describeProviderError(e: DiscoverProviderError): string {
  const name = PROVIDER_NAMES[e.provider] ?? e.provider;
  if (e.error === "provider_rate_limited") {
    const wait = e.retry_after ? ` Try again in about ${e.retry_after}s.` : " Try again shortly.";
    const key =
      e.provider === "semantic_scholar"
        ? " A Semantic Scholar API key (Settings) raises the limit."
        : "";
    return `${name} is rate-limiting requests, so these results come from the other provider only.${wait}${key}`;
  }
  return `${name} did not answer (${e.message}), so these results come from the other provider only.`;
}

export default function AssistantDiscoverRoute() {
  // Filter state — pinned default: only_with_pdf = false (HARD RULE).
  const [filters, setFilters] = useState<DiscoverSearchFilters>({
    only_with_pdf: false,
  });
  const [resolvedQuery, setResolvedQuery] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [addDialog, setAddDialog] = useState<{
    isOpen: boolean;
    papers: DiscoveredPaperWithMatch[];
  }>({ isOpen: false, papers: [] });
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  // The Batches list (right-pane dialogs feed off this).
  const batchesQuery = useQuery({
    queryKey: ["assistant", "batches", "list"],
    queryFn: () => listAssistantBatches(),
  });
  const batches: AssistantBatch[] = batchesQuery.data?.batches ?? [];

  // The Search call.
  const searchMutation = useMutation<
    DiscoverSearchResponse,
    Error,
    { query: string; filters: DiscoverSearchFilters }
  >({
    mutationFn: (vars) =>
      discoverSearch({ query: vars.query, filters: vars.filters }),
  });
  const results = searchMutation.data?.results ?? [];
  const cached = searchMutation.data?.cached ?? false;
  const providerErrors = searchMutation.data?.provider_errors ?? [];

  function toggleSelected(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleAddOne(paper: DiscoveredPaperWithMatch) {
    setAddDialog({ isOpen: true, papers: [paper] });
  }

  function handleAddSelected() {
    const selectedPapers = results.filter((p) => selectedIds.has(p.id));
    if (selectedPapers.length === 0) return;
    setAddDialog({ isOpen: true, papers: selectedPapers });
  }

  function handleCreateFromAll() {
    setCreateDialogOpen(true);
  }

  return (
    <main className="flex h-full flex-col gap-4 p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Discover</h1>
          <p className="text-sm text-muted-foreground max-w-prose">
            Search OpenAlex and Semantic Scholar for papers, then add
            results to a source batch. Cross-references the local Zotero
            catalog so you can see what is already in your library.
          </p>
        </div>
        <Link
          to={ASSISTANT_ROUTE_BATCHES}
          className="text-sm text-muted-foreground hover:underline"
          data-testid="discover-to-batches-link"
        >
          Go to Batches →
        </Link>
      </header>

      <DiscoverySearchBar
        isSearching={searchMutation.isPending}
        onSubmit={(query) => {
          setResolvedQuery(query);
          setSelectedIds(new Set());
          searchMutation.mutate({ query, filters });
        }}
      />

      <DiscoveryFilters filters={filters} onChange={setFilters} />

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded border px-3 py-1 text-sm disabled:opacity-50"
          onClick={handleAddSelected}
          disabled={selectedIds.size === 0 || batches.length === 0}
          data-testid="discover-add-selected"
        >
          Add selected to batch ({selectedIds.size})
        </button>
        <button
          type="button"
          className="rounded border px-3 py-1 text-sm disabled:opacity-50"
          onClick={handleCreateFromAll}
          disabled={results.length === 0}
          data-testid="discover-create-from-all"
        >
          Create batch from results
        </button>
        {resolvedQuery ? (
          <span
            data-testid="discover-search-meta"
            className="ml-auto text-xs text-muted-foreground"
          >
            {results.length} result{results.length === 1 ? "" : "s"} for
            “{resolvedQuery}”{cached ? " (cached)" : ""}
          </span>
        ) : null}
      </div>

      {searchMutation.isError ? (
        <div
          data-testid="discover-error"
          className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700"
        >
          {searchMutation.error?.message}
        </div>
      ) : null}

      {providerErrors.length > 0 ? (
        <div
          data-testid="discover-provider-errors"
          className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"
        >
          {providerErrors.map((e) => (
            <p key={e.provider}>{describeProviderError(e)}</p>
          ))}
        </div>
      ) : null}

      <DiscoveryResultsTable
        results={results}
        selectedIds={selectedIds}
        onToggleSelected={toggleSelected}
        onAddOne={handleAddOne}
      />

      <AddToBatchDialog
        isOpen={addDialog.isOpen}
        papers={addDialog.papers}
        batches={batches}
        onClose={() => setAddDialog({ isOpen: false, papers: [] })}
        onAdded={() => {
          batchesQuery.refetch();
        }}
      />

      <CreateBatchFromResultsDialog
        isOpen={createDialogOpen}
        papers={results}
        onClose={() => setCreateDialogOpen(false)}
        onCreated={() => {
          batchesQuery.refetch();
        }}
      />
    </main>
  );
}
