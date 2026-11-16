import { api } from "./client";

// ── Types ─────────────────────────────────────────────────────────────────────

export type ClaudeAssetManifestEntry = {
  path: string;
  bundled_sha?: string;
  user_sha?: string;
  modified?: boolean;
};

export type ClaudeAssetManifest = {
  generated_at?: string;
  entries?: ClaudeAssetManifestEntry[];
  // Manifest can be a flat object of file → metadata as well — kept loose.
  [key: string]: unknown;
};

export type ClaudeAssetStatus = {
  installed: boolean;
  path: string;
  manifest: ClaudeAssetManifest | null;
};

export type InstallResult = {
  path: string;
  overwrite: boolean;
  backup_path: string;
  backup_exists: boolean;
  warning: string | null;
};

export type FileResult = {
  path: string;
  content: string;
  source?: "bundled" | "user_or_bundled";
};

export type ResetResult = {
  status: string;
  path: string;
};

export type MaterializeResult = {
  path: string;
};

export type FolderResult = {
  path: string;
  exists: boolean;
};

// ── Calls ────────────────────────────────────────────────────────────────────

export async function getClaudeAssetsStatus() {
  return api<ClaudeAssetStatus>("/api/akms/claude-assets/status");
}

export async function installClaudeAssets(overwrite: boolean) {
  const qs = new URLSearchParams({ overwrite: String(overwrite) }).toString();
  return api<InstallResult>(`/api/akms/claude-assets/install?${qs}`, {
    method: "POST",
  });
}

export async function readClaudeAssetFile(
  path: string,
  opts?: { default?: boolean },
) {
  const params: Record<string, string> = { path };
  if (opts?.default) params.default = "true";
  const qs = new URLSearchParams(params).toString();
  return api<FileResult>(`/api/akms/claude-assets/file?${qs}`);
}

export async function writeClaudeAssetFile(path: string, content: string) {
  const qs = new URLSearchParams({ path }).toString();
  return api<{ path: string }>(`/api/akms/claude-assets/file?${qs}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export async function resetClaudeAssetFile(path: string) {
  const qs = new URLSearchParams({ path }).toString();
  return api<ResetResult>(`/api/akms/claude-assets/reset?${qs}`, {
    method: "POST",
  });
}

export async function materializeRuntime() {
  return api<MaterializeResult>("/api/akms/claude-assets/materialize-runtime", {
    method: "POST",
  });
}

export async function getClaudeAssetsFolder() {
  return api<FolderResult>("/api/akms/claude-assets/folder");
}
