/**
 * WorkspaceSearch — Cmd-K modal search across the Assistant workspace.
 *
 * The modal opens on Cmd-K (macOS) or Ctrl-K
 * (Windows/Linux) via a global keydown listener mounted by
 * AppShell. Seven kind filters mirror the closed-set
 * :data:`INDEX_KINDS` from the backend; toggling a filter scopes the
 * results without re-running the search (filtering is client-side
 * over the most recent result page).
 *
 * Each result links to its source page (batch / run / node / output)
 * via a kind-specific deep link. Empty state — when the backend's
 * status reports zero records — shows a "rebuild index" link that
 * POSTs to `/api/assistant/index/rebuild` and re-fetches status on
 * completion.
 *
 * Integration: AppShell mounts <WorkspaceSearch /> once + registers
 * a global Cmd-K / Ctrl-K shortcut that toggles its `open` prop. The
 * shortcut works from any Assistant sub-tab because the listener is
 * on `document`.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { JSX, KeyboardEvent } from "react";

import {
  INDEX_KINDS,
  getIndexStatus,
  rebuildIndex,
  searchIndex,
  type IndexKind,
  type IndexStatus,
} from "../../api/assistantIndex";

export interface WorkspaceSearchProps {
  /** Whether the modal is open. */
  open: boolean;
  /** Called when the user requests the modal to close. */
  onClose: () => void;
  /**
   * Optional override for the search backend. Tests inject a stub so
   * the modal can be exercised without a live sidecar. Production
   * code leaves this unset; the modal then calls the bundled
   * `searchWorkspace` function (also exported here).
   */
  searchFn?: (query: string, kinds: IndexKind[]) => Promise<SearchResult[]>;
  /** Optional override for the status fetcher (tests). */
  statusFn?: () => Promise<IndexStatus>;
  /** Optional override for the rebuild trigger (tests). */
  rebuildFn?: () => Promise<IndexStatus>;
  /** Optional navigation hook (tests assert on this; prod uses default). */
  onNavigate?: (target: NavigationTarget) => void;
}

/** A single workspace search hit. */
export interface SearchResult {
  id: string;
  kind: IndexKind;
  title: string;
  /** Optional snippet (first ~200 chars of the body). */
  snippet?: string;
  /** Identifiers used to build the deep link. */
  batch_id?: string;
  workflow_id?: string;
  run_id?: string;
}

/** Deep-link target the parent should navigate to. */
export interface NavigationTarget {
  /** Top-level Assistant route (e.g. `/assistant/batches`). */
  path: string;
  /** Source record id (for in-route deep linking). */
  recordId: string;
  /** Optional context ids carried through from the result. */
  batchId?: string;
  workflowId?: string;
  runId?: string;
}

/** Human-readable label for each kind. Kept short so the filter row fits. */
export const KIND_LABELS: Record<IndexKind, string> = {
  zotero: "Zotero",
  abstract: "Abstracts",
  zsum_card: "Cards",
  zsum_digest: "Digests",
  assistant_output: "Outputs",
  akms_node: "Nodes",
  run_metadata: "Runs",
};

/**
 * Map a result's kind to the Assistant route that owns it. Each entry
 * is the path the parent should navigate to when the user clicks the
 * result.
 */
export function resolveNavigationTarget(result: SearchResult): NavigationTarget {
  const base: NavigationTarget = {
    path: "/assistant/batches",
    recordId: result.id,
    batchId: result.batch_id,
    workflowId: result.workflow_id,
    runId: result.run_id,
  };
  switch (result.kind) {
    case "zotero":
    case "abstract":
      return { ...base, path: "/assistant/batches" };
    case "zsum_card":
    case "zsum_digest":
      return { ...base, path: "/assistant/discover" };
    case "assistant_output":
    case "run_metadata":
      return { ...base, path: "/assistant/outputs" };
    case "akms_node":
      return { ...base, path: "/nodes" };
    default:
      return base;
  }
}

/** Truncate the body to a short snippet for the result list. */
function bodySnippet(body: string): string {
  const trimmed = body.trim().replace(/\s+/g, " ");
  if (trimmed.length <= 200) return trimmed;
  return trimmed.slice(0, 200) + "…";
}

/**
 * Bundled default search backend. Calls
 * ``POST /api/assistant/index/search`` and adapts the wire shape
 * (``SearchResultRecord``) into the modal's ``SearchResult`` shape.
 * Tests inject a stub via the ``searchFn`` prop to bypass the sidecar.
 */
export async function searchWorkspace(
  query: string,
  kinds: IndexKind[],
): Promise<SearchResult[]> {
  const { results } = await searchIndex(query, kinds);
  return results.map((r) => ({
    id: r.id,
    kind: r.kind,
    title: r.title,
    snippet: r.body ? bodySnippet(r.body) : undefined,
    batch_id: r.batch_id ?? undefined,
    workflow_id: r.workflow_id ?? undefined,
    run_id: r.run_id ?? undefined,
  }));
}

interface FilterState {
  /** Set of currently-selected kinds. Empty set = all kinds. */
  active: ReadonlySet<IndexKind>;
}

function initialFilterState(): FilterState {
  return { active: new Set() };
}

/**
 * Workspace search modal. Renders `null` while `open` is false so
 * AppShell can mount it unconditionally.
 */
