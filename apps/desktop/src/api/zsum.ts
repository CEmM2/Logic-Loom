/**
 * Typed wrappers for /api/zsum/*. SSE streaming is handled directly in
 * the consumer (EventSource) since the api<T>() wrapper is JSON-only.
 */

import { api, getApiBase } from "./client";
import type { LocalModelDefaults } from "../zsum/config-schema";

export type { LocalModelDefaults };

export type ZsumStage =
  | "skeleton"
  | "index"
  | "extract"
  | "cards"
  | "digests"
  | "redigest"
  | "publish"
  | "all"
  | "stats"
  | "citations"
  | "reviews"
  | "restructure";

export type RunStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

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
};

export type LogEvent = {
  ts: string;
  level: string;
  line: string;
};

export async function getZsumStatus() {
  return api<{ zsum_repo: string | null; vault_path: string | null; cache_path: string }>(
    `/api/zsum/status`,
  );
}

export async function listRuns(): Promise<RunRecord[]> {
  const body = await api<{ runs: RunRecord[] }>(`/api/zsum/runs`);
  return body.runs;
}

export async function getRun(id: string): Promise<RunRecord> {
  return api<RunRecord>(`/api/zsum/runs/${id}`);
}

export async function submitRun(
  stage: ZsumStage,
  opts: { config_overrides?: Record<string, string>; extra_args?: string[] } = {},
): Promise<RunRecord> {
  return api<RunRecord>(`/api/zsum/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      stage,
      config_overrides: opts.config_overrides ?? {},
      extra_args: opts.extra_args ?? [],
    }),
  });
}

export async function cancelRun(id: string) {
  return api<{ status: string }>(`/api/zsum/runs/${id}/cancel`, { method: "POST" });
}

// ── Library: index.json + BBT-live ──────────────────────────────
export type CardState = "cached" | "pending" | "degraded" | "no_pdf";
export type DigestState = "ok" | "stale" | "missing";

export interface CollectionIndex {
  name: string;
  parent: string | null;
  path: string;
  item_count: number;
  digest_state: DigestState;
  redigest_state: DigestState;
}

export interface ItemIndex {
  citekey: string;
  year: number;
  venue: string;
  collections: string[];
  card_state: CardState;
  paper_type?: string | null;
  citation_count?: number | null;
}

export interface ZsumIndex {
  generated_at?: string | null;
  collections: Record<string, CollectionIndex>;
  items: Record<string, ItemIndex>;
}

export interface LibraryStats {
  total_collections: number;
  total_papers: number;
  cards_cached: number;
  cards_pending: number;
  cards_degraded: number;
  cards_no_pdf: number;
  digests_ok: number;
  digests_stale: number;
  digests_missing: number;
}

export type IndexResponse =
  | { kind: "ok"; cache_dir: string; index: ZsumIndex; stats: LibraryStats; message?: string }
  | { kind: "missing" | "parse" | "invalid" | "io"; cache_dir: string; message: string };

export async function getZsumIndex(): Promise<IndexResponse> {
  return api<IndexResponse>(`/api/zsum/index`);
}

export type BbtStatus = {
  is_live: boolean;
  mtime: number | null;
  bbt_json: string | null;
  reason?: string;
  age_seconds?: number;
};

export async function getBbtStatus() {
  return api<BbtStatus>(`/api/zsum/bbt-status`);
}

// ── Config doc (zsum.config.yaml) ───────────────────────────────
// ── Effective config (which zsum.config.yaml is actually in use) ────
export type EffectiveConfig = {
  config_path: string;
  config_exists: boolean;
  /** "override" → set explicitly in LogicLoom settings; "default" → fallback to <zsum_repo>/zsum.config.yaml */
  config_source: "override" | "default";
  vault_output_raw: string | null;
  vault_output_resolved: string | null;
  cache_dir_raw: string | null;
  cache_dir_resolved: string;
  index_json_path: string;
  index_exists: boolean;
  logic_loom_vault_path: string | null;
  vault_mismatch: boolean;
  /** A vault-relative zsum.config.yaml that the user might want to switch to. */
  vault_candidate: string | null;
};

export async function getEffectiveConfig() {
  return api<EffectiveConfig>(`/api/zsum/effective-config`);
}

export type ConfigDocState = {
  path: string;
  exists: boolean;
  raw: string;
  parse_error: string | null;
  // Plain dict view of the YAML — server strips ruamel internals.
  parsed: Record<string, unknown> | null;
  local_model_defaults: LocalModelDefaults | null;
};

export type LocalModelTier = {
  id: string;
  model: string;
  ram: string;
  params: string;
  note: string;
  is_default: boolean;
};

export type LocalModelsState = {
  tiers: LocalModelTier[];
  custom_models: string[];
  last_installed_model: string | null;
  current_config_model: string | null;
  requires_openai_compatible: boolean;
  ollama_endpoint: string | null;
  config_path: string;
  local_model_defaults: LocalModelDefaults | null;
};

export type LocalModelInstallResponse = LocalModelsState & {
  status: "configured" | "failed";
  installed_model: string;
  /** Dotted-path fields backfilled into zsum.config.yaml on install. Empty
   * when the user already has explicit values set for every relevant path. */
  backfilled_paths: string[];
  output: string;
  cmd: string[];
  cwd: string;
  exit_code: number | null;
};

export async function getConfigDoc() {
  return api<ConfigDocState>(`/api/zsum/config-doc`);
}

export async function postConfigDoc(updates: Array<{ path: string; value: unknown }>) {
  return api<ConfigDocState>(`/api/zsum/config-doc`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ updates }),
  });
}

export async function getLocalModels() {
  return api<LocalModelsState>(`/api/zsum/local-models`);
}

export async function installLocalModel(model: string) {
  return api<LocalModelInstallResponse>(`/api/zsum/local-models/install`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model }),
  });
}

/**
 * Open an SSE stream for a run. Calls ``onEvent`` per log line and ``onDone``
 * when the run terminates. Returns a function to close the stream early.
 */
export async function streamRun(
  id: string,
  onEvent: (e: LogEvent) => void,
  onDone?: (final: RunRecord | null) => void,
): Promise<() => void> {
  const base = await getApiBase();
  const es = new EventSource(`${base}/api/zsum/runs/${id}/stream`);
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
