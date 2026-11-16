/**
 * Run history placeholder. The tab exists so the batch detail layout is
 * stable; per-batch runs are browsed on the Assistant Outputs page,
 * which groups runs by batch.
 */
import type { AssistantBatch } from "../../../api/assistantBatches";

export interface BatchRunHistoryProps {
  batch: AssistantBatch;
}

export function BatchRunHistory({ batch }: BatchRunHistoryProps) {
  return (
    <div
      data-testid="batch-run-history"
      className="flex flex-col gap-2 text-sm"
    >
      <p className="text-muted-foreground">
        Run history for <code>{batch.id}</code> is not listed on this tab
        yet. Runs launched from this batch appear under Assistant ▸ Outputs.
      </p>
    </div>
  );
}
