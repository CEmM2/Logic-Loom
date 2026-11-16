/**
 * Batches API client (routes at /api/batches).
 *
 * The AKMS Batches page consumes this module to list,
 * inspect, edit, and run workflows against editable batches.
 */
import { ApiError, api, getApiBase } from "./client";

// ── Types ─────────────────────────────────────────────────────────────────────

export type BatchStatus = "active" | "archived" | "completed";
export type BatchSource = "user" | "import" | "duplicate";
/**
 * Origin of a batch: "starter" = seeded from the bundled starter pack
 * (Tier B — user-owned draft content); "user" = user-authored.
 * Optional so that cached payloads without the field treat it as "user".
 */
export type BatchOrigin = "user" | "starter";

export type BatchNode = {
  id: string;
  title?: string;
  notes?: string;
  // Loose shape — backend allows extra fields per node.
  [key: string]: unknown;
};

export type EditableBatch = {
  id: string;
  title: string;
  description: string;
  round: number | null;
  round_title: string;
  round_theme: string;
  status: BatchStatus;
  source: BatchSource;
  /** Absent on older cached payloads — treat undefined as "user". */
  origin?: BatchOrigin;
  created_at: string;
  updated_at: string;
  papers: string[];
  nodes: BatchNode[];
  notebook_id: string | null;
  zotsums_text: string;
  sources_text: string;
  notes: string;
  default_workflow_id: string | null;
  prompt_profile_id: string | null;
  prompt_overrides: Record<string, string>;
  [key: string]: unknown;
};

export type ListBatchesResponse = {
  batches: EditableBatch[];
};

export type PatchBatchBody = {
  title?: string;
  description?: string;
  round?: number | null;
  round_title?: string;
  round_theme?: string;
  status?: BatchStatus;
  notebook_id?: string | null;
  zotsums_text?: string;
  sources_text?: string;
  notes?: string;
  default_workflow_id?: string | null;
  prompt_profile_id?: string | null;
  prompt_overrides?: Record<string, string>;
};

export type CreateBatchBody = {
  title: string;
  description?: string;
  round?: number | null;
};

// ── Run-workflow types (POST /api/runs/workflow) ─────────────────────────────

export type RunWorkflowRequest = {
  batch_id: string;
  workflow_id: string;
  notebook_id?: string | null;
  prompt_profile_id?: string | null;
  prompt_overrides?: Record<string, string>;
  run_label?: string | null;
  save_as_batch_default?: boolean;
  workflow_inputs?: Record<string, string> | null;
  dry_run?: boolean;
};

export type RunRecord = {
  run_id: string;
  kind: string;
  status: string;
  batch_id?: string | null;
  workflow_id?: string | null;
  started_at?: string;
  ended_at?: string | null;
};

export type EffectivePromptEntry = {
  slot: string;
  variant_id: string;
  resolved_text: string;
  source?: string;
  family?: string;
};

export type EffectivePromptsManifest = {
  // The on-disk manifest schema is loose — we render whatever entries the
  // backend produced. Either an explicit `entries` array or a flat map of
  // slot → entry.
  entries?: EffectivePromptEntry[];
  [key: string]: unknown;
};

// ── Calls ────────────────────────────────────────────────────────────────────

export async function listBatches() {
  return api<ListBatchesResponse>("/api/batches");
}

export async function getBatch(batchId: string) {
  return api<EditableBatch>(`/api/batches/${encodeURIComponent(batchId)}`);
}

export async function createBatch(body: CreateBatchBody) {
  return api<EditableBatch>("/api/batches", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function patchBatch(batchId: string, body: PatchBatchBody) {
  return api<EditableBatch>(`/api/batches/${encodeURIComponent(batchId)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function archiveBatch(batchId: string) {
  return api<EditableBatch>(`/api/batches/${encodeURIComponent(batchId)}/archive`, {
    method: "POST",
  });
}

export async function deleteBatch(batchId: string): Promise<void> {
  // Bypass the JSON-parsing `api()` wrapper because DELETE returns 204 No
  // Content; calling res.json() on that throws.
  const base = await getApiBase();
  const res = await fetch(
    `${base}/api/batches/${encodeURIComponent(batchId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = await res.json(); } catch { /* leave null */ }
    throw new ApiError(res.status, `HTTP ${res.status} on /api/batches/${batchId}`, detail);
  }
}

export async function duplicateBatch(batchId: string) {
  return api<EditableBatch>(`/api/batches/${encodeURIComponent(batchId)}/duplicate`, {
    method: "POST",
  });
}

/**
 * Start a workflow run.
 *
 * ``akms.node_extraction`` is routed to
 * ``POST /api/akms/node-generation/runs`` so the backend attaches a live
 * executor (and creates a NotebookLM notebook when no ``nlm_id`` /
 * ``notebook_id`` is supplied). Other workflows stay on the generic
 * ``POST /api/runs/workflow`` path. Dry-run previews always use the
 * generic path so they never create notebooks or spawn agents.
 */
export async function runWorkflow(body: RunWorkflowRequest) {
  if (body.workflow_id === "akms.node_extraction" && !body.dry_run) {
    const wi = body.workflow_inputs ?? {};
    return api<RunRecord>("/api/akms/node-generation/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        batch_id: body.batch_id,
        notebook_id: body.notebook_id ?? null,
        nlm_id: wi.nlm_id ?? body.notebook_id ?? null,
        node_selection: wi.node_selection ?? null,
        plan_path: wi.plan_path ?? null,
        prompt_profile_id: body.prompt_profile_id ?? null,
        prompt_overrides: body.prompt_overrides ?? {},
        run_label: body.run_label ?? null,
        save_as_batch_default: body.save_as_batch_default ?? false,
        dry_run: false,
      }),
    });
  }
  return api<RunRecord>("/api/runs/workflow", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Resolve effective prompts for a (batch, workflow, profile, overrides) tuple
 * WITHOUT starting a run. The frontend uses `dry_run=true` against
 * POST /api/runs/workflow to preview which prompt variants would fire.
 */
export async function previewEffectivePrompts(body: Omit<RunWorkflowRequest, "dry_run">) {
  return api<{ run_id?: string; effective_prompts: EffectivePromptsManifest }>(
    "/api/runs/workflow",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...body, dry_run: true }),
    },
  );
}
