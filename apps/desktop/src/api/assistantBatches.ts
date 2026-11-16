/**
 * Assistant Batches API client (routes at /api/assistant/batches/*).
 *
 * Mirrors `api/batches.ts` style.
 */
import { api } from "./client";

// ── Types ─────────────────────────────────────────────────────────────────────

export type SourceItemKind =
  | "zotero"
  | "discovered"
  | "external_pdf"
  | "url"
  | "manual";

export type SourceBatchStatus = "draft" | "active" | "archived";

export type ZoteroMatch = {
  citekey: string;
  library_id?: string | null;
  item_key?: string | null;
};

export type BatchSourceItem = {
  id: string;
  kind: SourceItemKind;
  title: string;
  citekey?: string | null;
  authors?: string[];
  year?: number | null;
  doi?: string | null;
  openalex_id?: string | null;
  semantic_scholar_id?: string | null;
  local_pdf_path?: string | null;
  external_pdf_url?: string | null;
  url?: string | null;
  zotero_match?: ZoteroMatch | null;
};

export type AssistantBatch = {
  schema_version: number;
  id: string;
  title: string;
  description: string;
  status: SourceBatchStatus;
  tags: string[];
  purpose: string | null;
  sources: BatchSourceItem[];
  notebook_id: string | null;
  default_workflow_id: string | null;
  prompt_profile_id: string | null;
  prompt_overrides: Record<string, string>;
  akms_node_plan: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
};

export type ListAssistantBatchesResponse = {
  batches: AssistantBatch[];
};

export type CreateAssistantBatchBody = {
  title: string;
  description?: string;
  tags?: string[];
  purpose?: string | null;
  sources?: BatchSourceItem[];
  notebook_id?: string | null;
  default_workflow_id?: string | null;
  prompt_profile_id?: string | null;
  prompt_overrides?: Record<string, string>;
};

export type PatchAssistantBatchBody = Partial<{
  title: string;
  description: string;
  status: SourceBatchStatus;
  tags: string[];
  purpose: string | null;
  notebook_id: string | null;
  default_workflow_id: string | null;
  prompt_profile_id: string | null;
  prompt_overrides: Record<string, string>;
}>;

export type SendToAkmsResponse = {
  akms_batch_id: string;
  akms_workspace_path: string;
};

// ── Calls ────────────────────────────────────────────────────────────────────

export async function listAssistantBatches() {
  return api<ListAssistantBatchesResponse>("/api/assistant/batches");
}

export async function getAssistantBatch(batchId: string) {
  return api<AssistantBatch>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}`,
  );
}

export async function createAssistantBatch(body: CreateAssistantBatchBody) {
  return api<AssistantBatch>("/api/assistant/batches", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function patchAssistantBatch(
  batchId: string,
  body: PatchAssistantBatchBody,
) {
  return api<AssistantBatch>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function archiveAssistantBatch(batchId: string) {
  return api<AssistantBatch>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}/archive`,
    { method: "POST" },
  );
}

export async function deleteAssistantBatch(batchId: string) {
  return api<AssistantBatch>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}`,
    { method: "DELETE" },
  );
}

export async function duplicateAssistantBatch(batchId: string) {
  return api<AssistantBatch>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}/duplicate`,
    { method: "POST" },
  );
}

export async function addAssistantBatchSources(
  batchId: string,
  sources: BatchSourceItem[],
) {
  return api<AssistantBatch>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}/sources/add`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sources }),
    },
  );
}

export async function removeAssistantBatchSources(
  batchId: string,
  ids: string[],
) {
  return api<AssistantBatch>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}/sources/remove`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ids }),
    },
  );
}

export async function reorderAssistantBatchSources(
  batchId: string,
  ordering: string[],
) {
  return api<AssistantBatch>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}/sources/reorder`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ordering }),
    },
  );
}

export async function sendAssistantBatchToAkms(batchId: string) {
  return api<SendToAkmsResponse>(
    `/api/assistant/batches/${encodeURIComponent(batchId)}/send-to-akms`,
    { method: "POST" },
  );
}
