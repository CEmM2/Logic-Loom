/**
 * "Send to AKMS" button — invokes POST /api/assistant/batches/{id}/send-to-akms
 * and surfaces the returned `akms_batch_id` in a status banner.
 *
 * The component name `SendBatchToWorkflowButton` is historical: workflow
 * runs go through their own dialog (`RunWorkflowFromAssistant`). The name
 * is kept to avoid churn in callers.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  sendAssistantBatchToAkms,
  type SendToAkmsResponse,
} from "../../../api/assistantBatches";

export interface SendBatchToWorkflowButtonProps {
  batchId: string;
}

export function SendBatchToWorkflowButton({
  batchId,
}: SendBatchToWorkflowButtonProps) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<
    | { state: "idle" }
    | { state: "pending" }
    | { state: "ok"; result: SendToAkmsResponse }
    | { state: "error"; message: string }
  >({ state: "idle" });

  async function trigger() {
    setStatus({ state: "pending" });
    try {
      const result = await sendAssistantBatchToAkms(batchId);
      setStatus({ state: "ok", result });
      // Invalidate the AKMS batches caches so when the user switches
      // modes they see the freshly-imported (or overwritten) batch with
      // its current default_workflow_id / prompt_profile_id, instead of
      // whatever copy was already cached client-side.
      qc.invalidateQueries({ queryKey: ["akms", "batches", "list"] });
      qc.invalidateQueries({ queryKey: ["akms", "batch", result.akms_batch_id] });
    } catch (err) {
      setStatus({
        state: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return (
    <div
      data-testid="send-to-akms-container"
      className="flex flex-col gap-2"
    >
      <button
        type="button"
        data-testid="send-to-akms-button"
        onClick={trigger}
        disabled={status.state === "pending"}
        className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
      >
        {status.state === "pending" ? "Sending…" : "Send to AKMS"}
      </button>
      {status.state === "ok" && (
        <div
          data-testid="send-to-akms-toast"
          className="rounded border border-green-500/40 bg-green-500/10 px-3 py-2 text-xs"
        >
          <div>
            AKMS batch id:{" "}
            <code data-testid="send-to-akms-id">
              {status.result.akms_batch_id}
            </code>
          </div>
          <div>
            Workspace:{" "}
            <code data-testid="send-to-akms-workspace">
              {status.result.akms_workspace_path}
            </code>
          </div>
        </div>
      )}
      {status.state === "error" && (
        <div
          data-testid="send-to-akms-error"
          className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs"
        >
          {status.message}
        </div>
      )}
    </div>
  );
}
