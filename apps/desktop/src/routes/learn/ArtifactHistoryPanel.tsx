/**
 * Artifact history panel.
 *
 * Accumulates each successful compile response in React state and renders the
 * list of prior compiled artifacts for the current session.
 *
 * IMPORTANT — SESSION-SCOPED ONLY:
 *   There is no backend `/api/learn/history` or `/api/learn/list` endpoint.
 *   `GET /api/learn/status`, `/profiles`, `/exporters`, `/compile`, `/export`,
 *   and `/artifacts/{id}/{path}` are the only endpoints (routes/learn.py). This
 *   panel therefore accumulates history in React state only — it is NOT persisted
 *   across page reloads or Tauri restarts. A future backend listing endpoint
 *   (if added) would be the right place to persist cross-session history.
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

/**
 * Renders the session-scoped artifact history list.
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
        Session history ({entries.length} compile{entries.length === 1 ? "" : "s"})
      </div>
      <p className="text-[10px] text-muted-foreground/70">
        Session-scoped — not persisted across reloads (no backend history endpoint).
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
                {new Date(entry.timestamp).toLocaleTimeString()}
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
