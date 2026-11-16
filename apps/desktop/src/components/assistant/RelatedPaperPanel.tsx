/**
 * Related papers UI panel.
 *
 * Mounts inside the Batches detail page under a "Related" sub-tab.
 * Calls POST /api/assistant/discover/related for the active
 * batch, decorates results with Zotero crossref badges, and lets the
 * user choose:
 *
 *   - Seed selection: "use all papers" / "use selected papers only".
 *   - Discovery mode: 5 modes (guide §7.3).
 *   - Ranking preset: 4 presets (guide §7.4).
 *   - Filter toggles: PDF link, open access, hide in Zotero, hide in
 *     batch.
 *
 * Re-sorting is client-side when feasible — changing only the ranking
 * preset on an existing result set sorts without re-querying. Changing
 * mode / seeds / filters re-fetches.
 *
 * Reuses the Discover page's DiscoveryResultsTable for the row rendering surface
 * so the look-and-feel matches Discover.
 */
import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import {
  discoverRelated,
  type DiscoverRelatedResponse,
  type DiscoveredPaperWithMatch,
  type RelatedFilters,
  type RelatedMode,
  type RelatedRanking,
} from "../../api/assistantDiscover";
import type {
  AssistantBatch,
  BatchSourceItem,
} from "../../api/assistantBatches";
import { DiscoveryResultsTable } from "./discover/DiscoveryResultsTable";

// ── Constants ────────────────────────────────────────────────────────────────

export const RELATED_MODES: readonly { value: RelatedMode; label: string }[] = [
  { value: "similar", label: "Similar papers" },
  { value: "foundational", label: "Foundational papers" },
  { value: "recent_citing", label: "Recent citing papers" },
  { value: "highly_cited_neighbours", label: "Highly cited neighbours" },
  { value: "review_candidates", label: "Review / survey candidates" },
] as const;

export const RELATED_RANKINGS: readonly {
  value: RelatedRanking;
  label: string;
}[] = [
  { value: "most_relevant", label: "Most relevant" },
  { value: "most_cited", label: "Most cited" },
  { value: "recent_and_relevant", label: "Recent + relevant" },
  { value: "foundational", label: "Foundational" },
] as const;

// ── Component ────────────────────────────────────────────────────────────────

export interface RelatedPaperPanelProps {
  batch: AssistantBatch;
  onAddOne?: (paper: DiscoveredPaperWithMatch) => void;
}

export type SeedSelection = "all" | "selected";

/**
 * Map a batch's sources into seed paper ids that the related-paper
 * providers can resolve.
 *
 * Provider ids are preferred when present (OpenAlex / Semantic Scholar
 * pass straight through to the providers). A DOI is emitted as
 * ``doi:<doi>`` and the backend expands it to an OpenAlex W-id via
 * one batched lookup. A bare Zotero citekey is emitted as
 * ``zotero:<citekey>`` — the backend looks the citekey up in the
 * Better-BibTeX library FTS5 index to recover the DOI / provider
 * ids and then resolves through the same DOI-expansion path. This
 * covers batches whose sources were added before the Zotero picker
 * started populating ``doi``, plus Zotero items that simply don't
 * carry one (rare but possible — e.g. preprint-only entries).
 *
 * Sources with none of those identifiers are skipped so a malformed
 * row can't poison the seed list.
 */
function sourceToSeedId(s: BatchSourceItem): string | null {
  if (s.openalex_id) return `openalex:${s.openalex_id}`;
  if (s.semantic_scholar_id) return `semantic_scholar:${s.semantic_scholar_id}`;
  if (s.doi) return `doi:${s.doi}`;
  if (s.citekey) return `zotero:${s.citekey}`;
  return null;
}

function seedsFromSources(sources: BatchSourceItem[]): string[] {
  const out: string[] = [];
  for (const s of sources) {
    const seed = sourceToSeedId(s);
    if (seed) out.push(seed);
  }
  return out;
}

/**
 * Client-side sort by ranking preset. Mirrors the backend
 * `rank_papers` for the four documented presets so the UI can re-sort
 * without re-fetching.
 */
export function sortByRanking(
  papers: DiscoveredPaperWithMatch[],
  ranking: RelatedRanking,
): DiscoveredPaperWithMatch[] {
  if (ranking === "most_cited") {
    return [...papers].sort(
      (a, b) =>
        Math.log((b.citation_count ?? 0) + 1) -
        Math.log((a.citation_count ?? 0) + 1),
    );
  }
  if (ranking === "recent_and_relevant") {
    // Stable sort: ties on year preserve input order (relevance).
    return [...papers]
      .map((p, idx) => ({ p, idx }))
      .sort((a, b) => {
        const yearDiff = (b.p.year ?? 0) - (a.p.year ?? 0);
        if (yearDiff !== 0) return yearDiff;
        return a.idx - b.idx;
      })
      .map(({ p }) => p);
  }
  if (ranking === "foundational") {
    return [...papers].sort((a, b) => {
      const ca = Math.log((a.citation_count ?? 0) + 1);
      const cb = Math.log((b.citation_count ?? 0) + 1);
      if (cb !== ca) return cb - ca;
      // Older year wins among equal-citation peers.
      return (a.year ?? 9999) - (b.year ?? 9999);
    });
  }
  // most_relevant — preserve input order.
  return [...papers];
}

