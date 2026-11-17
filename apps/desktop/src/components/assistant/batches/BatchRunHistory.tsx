/**
 * Run history for one Assistant batch: the Assistant runs from the shared
 * run registry whose ``batch_id`` matches, newest first. Each row links to
 * the Outputs page, which shows the run's files.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import type { AssistantBatch } from "../../../api/assistantBatches";
import { listAssistantOutputs } from "../../../api/assistantOutputs";
import type { RunRecord } from "../../../api/runs";
import { ASSISTANT_ROUTE_OUTPUTS } from "../../../routes/assistant/routes";

export interface BatchRunHistoryProps {
  batch: AssistantBatch;
}

export function runsForBatch(runs: readonly RunRecord[], batchId: string): RunRecord[] {
  return runs
    .filter((r) => r.batch_id === batchId)
    .sort((a, b) => b.started_at.localeCompare(a.started_at));
}

export function BatchRunHistory({ batch }: BatchRunHistoryProps) {
  const runsQuery = useQuery({
    queryKey: ["assistant-outputs"],
    queryFn: listAssistantOutputs,
    staleTime: 5_000,
  });

  const runs = runsQuery.data ? runsForBatch(runsQuery.data, batch.id) : [];

  return (
    <div data-testid="batch-run-history" className="flex flex-col gap-3 text-sm">
      {runsQuery.isLoading && <p className="text-muted-foreground">Loading runs…</p>}
      {runsQuery.isError && (
        <p className="text-destructive">
          Could not load runs: {String((runsQuery.error as Error)?.message ?? runsQuery.error)}
        </p>
      )}
      {runsQuery.isSuccess && runs.length === 0 && (
        <p className="text-muted-foreground">
          No runs yet. Run a workflow on this batch from the Workflows tab or
          Assistant ▸ Workflows, and it will be listed here.
        </p>
      )}
      {runs.length > 0 && (
        <table className="w-full text-left">
          <thead className="text-xs text-muted-foreground">
            <tr>
              <th className="py-1 pr-3 font-medium">Workflow</th>
              <th className="py-1 pr-3 font-medium">Status</th>
              <th className="py-1 pr-3 font-medium">Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} data-testid={`batch-run-${run.id}`} className="border-t">
                <td className="py-1.5 pr-3">
                  {run.run_label || run.workflow_id || run.stage}
                  <div className="font-mono text-xs text-muted-foreground">{run.id}</div>
                </td>
                <td className="py-1.5 pr-3">{run.status}</td>
                <td className="py-1.5 pr-3">{new Date(run.started_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Link to={ASSISTANT_ROUTE_OUTPUTS} className="text-xs underline underline-offset-2">
        Open Assistant ▸ Outputs to read run files
      </Link>
    </div>
  );
}
