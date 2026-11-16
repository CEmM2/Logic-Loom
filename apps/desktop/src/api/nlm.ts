/**
 * Typed wrappers for /api/nlm/* — NotebookLM notebook creation,
 * node generation, and node validation runs.
 */

import { api, getApiBase } from "./client";
import type { LogEvent, RunRecord } from "./zsum";

export type { LogEvent, RunRecord };

// ── Request types ────────────────────────────────────────────────────────────

export type NotebookRequest = {
  batch_id: string;
  collection?: string;
  max_papers?: number;
  dry_run?: boolean;
};

export type GenerateRequest = {
  batch_id: string;
  notebook_id?: string;
  model?: string;
  batch_size?: number;
  max_tokens?: number;
  dry_run?: boolean;
  resume_from?: number;
  nlm_mcp_url?: string;
};

export type NlmBatchRequest = {
  batch_id: string;
  plan_path: string;
  prompt_file: string;
  out_dir?: string;
  template_file?: string;
  notebook_id?: string;
  source_ids?: string[];
  timeout?: number;
  output_format?: "yaml" | "json";
  profile?: string;
  max_retries?: number;
  force?: boolean;
  dry_run?: boolean;
};

export type ValidateRequest = {
  batch_id: string;
  node_id?: string;
  concurrency?: number;
  timeout?: number;
  dry_run?: boolean;
};

// ── API calls ────────────────────────────────────────────────────────────────

export async function submitNotebook(req: NotebookRequest): Promise<RunRecord> {
  return api<RunRecord>(`/api/nlm/notebook`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(req),
  });
}

export async function submitGenerate(req: GenerateRequest): Promise<RunRecord> {
  return api<RunRecord>(`/api/nlm/generate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(req),
  });
}

export async function submitNlmBatch(req: NlmBatchRequest): Promise<RunRecord> {
  return api<RunRecord>(`/api/nlm/nlm-batch`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(req),
  });
}

export async function submitValidate(req: ValidateRequest): Promise<RunRecord> {
  return api<RunRecord>(`/api/nlm/validate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(req),
  });
}

export async function listNlmRuns(): Promise<RunRecord[]> {
  const body = await api<{ runs: RunRecord[] }>(`/api/nlm/runs`);
  return body.runs;
}

export async function getNlmRun(id: string): Promise<RunRecord> {
  return api<RunRecord>(`/api/nlm/runs/${id}`);
}

export async function cancelNlmRun(id: string) {
  return api<{ status: string }>(`/api/nlm/runs/${id}/cancel`, { method: "POST" });
}

export async function streamNlmRun(
  id: string,
  onEvent: (e: LogEvent) => void,
  onDone?: (final: RunRecord | null) => void,
): Promise<() => void> {
  const base = await getApiBase();
  const es = new EventSource(`${base}/api/nlm/runs/${id}/stream`);
  es.addEventListener("log", (raw) => {
    try {
      const data = JSON.parse((raw as MessageEvent).data) as LogEvent;
      onEvent(data);
    } catch {
      /* ignore malformed line */
    }
  });
  es.addEventListener("done", (raw) => {
    let final: RunRecord | null = null;
    try {
      final = JSON.parse((raw as MessageEvent).data);
    } catch {
      /* leave null */
    }
    onDone?.(final);
    es.close();
  });
  es.onerror = () => {
    es.close();
  };
  return () => es.close();
}