/**
 * Apply the four filter toggles client-side on top of the backend
 * filtering. Backend already applies only_with_pdf / open_access_only /
 * hide_in_zotero; this helper also applies hide_in_batch (client-only
 * because the backend doesn't know the batch contents).
 */
export function applyClientFilters(
  papers: DiscoveredPaperWithMatch[],
  filters: RelatedFilters,
  batchSourceIds: Set<string>,
): DiscoveredPaperWithMatch[] {
  return papers.filter((p) => {
    if (filters.only_with_pdf && !p.pdf_url) return false;
    if (filters.open_access_only && !p.is_open_access) return false;
    if (filters.hide_in_zotero && p.zotero_match?.in_zotero) return false;
    if (filters.hide_in_batch && batchSourceIds.has(p.id)) return false;
    return true;
  });
}

export function RelatedPaperPanel({
  batch,
  onAddOne,
}: RelatedPaperPanelProps) {
  const [seedSelection, setSeedSelection] = useState<SeedSelection>("all");
  const [selectedSeedIds, setSelectedSeedIds] = useState<Set<string>>(
    new Set(),
  );
  const [mode, setMode] = useState<RelatedMode>("similar");
  const [ranking, setRanking] = useState<RelatedRanking>("most_relevant");
  const [filters, setFilters] = useState<RelatedFilters>({
    only_with_pdf: false,
    open_access_only: false,
    hide_in_zotero: false,
    hide_in_batch: false,
  });
  const [selectedResultIds, setSelectedResultIds] = useState<Set<string>>(
    new Set(),
  );

  const allSeeds = useMemo(
    () => seedsFromSources(batch.sources),
    [batch.sources],
  );
  const selectedSeeds = useMemo(
    () => allSeeds.filter((id) => selectedSeedIds.has(id)),
    [allSeeds, selectedSeedIds],
  );

  const effectiveSeeds =
    seedSelection === "all" ? allSeeds : selectedSeeds;

  const batchSourceIds = useMemo(
    () => new Set(batch.sources.map((s) => s.id)),
    [batch.sources],
  );

  const findRelatedMutation = useMutation<
    DiscoverRelatedResponse,
    Error,
    void
  >({
    mutationFn: async () =>
      discoverRelated({
        batch_id: batch.id,
        seed_paper_ids: effectiveSeeds,
        mode,
        ranking,
        limit: 25,
        filters,
      }),
  });

  const raw = findRelatedMutation.data?.results ?? [];

  // Re-sort client-side when the ranking changes; re-fetch only when
  // mode / seeds / filters change.
  const sorted = useMemo(() => sortByRanking(raw, ranking), [raw, ranking]);
  const filtered = useMemo(
    () => applyClientFilters(sorted, filters, batchSourceIds),
    [sorted, filters, batchSourceIds],
  );

  function toggleResultSelected(id: string) {
    setSelectedResultIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSeedSelected(id: string) {
    setSelectedSeedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const canSubmit =
    effectiveSeeds.length > 0 && !findRelatedMutation.isPending;

  return (
    <section
      data-testid="related-paper-panel"
      className="flex flex-col gap-4 p-4"
    >
      <header className="sticky top-0 z-10 -m-4 mb-0 flex items-start justify-between gap-3 border-b bg-background p-4">
        <div>
          <h2 className="text-lg font-semibold">Related papers</h2>
          <p className="text-xs text-muted-foreground">
            Find papers similar to, cited by, or citing this batch's
            sources via OpenAlex + Semantic Scholar.
          </p>
          {effectiveSeeds.length === 0 && (
            <p
              data-testid="related-no-seeds-header"
              className="mt-1 text-xs text-amber-700"
            >
              {allSeeds.length === 0
                ? "Add at least one source to the batch first (Sources tab → Zotero picker)."
                : "Pick one or more seed papers below, or switch to 'All sources'."}
            </p>
          )}
        </div>
        <button
          type="button"
          className="shrink-0 rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow disabled:opacity-50"
          onClick={() => findRelatedMutation.mutate()}
          disabled={!canSubmit}
          data-testid="related-submit-header"
        >
          {findRelatedMutation.isPending
            ? "Searching…"
            : `Find related papers (${effectiveSeeds.length} seed${effectiveSeeds.length === 1 ? "" : "s"})`}
        </button>
      </header>

      {/* Seed selection */}
      <fieldset
        data-testid="related-seed-fieldset"
        className="flex flex-col gap-1 rounded border p-3"
      >
        <legend className="px-1 text-xs uppercase tracking-wide text-muted-foreground">
          Seed selection
        </legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="related-seed-selection"
            data-testid="related-seed-all"
            checked={seedSelection === "all"}
            onChange={() => setSeedSelection("all")}
          />
          Use all papers ({allSeeds.length})
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="related-seed-selection"
            data-testid="related-seed-selected"
            checked={seedSelection === "selected"}
            onChange={() => setSeedSelection("selected")}
          />
          Use selected papers only ({selectedSeeds.length})
        </label>
        {seedSelection === "selected" ? (
          <div
            data-testid="related-seed-source-list"
            className="ml-6 flex max-h-32 flex-col gap-1 overflow-y-auto text-xs"
          >
            {batch.sources.map((s) => {
              // Use the same seed-id derivation as ``seedsFromSources``
              // so checkbox selections actually intersect with the seed
              // list the backend receives. Sources without a usable
              // identifier are still rendered but their checkbox is
              // disabled — a visual nudge that the row needs metadata
              // before it can act as a seed.
              const seedId = sourceToSeedId(s);
              return (
                <label
                  key={s.id}
                  className="flex items-center gap-2"
                >
                  <input
                    type="checkbox"
                    aria-label={`Seed: ${s.title}`}
                    disabled={seedId == null}
                    checked={seedId != null && selectedSeedIds.has(seedId)}
                    onChange={() =>
                      seedId != null && toggleSeedSelected(seedId)
                    }
                  />
                  <span className="truncate">
                    {s.title}
                    {seedId == null && (
                      <span className="ml-1 text-[10px] text-muted-foreground">
                        (no identifier)
                      </span>
                    )}
                  </span>
                </label>
              );
            })}
          </div>
        ) : null}
      </fieldset>

      {/* Mode + ranking dropdowns */}
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-muted-foreground">
            Discovery mode
          </span>
          <select
            data-testid="related-mode-select"
            className="rounded border px-2 py-1 text-sm"
            value={mode}
            onChange={(e) => setMode(e.target.value as RelatedMode)}
          >
            {RELATED_MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs uppercase tracking-wide text-muted-foreground">
            Ranking
          </span>
          <select
            data-testid="related-ranking-select"
            className="rounded border px-2 py-1 text-sm"
            value={ranking}
            onChange={(e) => setRanking(e.target.value as RelatedRanking)}
          >
            {RELATED_RANKINGS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Filters */}
      <fieldset
        data-testid="related-filters"
        className="flex flex-wrap gap-3 rounded border p-3 text-sm"
      >
        <legend className="px-1 text-xs uppercase tracking-wide text-muted-foreground">
          Filters
        </legend>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            data-testid="related-filter-only-pdf"
            checked={!!filters.only_with_pdf}
            onChange={(e) =>
              setFilters((f: RelatedFilters) => ({ ...f, only_with_pdf: e.target.checked }))
            }
          />
          PDF available
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            data-testid="related-filter-open-access"
            checked={!!filters.open_access_only}
            onChange={(e) =>
              setFilters((f: RelatedFilters) => ({ ...f, open_access_only: e.target.checked }))
            }
          />
          Open access only
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            data-testid="related-filter-hide-in-zotero"
            checked={!!filters.hide_in_zotero}
            onChange={(e) =>
              setFilters((f: RelatedFilters) => ({ ...f, hide_in_zotero: e.target.checked }))
            }
          />
          Hide papers already in Zotero
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            data-testid="related-filter-hide-in-batch"
            checked={!!filters.hide_in_batch}
            onChange={(e) =>
              setFilters((f: RelatedFilters) => ({ ...f, hide_in_batch: e.target.checked }))
            }
          />
          Hide papers already in this batch
        </label>
      </fieldset>

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            onClick={() => findRelatedMutation.mutate()}
            disabled={!canSubmit}
            data-testid="related-submit"
          >
            {findRelatedMutation.isPending ? "Searching…" : "Find related papers"}
          </button>
          <span
            data-testid="related-result-count"
            className="text-xs text-muted-foreground"
          >
            {filtered.length} result{filtered.length === 1 ? "" : "s"}
            {findRelatedMutation.data?.cached ? " (cached)" : ""}
          </span>
        </div>
        {effectiveSeeds.length === 0 && (
          <p className="text-xs text-amber-700" data-testid="related-no-seeds">
            {allSeeds.length === 0
              ? "Add at least one source to the batch first (Sources tab → Zotero picker)."
              : "Pick one or more seed papers above, or switch to 'All sources'."}
          </p>
        )}
      </div>

      {findRelatedMutation.isError ? (
        <div
          data-testid="related-error"
          className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700"
        >
          {findRelatedMutation.error?.message}
        </div>
      ) : null}

      <DiscoveryResultsTable
        results={filtered}
        selectedIds={selectedResultIds}
        onToggleSelected={toggleResultSelected}
        onAddOne={(p) => onAddOne?.(p)}
      />
    </section>
  );
}
