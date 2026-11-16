/**
 * Assistant Discover API client (routes at /api/assistant/discover/*).
 *
 * Mirrors `api/assistantBatches.ts` style.
 */
import { api } from "./client";
import type { ZoteroMatch } from "./assistantBatches";

// ── Types ─────────────────────────────────────────────────────────────────────

export type DiscoveredPaper = {
  id: string;
  source: string; // "openalex" | "semantic_scholar" | "merged"
  doi?: string | null;
  openalex_id?: string | null;
  semantic_scholar_id?: string | null;
  arxiv_id?: string | null;
  pmid?: string | null;
  title: string;
  abstract?: string | null;
  authors?: string[];
  year?: number | null;
  venue?: string | null;
  publication_date?: string | null;
  work_type?: string | null;
  citation_count?: number | null;
  reference_count?: number | null;
  pdf_url?: string | null;
  is_open_access?: boolean | null;
  raw?: Record<string, Record<string, unknown>>;
};

/** DiscoveredPaper decorated with the Zotero crossref result. */
export type DiscoveredPaperWithMatch = DiscoveredPaper & {
  zotero_match: ZoteroMatch & {
    confidence: number;
    match_reason: string;
    in_zotero: boolean;
  };
};

export type DiscoverSearchFilters = {
  only_with_pdf?: boolean; // pinned default: false (HARD RULE, guide section 5.1)
  year_from?: number | null;
  year_to?: number | null;
  open_access_only?: boolean;
  work_types?: string[];
};

export type DiscoverSearchBody = {
  query: string;
  filters?: DiscoverSearchFilters;
  limit?: number;
};

export type DiscoverSearchResponse = {
  results: DiscoveredPaperWithMatch[];
  cached: boolean;
};

export type DiscoverMergeBody = {
  lists: DiscoveredPaper[][];
};

export type DiscoverMergeResponse = {
  results: DiscoveredPaper[];
};

export type DiscoverCrossrefBody = {
  papers: DiscoveredPaper[];
};

export type DiscoverCrossrefResponse = {
  results: DiscoveredPaperWithMatch[];
};

export type DiscoverCacheStats = {
  hits: number;
  misses: number;
  clears: number;
  entries: number;
  oldest_entry_epoch: number | null;
  provider_version: string;
};

// ── Calls ─────────────────────────────────────────────────────────────────────

export async function discoverSearch(
  body: DiscoverSearchBody,
): Promise<DiscoverSearchResponse> {
  return api<DiscoverSearchResponse>("/api/assistant/discover/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function discoverMerge(
  body: DiscoverMergeBody,
): Promise<DiscoverMergeResponse> {
  return api<DiscoverMergeResponse>("/api/assistant/discover/merge", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function discoverCrossrefZotero(
  body: DiscoverCrossrefBody,
): Promise<DiscoverCrossrefResponse> {
  return api<DiscoverCrossrefResponse>(
    "/api/assistant/discover/crossref-zotero",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function discoverCacheStats(): Promise<DiscoverCacheStats> {
  return api<DiscoverCacheStats>("/api/assistant/discover/cache/stats");
}

export async function discoverCacheClear(): Promise<{ removed: number }> {
  return api<{ removed: number }>("/api/assistant/discover/cache", {
    method: "DELETE",
  });
}

// ── Related-paper discovery ──────────────────────────────────────────────────

/** Closed set per guide §7.3 — five modes. */
export type RelatedMode =
  | "similar"
  | "foundational"
  | "recent_citing"
  | "highly_cited_neighbours"
  | "review_candidates";

/** Closed set per guide §7.4 — four ranking presets. */
export type RelatedRanking =
  | "most_relevant"
  | "most_cited"
  | "recent_and_relevant"
  | "foundational";

export type RelatedFilters = {
  only_with_pdf?: boolean; // pinned default: false (HARD RULE)
  open_access_only?: boolean;
  hide_in_zotero?: boolean;
  hide_in_batch?: boolean;
};

export type DiscoverRelatedBody = {
  batch_id?: string | null;
  seed_paper_ids?: string[];
  mode: RelatedMode;
  ranking: RelatedRanking;
  limit?: number;
  year_from?: number | null;
  year_to?: number | null;
  filters?: RelatedFilters;
};

export type DiscoverRelatedResponse = {
  results: DiscoveredPaperWithMatch[];
  mode: RelatedMode;
  ranking: RelatedRanking;
  cached: boolean;
};

export async function discoverRelated(
  body: DiscoverRelatedBody,
): Promise<DiscoverRelatedResponse> {
  return api<DiscoverRelatedResponse>("/api/assistant/discover/related", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function discoverRelatedCacheClear(): Promise<{ removed: number }> {
  return api<{ removed: number }>("/api/assistant/discover/related-cache", {
    method: "DELETE",
  });
}

/**
 * Project a DiscoveredPaper onto the `BatchSourceItem` shape with
 * `kind="discovered"` (the batch-source contract). Used by the
 * Discover -> Batches integration on Add-to-Batch and
 * Create-Batch-from-Results flows.
 */
export function discoveredPaperToSourceItem(
  paper: DiscoveredPaper,
): import("./assistantBatches").BatchSourceItem {
  return {
    id: paper.id,
    kind: "discovered",
    title: paper.title,
    authors: paper.authors ?? [],
    year: paper.year ?? null,
    doi: paper.doi ?? null,
    openalex_id: paper.openalex_id ?? null,
    semantic_scholar_id: paper.semantic_scholar_id ?? null,
    external_pdf_url: paper.pdf_url ?? null,
  };
}
