/**
 * Runs API client — typed wrappers around ``/api/runs/*``.
 *
 * The Runs page (list, detail and comparison view) consumes this client:
 *   * ``listRuns`` / ``getRun`` for the list + detail panes.
 *   * ``rerunRun`` for the Rerun button.
 *   * ``getEffectivePrompts`` for the comparison view's prompt diff.
 *   * ``openRunStream`` for live log tailing via browser-native EventSource.
 *
 * Why a dedicated module: the legacy ``zsum.ts`` module exposes a similarly
 * shaped registry surface but only for ``/api/zsum/*``. Workflow runs live
 * under ``/api/runs/*``, alongside the rerun + effective-prompts +
 * stream endpoints. Mirror them here so the Runs page does not
 * have to know about the legacy zsum route group.
 *
 * SSE: we use the browser-native ``EventSource``, not ``fetch`` + a stream
 * reader. The route is ``GET /api/runs/{run_id}/stream`` with
 * ``Content-Type: text/event-stream``. ``openRunStream`` returns the raw
 * EventSource handle so callers can attach ``log`` / ``done`` listeners and
 * call ``.close()`` themselves. Callers MUST close the handle on terminal
 * status — leaking it triggers infinite reconnects from EventSource's
 * default retry behaviour.
 */
import { api, getApiBase } from "./client";

// ── Types ────────────────────────────────────────────────────────────────────

export type RunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type RunKind = "zsum" | "workflow";

export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "succeeded",
  "failed",
  "cancelled",
]);

export function isTerminal(status: string | undefined | null): boolean {
  return !!status && TERMINAL_STATUSES.has(status as RunStatus);
}

/**
 * Mirrors :class:`logic_loom_api.runs.models.RunRecord`.
 *
 * ``kind``, ``batch_id`` and ``workflow_id`` are later additions; for
 * legacy zsum records the latter two are null. ``run_label`` is a
 * workflow-only field surfaced from metadata when available.
 */
export type RunRecord = {
  id: string;
  stage: string;
  status: RunStatus;
  started_at: string;
  ended_at: string | null;
  exit_code: number | null;
  pid: number | null;
  log_path: string;
  args: string[];
  cwd: string | null;
  last_lines: string[];
  kind: RunKind;
  batch_id: string | null;
  workflow_id: string | null;
  run_label?: string | null;
  prompt_hash?: string | null;
  [key: string]: unknown;
};

export type LogEvent = {
  ts: string;
  level: string;
  line: string;
};

export type RerunBody = {
  run_label?: string | null;
  prompt_profile_id?: string | null;
  prompt_overrides?: Record<string, string>;
  notebook_id?: string | null;
  out_dir?: string | null;
  force?: boolean;
  dry_run?: boolean;
  save_as_batch_default?: boolean;
  workflow_inputs?: Record<string, string>;
};

export type RerunResponse = {
  run_id: string;
  status?: string;
  kind?: string;
  batch_id?: string | null;
  workflow_id?: string | null;
  [key: string]: unknown;
};

export type EffectivePromptEntry = {
  slot: string;
  variant_id?: string;
  resolved_text?: string;
  source?: string;
  family?: string;
  [key: string]: unknown;
};

export type EffectivePromptsManifest = {
  entries?: EffectivePromptEntry[];
  [key: string]: unknown;
};

// ── Calls ────────────────────────────────────────────────────────────────────

export async function listRuns(opts?: {
  kind?: RunKind;
  limit?: number;
}): Promise<RunRecord[]> {
  const params = new URLSearchParams();
  if (opts?.kind) params.set("kind", opts.kind);
  if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
  const qs = params.toString() ? `?${params.toString()}` : "";
  const body = await api<{ runs: RunRecord[] }>(`/api/runs${qs}`);
  return body.runs;
}

export async function getRun(runId: string): Promise<RunRecord> {
  return api<RunRecord>(`/api/runs/${encodeURIComponent(runId)}`);
}

export async function rerunRun(
  runId: string,
  body: RerunBody = {},
): Promise<RerunResponse> {
  return api<RerunResponse>(
    `/api/runs/${encodeURIComponent(runId)}/rerun`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function cancelRun(runId: string): Promise<{ status: string }> {
  return api<{ status: string }>(
    `/api/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );
}

export async function deleteRun(
  runId: string,
): Promise<{ status: string; run_id: string }> {
  return api<{ status: string; run_id: string }>(
    `/api/runs/${encodeURIComponent(runId)}`,
    { method: "DELETE" },
  );
}

export async function clearRuns(
  statuses?: RunStatus[],
): Promise<{ deleted: number }> {
  return api<{ deleted: number }>("/api/runs/clear", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(statuses ? { statuses } : {}),
  });
}

export async function getEffectivePrompts(
  runId: string,
): Promise<EffectivePromptsManifest> {
  return api<EffectivePromptsManifest>(
    `/api/runs/${encodeURIComponent(runId)}/effective-prompts`,
  );
}

/**
 * Open a browser-native EventSource for ``GET /api/runs/{run_id}/stream``.
 *
 * Returns the raw handle so the caller controls listeners and lifetime.
 * The stream emits two named events:
 *   - ``log`` — JSON-encoded :class:`LogEvent`.
 *   - ``done`` — JSON-encoded terminal :class:`RunRecord`.
 *
 * The caller MUST close the handle on the ``done`` event (and on unmount).
 * Failing to do so lets EventSource reconnect indefinitely after the
 * backend closes the stream.
 */
export async function openRunStream(runId: string): Promise<EventSource> {
  const base = await getApiBase();
  return new EventSource(
    `${base}/api/runs/${encodeURIComponent(runId)}/stream`,
  );
}
