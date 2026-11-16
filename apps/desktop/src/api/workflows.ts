/**
 * Workflows API client (routes at `/api/workflows`).
 *
 * Consumed by the Workflows page to list bundled +
 * user workflows, inspect details, duplicate, edit, and archive.
 *
 * Library-vs-request boundary: this client only mutates **workflow
 * definitions** (the on-disk library). It does NOT touch per-run
 * invocation parameters — those belong to the Run dialog
 * (`api/batches.ts::runWorkflow`).
 *
 * Mirrors the route contract in
 * `services/logic_loom_api/src/logic_loom_api/routes/workflows.py`.
 */
import { api } from "./client";

// ── Types mirroring services/logic_loom_api/.../workflows/models.py ──────────

export type WorkflowExecutor = "claude" | "zsum";
export type WorkflowSourceMode = "notebooklm" | "zotsum-local" | "none";
export type WorkflowOutputFormat = "yaml" | "json" | "markdown" | "text";
export type WorkflowStatus = "active" | "archived";
export type WorkflowSource = "bundled" | "user";

export type WorkflowValidation = {
  required_sections: string[];
};

export type WorkflowDefinition = {
  schema_version: 1;
  id: string;
  name: string;
  category: string;
  description: string;
  executor: WorkflowExecutor;
  source_mode: WorkflowSourceMode;
  input_slots: string[];
  output_format: WorkflowOutputFormat;
  default_output_subdir: string;
  prompt_slots: Record<string, string>;
  required_external_tools: string[];
  validation: WorkflowValidation;
  status: WorkflowStatus;
  source: WorkflowSource;
};

export type WorkflowPayload = {
  id?: string | null;
  name: string;
  category?: string;
  description?: string;
  executor: WorkflowExecutor;
  source_mode?: WorkflowSourceMode;
  input_slots?: string[];
  output_format?: WorkflowOutputFormat;
  default_output_subdir?: string;
  prompt_slots?: Record<string, string>;
  required_external_tools?: string[];
  validation?: WorkflowValidation | Record<string, unknown>;
};

export type ListWorkflowsResponse = {
  workflows: WorkflowDefinition[];
};

// ── Read ────────────────────────────────────────────────────────────────────

export async function listWorkflows(includeArchived = false) {
  const q = includeArchived ? "?include_archived=true" : "";
  return api<ListWorkflowsResponse>(`/api/workflows${q}`);
}

export async function getWorkflow(workflowId: string) {
  return api<WorkflowDefinition>(
    `/api/workflows/${encodeURIComponent(workflowId)}`,
  );
}

// ── Write (user workflows only on the backend) ──────────────────────────────

export async function createWorkflow(body: WorkflowPayload) {
  return api<WorkflowDefinition>("/api/workflows", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateWorkflow(workflowId: string, body: WorkflowPayload) {
  return api<WorkflowDefinition>(
    `/api/workflows/${encodeURIComponent(workflowId)}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function duplicateWorkflow(workflowId: string) {
  return api<WorkflowDefinition>(
    `/api/workflows/${encodeURIComponent(workflowId)}/duplicate`,
    { method: "POST" },
  );
}

export async function archiveWorkflow(workflowId: string) {
  return api<WorkflowDefinition>(
    `/api/workflows/${encodeURIComponent(workflowId)}/archive`,
    { method: "POST" },
  );
}

export async function deleteWorkflow(workflowId: string) {
  return api<{ status: string; workflow_id: string }>(
    `/api/workflows/${encodeURIComponent(workflowId)}`,
    { method: "DELETE" },
  );
}

export async function deleteAssistantWorkflow(workflowId: string) {
  return api<{ status: string; workflow_id: string }>(
    `/api/assistant/workflows/${encodeURIComponent(workflowId)}`,
    { method: "DELETE" },
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

/**
 * Group a list of workflows by source (bundled vs user) — keeps the
 * left-pane rendering trivial and stable across re-renders.
 */
export function groupBySource(workflows: WorkflowDefinition[]): {
  bundled: WorkflowDefinition[];
  user: WorkflowDefinition[];
} {
  const bundled: WorkflowDefinition[] = [];
  const user: WorkflowDefinition[] = [];
  for (const w of workflows) {
    if (w.source === "bundled") bundled.push(w);
    else user.push(w);
  }
  return { bundled, user };
}
