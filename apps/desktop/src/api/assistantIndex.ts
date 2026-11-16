/**
 * Assistant Index API client — typed wrappers around
 * ``/api/assistant/index/*``.
 *
 * Three endpoints back the workspace search UI:
 *
 *   - getIndexStatus()  → per-kind counts + last-rebuild timestamp.
 *                         Used to drive the empty-state "rebuild
 *                         index" link.
 *   - rebuildIndex()    → walks every workspace artefact and rebuilds.
 *                         Returns the updated status payload.
 *   - searchIndex()     → full-text search against the FTS5 backend.
 *                         Powers the Cmd-K WorkspaceSearch modal.
 */
import { api } from "./client";

const BASE = "/api/assistant/index";

/** Seven indexable kinds — closed set, mirrors `IndexKind` in the backend. */
export type IndexKind =
  | "zotero"
  | "abstract"
  | "zsum_card"
  | "zsum_digest"
  | "assistant_output"
  | "akms_node"
  | "run_metadata";

export const INDEX_KINDS: readonly IndexKind[] = [
  "zotero",
  "abstract",
  "zsum_card",
  "zsum_digest",
  "assistant_output",
  "akms_node",
  "run_metadata",
] as const;

export type IndexStatus = {
  /** Per-kind row counts. Every closed-set kind is keyed (zero when absent). */
  counts: Record<IndexKind, number>;
  /** Total row count across every kind. */
  total: number;
  /** ISO-8601 string of the most recent rebuild, or `null` if never rebuilt. */
  last_rebuild_at: string | null;
};

export type RebuildResponse = IndexStatus;

/**
 * Fetch the current index status. Returns the closed-set per-kind
 * counts + the last-rebuild timestamp. Safe to call on every modal
 * open — the route is cheap (one SQLite aggregate query).
 */
export async function getIndexStatus(): Promise<IndexStatus> {
  return api<IndexStatus>(`${BASE}/status`);
}

/**
 * Trigger a full rebuild of the index. Walks every workspace artefact
 * via the seven backend adapters; clears the records table first so
 * stale rows are evicted. Returns the post-rebuild status payload.
 */
export async function rebuildIndex(): Promise<RebuildResponse> {
  return api<RebuildResponse>(`${BASE}/rebuild`, { method: "POST" });
}

/** Wire-shape of a single search hit (mirrors backend ``IndexableRecord``). */
export type SearchResultRecord = {
  id: string;
  kind: IndexKind;
  title: string;
  body: string;
  source_path: string;
  batch_id: string | null;
  workflow_id: string | null;
  run_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  content_hash: string;
};

export type SearchResponse = {
  results: SearchResultRecord[];
  total: number;
};

/**
 * Full-text search the research-memory index. An empty ``kinds`` list
 * searches every closed-set kind; passing one or more restricts the
 * result set. ``limit`` caps the rendered slice (default 50).
 */
export async function searchIndex(
  query: string,
  kinds: readonly IndexKind[] = [],
  limit = 50,
): Promise<SearchResponse> {
  return api<SearchResponse>(`${BASE}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, kinds: Array.from(kinds), limit }),
  });
}
