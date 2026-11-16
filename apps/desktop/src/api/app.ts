import { api } from "./client";

export type AppConfig = {
  // Optional dev-only source-checkout overrides: null when unset.
  logic_loom_root: string | null;
  akms_repo: string | null;
  akms_workspace: string;
  akms_global_vault: string | null;
  akms_global_vault_effective: string;
  zsum_repo: string | null;
  vault_path: string | null;
  cache_path: string;
  graph_cache_path: string;
  search_backend: "ripgrep" | "qmd";
  vault_qmd_collection: string | null;
  // Zotero Summarizer mirror — see Settings model in logic_loom_api/config.py.
  zsum_config_path: string | null;
  zsum_config_path_effective: string;
  zsum_python_cmd: string;
  zsum_cost_per_mtoken_in: number;
  zsum_cost_per_mtoken_out: number;
  zsum_tokens_per_card_in: number;
  zsum_tokens_per_card_out: number;
  // Nodes Review
  nodes_folders: string[];
  nodes_editor: string | null;
  // Assistant mode.
  openalex_user_email: string | null;
  openalex_api_key: string | null;
  semantic_scholar_api_key: string | null;
  assistant_workspace: string | null;
  assistant_workspace_colocate: boolean;
  assistant_workspace_effective: string;
  // Host LLM provider (unified layer; provider keys + legacy Learn alias).
  anthropic_api_key: string | null;
  openai_api_key: string | null;
  google_api_key: string | null;
  // Global host-LLM provider. Effective global = llm_provider or learn_llm_provider.
  llm_provider: string | null;
  // Legacy/back-compat alias for the global llm_provider (D3). Still feeds the
  // global when llm_provider is unset.
  learn_llm_provider: string | null;
  // Per-consumer overrides. null/unset = "use global".
  zsum_llm_provider: string | null;
  nodegen_llm_provider: string | null;
  learn_llm_custom_clis: Record<string, string>;
};

export type FirstRunStatus = {
  needs_setup: boolean;
  missing_or_placeholder: string[];
  configured: {
    akms_repo: string | null;
    akms_global_vault: string | null;
    zsum_repo: string | null;
    vault_path: string | null;
  };
  detected: {
    akms_repo: string | null;
    akms_global_vault: string | null;
    zsum_repo: string | null;
    vault_path: string | null;
  };
  config_path: string;
};

export type ConfigUpdate = Partial<{
  akms_repo: string;
  akms_workspace: string;
  akms_global_vault: string | null;
  zsum_repo: string;
  vault_path: string;
  search_backend: "ripgrep" | "qmd";
  vault_qmd_collection: string | null;
  // Empty string clears the field on the server (= falls back to default).
  zsum_config_path: string | null;
  zsum_python_cmd: string;
  zsum_cost_per_mtoken_in: number;
  zsum_cost_per_mtoken_out: number;
  zsum_tokens_per_card_in: number;
  zsum_tokens_per_card_out: number;
  // Nodes Review
  nodes_folders: string[];
  nodes_editor: string;
  // Assistant mode.
  openalex_user_email: string;
  openalex_api_key: string;
  semantic_scholar_api_key: string;
  assistant_workspace: string;
  assistant_workspace_colocate: boolean;
  // Host LLM provider. Empty string clears the key/choice on the server
  // (= falls back: per-consumer override → global llm_provider → learn alias).
  anthropic_api_key: string;
  openai_api_key: string;
  google_api_key: string;
  llm_provider: string;
  learn_llm_provider: string;
  // Per-consumer overrides. Empty string clears the override → "use global".
  zsum_llm_provider: string;
  nodegen_llm_provider: string;
  learn_llm_custom_clis: Record<string, string>;
}>;

export type PathExistsResult = {
  path: string;
  exists: boolean;
  is_file: boolean;
  is_dir: boolean;
};

export type ZsumTestResult = {
  exit_code: number;
  stdout: string;
  stderr: string;
  cmd: string[];
  cwd: string;
};

export type AkmsStatus = {
  available: boolean;
  package_installed: boolean;
  picker_package_installed: boolean;
  graph_package_installed: boolean;
  graph_available: boolean;
  repo_exists: boolean;
  akms_global_vault_exists: boolean;
  akms_repo: string | null;
  akms_global_vault: string;
};

export async function getAkmsStatus() {
  return api<AkmsStatus>("/api/app/akms-status");
}

export async function getConfig() {
  return api<AppConfig>("/api/app/config");
}

export async function postConfig(patch: ConfigUpdate) {
  return api<{ status: string; path: string; applied: ConfigUpdate }>("/api/app/config", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export async function getFirstRunStatus() {
  return api<FirstRunStatus>("/api/app/config/first-run");
}

export async function saveFirstRun(payload: {
  akms_repo?: string;
  akms_global_vault?: string;
  zsum_repo?: string;
  vault_path: string;
  search_backend?: "ripgrep" | "qmd";
  vault_qmd_collection?: string | null;
}) {
  return api<{ status: string; path: string }>("/api/app/config/first-run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function reloadConfig() {
  return api<{ status: string }>("/api/app/config/reload", { method: "POST" });
}

export async function pathExists(path: string) {
  const qs = new URLSearchParams({ path }).toString();
  return api<PathExistsResult>(`/api/app/path-exists?${qs}`);
}

export async function testZsumEnvironment() {
  return api<ZsumTestResult>("/api/zsum/test", { method: "POST" });
}

// ── Assistant provider test-connection ───────────────────────────────────

export type AssistantTestProvider = "openalex" | "semantic_scholar";

export type AssistantTestConnectionResult = {
  ok: boolean;
  latency_ms: number;
  quota_remaining: number | null;
  detail: string | null;
};

export async function testAssistantProvider(provider: AssistantTestProvider) {
  return api<AssistantTestConnectionResult>(
    `/api/app/assistant/test-connection/${provider}`,
  );
}