export function WorkspaceSearch(props: WorkspaceSearchProps): JSX.Element | null {
  const {
    open,
    onClose,
    searchFn = searchWorkspace,
    statusFn = getIndexStatus,
    rebuildFn = rebuildIndex,
    onNavigate,
  } = props;

  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<FilterState>(initialFilterState);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [loading, setLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const lastQuery = useRef<string | null>(null);

  // Reset state every time the modal opens (so re-opening starts fresh).
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults([]);
    lastQuery.current = null;
    setSearchError(null);
    setLoading(false);
    setFilters(initialFilterState());
    let cancelled = false;
    statusFn()
      .then((next) => {
        if (!cancelled) setStatus(next);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, [open, statusFn]);

  // Run the search whenever the query or filters change. Debounced
  // client-side via a short setTimeout so rapid typing doesn't fire
  // one request per keystroke. Tests bypass this with searchFn
  // resolving synchronously.
  useEffect(() => {
    if (!open) return;
    // When the query text changes, drop the previous query's results
    // straight away, so a cleared or changed query never shows stale hits
    // (v0.3.1 QA), and show "Searching…" through the debounce. A filter
    // change keeps the current hits (filtered client-side) while it refetches.
    const queryChanged = lastQuery.current !== query;
    lastQuery.current = query;
    setSearchError(null);
    if (queryChanged) setResults([]);
    if (!query.trim()) {
      setResults([]);
      setLoading(false);
      return;
    }
    if (queryChanged) setLoading(true);
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const next = await searchFn(query, Array.from(filters.active));
        if (!cancelled) setResults(next);
      } catch (error) {
        if (!cancelled) {
          setSearchError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [open, query, filters.active, searchFn]);

  function toggleKind(kind: IndexKind): void {
    setFilters((prev) => {
      const next = new Set(prev.active);
      if (next.has(kind)) {
        next.delete(kind);
      } else {
        next.add(kind);
      }
      return { active: next };
    });
  }

  async function handleRebuild(): Promise<void> {
    setRebuilding(true);
    try {
      const next = await rebuildFn();
      setStatus(next);
    } finally {
      setRebuilding(false);
    }
  }

  function handleResultClick(result: SearchResult): void {
    const target = resolveNavigationTarget(result);
    if (onNavigate) {
      onNavigate(target);
    }
    onClose();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  // Client-side filter pass over the most recent result page.
  const filteredResults = useMemo(() => {
    if (filters.active.size === 0) return results;
    return results.filter((r) => filters.active.has(r.kind));
  }, [results, filters.active]);

  const indexEmpty = useMemo(() => {
    if (status === null) return false;
    return status.total === 0;
  }, [status]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-label="Workspace search"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-24"
      onKeyDown={handleKeyDown}
    >
      <div className="w-[640px] max-w-[90vw] rounded-lg border border-border bg-background shadow-xl">
        <div className="border-b border-border p-3">
          <input
            autoFocus
            type="text"
            value={query}
            placeholder="Search workspace…"
            aria-label="Workspace search query"
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-border bg-background px-3 py-2 text-base text-foreground outline-none"
          />
        </div>

        <div className="flex flex-wrap gap-2 border-b border-border p-2" role="group" aria-label="Kind filters">
          {INDEX_KINDS.map((kind) => {
            const active = filters.active.has(kind);
            return (
              <button
                key={kind}
                type="button"
                aria-pressed={active}
                onClick={() => toggleKind(kind)}
                className={
                  "rounded-full border px-3 py-1 text-xs " +
                  (active
                    ? "bg-foreground text-background border-transparent"
                    : "bg-transparent text-muted-foreground border-border hover:bg-muted")
                }
              >
                {KIND_LABELS[kind]}
              </button>
            );
          })}
        </div>

        <div className="max-h-[60vh] overflow-auto">
          {indexEmpty && query.trim() === "" ? (
            <div className="p-6 text-center text-sm text-muted-foreground">
              <p>No records in the research-memory index yet.</p>
              <button
                type="button"
                onClick={handleRebuild}
                disabled={rebuilding}
                className="mt-3 rounded border border-border px-3 py-1 text-foreground hover:bg-muted"
              >
                {rebuilding ? "Rebuilding…" : "Rebuild index"}
              </button>
            </div>
          ) : query.trim() === "" ? (
            <div className="p-6 text-center text-sm text-muted-foreground">
              Type to search…
            </div>
          ) : loading ? (
            <div className="p-6 text-center text-sm text-muted-foreground">Searching…</div>
          ) : searchError ? (
            <div className="p-6 text-center text-sm text-destructive" data-testid="workspace-search-error">
              Search failed: {searchError}
            </div>
          ) : filteredResults.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted-foreground">
              No results.
            </div>
          ) : (
            <ul className="divide-y" role="list">
              {filteredResults.map((result) => (
                <li key={result.id}>
                  <button
                    type="button"
                    onClick={() => handleResultClick(result)}
                    className="flex w-full flex-col items-start p-3 text-left hover:bg-muted"
                  >
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-muted px-2 py-0.5 text-xs uppercase text-muted-foreground">
                        {KIND_LABELS[result.kind]}
                      </span>
                      <span className="text-sm font-medium">{result.title}</span>
                    </div>
                    {result.snippet ? (
                      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                        {result.snippet}
                      </p>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

export default WorkspaceSearch;
