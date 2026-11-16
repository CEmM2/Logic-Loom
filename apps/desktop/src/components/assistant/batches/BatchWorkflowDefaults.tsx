/**
 * Workflow defaults panel — sets `default_workflow_id`.
 *
 * The picker lists workflows from the Workflow Library
 * (`listWorkflows`).
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import type { AssistantBatch } from "../../../api/assistantBatches";
import { listWorkflows } from "../../../api/workflows";

export interface BatchWorkflowDefaultsProps {
  batch: AssistantBatch;
  onSave: (workflowId: string | null) => void;
  saving?: boolean;
  saveError?: string | null;
}

export function BatchWorkflowDefaults({
  batch,
  onSave,
  saving = false,
  saveError = null,
}: BatchWorkflowDefaultsProps) {
  const [workflowId, setWorkflowId] = useState(batch.default_workflow_id ?? "");
  // Also re-sync when default_workflow_id changes on the same batch —
  // not just on a batch-id swap — so a fresh save round-trip reflects in
  // the form without the user having to navigate away and back.
  useEffect(() => {
    setWorkflowId(batch.default_workflow_id ?? "");
  }, [batch.id, batch.default_workflow_id]);

  const dirty =
    (workflowId || null) !== (batch.default_workflow_id ?? null);
  const savedClean = !dirty && !saving && batch.default_workflow_id;

  const workflowsQ = useQuery({
    queryKey: ["workflows", "list"],
    queryFn: () => listWorkflows(false),
  });
  const workflows = workflowsQ.data?.workflows ?? [];

  return (
    <div data-testid="batch-workflow-defaults" className="flex flex-col gap-2">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Default workflow
        </span>
        <select
          data-testid="workflow-id"
          value={workflowId}
          onChange={(e) => setWorkflowId(e.target.value)}
          className="rounded border px-2 py-1 text-sm"
        >
          <option value="">(none)</option>
          {workflows.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name} ({w.id})
            </option>
          ))}
        </select>
        {workflowsQ.isLoading && (
          <span className="text-[10px] text-muted-foreground">Loading workflows…</span>
        )}
      </label>
      {saveError && (
        <div
          data-testid="workflow-save-error"
          className="rounded border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive"
        >
          Save failed: {saveError}
        </div>
      )}
      <div className="flex items-center justify-end gap-3">
        {savedClean && (
          <span
            data-testid="workflow-saved-indicator"
            className="text-xs text-emerald-700"
          >
            ✓ Saved.
          </span>
        )}
        <button
          type="button"
          data-testid="workflow-save"
          disabled={!dirty || saving}
          onClick={() => onSave(workflowId.trim() || null)}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          {saving ? "Saving…" : "Set default"}
        </button>
      </div>
    </div>
  );
}
