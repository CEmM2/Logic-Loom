import { api } from "./client";

// MechDSL executable-bridge plugin client.
// Mirrors api/app.ts conventions: typed wrappers over the shared `api<T>` fetch.

export type MechdslStatus = {
  available: boolean;
  package_installed: boolean;
  mechdsl_installed: boolean;
  capabilities: Record<string, unknown> | null;
  reason: string | null;
  // Taichi backs the `verify` action only; emit/transpile are Taichi-free.
  taichi_available: boolean;
  verify_available: boolean;
  platform_supported: boolean;
  platform_reason: string | null;
};

export type MechdslInstallStatus =
  | "idle"
  | "installing"
  | "installed"
  | "failed";

export type MechdslRuntime = {
  taichi_version: string;
  taichi_available: boolean;
  platform_supported: boolean;
  platform_reason: string | null;
  install_status: MechdslInstallStatus;
  log: string[];
  error: string | null;
  target_dir: string;
};

export type MechdslCompileRequest = {
  node_id?: string;
  graph_slice?: Record<string, unknown>;
  run_verify?: boolean;
};

export type MechdslCompileResult = {
  artifact_id: string;
  status: string | null;
  emitted_source_ref: string;
  vv_plan: Record<string, unknown> | null;
  provenance: Record<string, unknown> | null;
  warnings: string[];
};

// The cached artifact is the adapter's raw build_executable result (8-key dict).
export type MechdslArtifact = Record<string, unknown>;

export async function getMechdslStatus() {
  return api<MechdslStatus>("/api/mechdsl/status");
}

export async function compileMechdsl(req: MechdslCompileRequest) {
  return api<MechdslCompileResult>("/api/mechdsl/compile", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(req),
  });
}

export async function getMechdslArtifact(artifactId: string) {
  return api<MechdslArtifact>(
    `/api/mechdsl/artifacts/${encodeURIComponent(artifactId)}`,
  );
}

export async function getMechdslRuntime() {
  return api<MechdslRuntime>("/api/mechdsl/runtime");
}

// Non-blocking: kicks off the background install and returns current runtime
// state. Poll getMechdslRuntime() for progress.
export async function installTaichi() {
  return api<MechdslRuntime>("/api/mechdsl/runtime/install-taichi", {
    method: "POST",
  });
}
