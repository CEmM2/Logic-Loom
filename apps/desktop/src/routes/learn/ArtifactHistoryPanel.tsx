/**
 * Artifact history panel.
 *
 * Lists the artifacts compiled on this computer, newest first. There is no
 * backend history endpoint (routes/learn.py has none), so the list is kept in
 * the window's localStorage: it survives reloads and restarts of the app, is
 * capped at HISTORY_LIMIT entries, and degrades to session-only when storage
 * is unavailable (every access is wrapped in try/catch).
 *
 * Each entry carries the key fields from `LearnCompileResponse`:
 *   - artifact_id  — deterministic hash derived by the backend from request + graph
 *   - topic        — from provenance_summary.topic (or the compile request)
 *   - generation_option — mode used (from provenance_summary or the request)
 *   - cache_dir    — artifact directory on disk (used by OpenFolderButton)
 *   - exports      — export artifact entries (path + type)
 *   - timestamp    — wall-clock time of the compile (client-side Date.now())
 */

import type { LearnArtifact } from "../../api/learn";

/** One entry in the session artifact history. */
export interface ArtifactHistoryEntry {
  artifact_id: string;
  topic: string | null;
  generation_option: string | null;
  cache_dir: string | null;
  exports: LearnArtifact[];
  /** Client-side timestamp (ms since epoch, Date.now()) */
  timestamp: number;
}

interface Props {
  entries: ArtifactHistoryEntry[];
}

export const HISTORY_STORAGE_KEY = "logic-loom.learn.history.v1";
export const HISTORY_LIMIT = 50;

function isEntry(value: unknown): value is ArtifactHistoryEntry {
  if (!value || typeof value !== "object") return false;
  const e = value as Record<string, unknown>;
  return (
    typeof e.artifact_id === "string" &&
    typeof e.timestamp === "number" &&
    Array.isArray(e.exports)
  );
}

/** Read the saved history; an empty list when storage is missing or corrupt. */
export function loadArtifactHistory(): ArtifactHistoryEntry[] {
  try {
    const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isEntry).slice(-HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
}

/** Save the history (oldest first), keeping the newest HISTORY_LIMIT entries. */
export function saveArtifactHistory(entries: ArtifactHistoryEntry[]): ArtifactHistoryEntry[] {
  const kept = entries.slice(-HISTORY_LIMIT);
  try {
    window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(kept));
  } catch {
    // Storage full or blocked: keep the in-memory list only.
  }
  return kept;
}

/**
 * Renders the saved artifact history list.
 *
 * Shown in reverse-chronological order (newest first). Each row shows:
 *   - topic + generation_option
 *   - artifact_id (truncated)
 *   - export count
 *   - wall-clock compile time
 *
 * Renders nothing when there are no prior compiles yet (not even an empty box) —
 * the first compile populates it.
 */
export function ArtifactHistoryPanel({ entries }: Props) {
  if (entries.length === 0) return null;

  // Newest first.
  const reversed = [...entries].reverse();

  return (
    <section
      data-testid="learn-artifact-history"
      className="flex flex-col gap-2"
    >
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        History ({entries.length} compile{entries.length === 1 ? "" : "s"})
      </div>
      <p className="text-[10px] text-muted-foreground/70">
        Kept on this computer; the latest {HISTORY_LIMIT} compiles are listed.
      </p>
      <ul className="space-y-1">
        {reversed.map((entry) => (
          <li
            key={`${entry.artifact_id}-${entry.timestamp}`}
            data-testid={`learn-history-entry-${entry.artifact_id}`}
            className="rounded border border-border bg-background px-3 py-2 text-sm"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <span
                  data-testid={`learn-history-topic-${entry.artifact_id}`}
                  className="font-medium"
                >
                  {entry.topic ?? "(no topic)"}
                </span>
                {entry.generation_option ? (
                  <span
                    data-testid={`learn-history-mode-${entry.artifact_id}`}
                    className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
                  >
                    {entry.generation_option}
                  </span>
                ) : null}
              </div>
              <time
                data-testid={`learn-history-time-${entry.artifact_id}`}
                className="shrink-0 text-[10px] text-muted-foreground"
                dateTime={new Date(entry.timestamp).toISOString()}
              >
                {new Date(entry.timestamp).toLocaleString()}
              </time>
            </div>
            <div className="mt-0.5 flex gap-4 text-[10px] text-muted-foreground">
              <span data-testid={`learn-history-id-${entry.artifact_id}`}>
                id:{" "}
                <span className="font-mono">
                  {entry.artifact_id.length > 12
                    ? `${entry.artifact_id.slice(0, 12)}…`
                    : entry.artifact_id}
                </span>
              </span>
              <span data-testid={`learn-history-exports-${entry.artifact_id}`}>
                {entry.exports.length} export{entry.exports.length === 1 ? "" : "s"}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
