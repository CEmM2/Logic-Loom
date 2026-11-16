/**
 * Assistant Outputs API client — typed wrappers around
 * ``/api/assistant/outputs/*``.
 *
 * The Outputs page UI and its rerun + comparison flows
 * both consume this client. The endpoint set:
 *
 *   - listAssistantOutputs() → list runs filtered by Assistant
 *     workflow membership.
 *   - getPrimaryOutput(run_id) → markdown body of the primary output.
 *   - openRunFolder(run_id) → absolute run-dir path (FE opens via
 *     Tauri shell APIs).
 *   - exportRun(run_id) → zip blob of the run dir contents.
 */
import { api, getApiBase } from "./client";

import type { RunRecord } from "./runs";

const BASE = "/api/assistant/outputs";

export type AssistantOutputRecord = RunRecord;

export type PrimaryOutputResponse = {
  run_id: string;
  filename: string;
  content: string;
};

export type OpenFolderResponse = {
  path: string;
};

export async function listAssistantOutputs(): Promise<AssistantOutputRecord[]> {
  const body = await api<{ runs: AssistantOutputRecord[] }>(BASE);
  return body.runs;
}

export async function getPrimaryOutput(
  runId: string,
): Promise<PrimaryOutputResponse> {
  return api<PrimaryOutputResponse>(
    `${BASE}/${encodeURIComponent(runId)}/primary`,
  );
}

export async function openRunFolder(
  runId: string,
): Promise<OpenFolderResponse> {
  return api<OpenFolderResponse>(
    `${BASE}/${encodeURIComponent(runId)}/open-folder`,
    { method: "POST" },
  );
}

/**
 * Export the run dir as a zip. Returns the raw Blob so the caller can
 * trigger a download or hand it to a save-dialog. Bypasses the JSON
 * envelope helper because the response body is a binary stream.
 */
export async function exportRun(runId: string): Promise<Blob> {
  const base = await getApiBase();
  const url = `${base}${BASE}/${encodeURIComponent(runId)}/export`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    throw new Error(`exportRun failed: ${res.status} ${res.statusText}`);
  }
  return res.blob();
}
