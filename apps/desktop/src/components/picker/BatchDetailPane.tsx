import { useState } from "react";

import type { RunRecord } from "../../api/nlm";
import {
  useBatch,
  useRemovePapers,
  useSubmitGenerate,
  useSubmitNlmBatch,
  useSubmitValidate,
} from "../../hooks/picker";
import { NlmBatchDialog } from "./NlmBatchDialog";
import { NlmRunDialog } from "./NlmRunDialog";
import { SyncNlmDialog } from "./SyncNlmDialog";

interface Props {
  batchId: string | null;
}

type NlmAction = "generate" | "generate-nlm" | "validate";

export function BatchDetailPane({ batchId }: Props) {
  const { data, isLoading, refetch } = useBatch(batchId);
  const removePapers = useRemovePapers();
  const [syncOpen, setSyncOpen] = useState(false);
  const [nlmBatchOpen, setNlmBatchOpen] = useState(false);

  const submitGenerate = useSubmitGenerate();
  const submitNlmBatch = useSubmitNlmBatch();
  const submitValidate = useSubmitValidate();
  const [nlmAction, setNlmAction] = useState<NlmAction | null>(null);
  const [nlmRunRecord, setNlmRunRecord] = useState<RunRecord | null>(null);

  if (!batchId) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
        Select a batch.
      </div>
    );
  }
  if (isLoading || !data) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
        Loading batch…
      </div>
    );
  }

  const { batch, papers, unknown_citekeys } = data;
  const hasNotebook = !!batch.notebook_id;

  const onGenerate = async () => {
    try {
      const rec = await submitGenerate.mutateAsync({
        batch_id: batch.id,
        notebook_id: batch.notebook_id ?? undefined,
      });
      setNlmAction("generate");
      setNlmRunRecord(rec);
    } catch {
      /* mutation error handled via isPending/isError */
    }
  };

  const onValidate = async () => {
    try {
      const rec = await submitValidate.mutateAsync({ batch_id: batch.id });
      setNlmAction("validate");
      setNlmRunRecord(rec);
    } catch {
      /* mutation error handled via isPending/isError */
    }
  };

  const anyPending =
    submitGenerate.isPending || submitNlmBatch.isPending || submitValidate.isPending;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <div className="flex items-baseline justify-between">
          <h3 className="text-base font-semibold">{batch.title || batch.id}</h3>
          <span className="text-xs text-muted-foreground">{batch.paper_count} papers</span>
        </div>

        {/* Action buttons row */}
        <div className="mt-2 flex flex-wrap gap-1.5">
          <button
            onClick={() => setSyncOpen(true)}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
            disabled={batch.paper_count === 0}
            title={batch.paper_count === 0 ? "Add papers first" : "Create NotebookLM notebook"}
          >
            Sync nlm
          </button>
          <button
            onClick={onGenerate}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!hasNotebook || anyPending}
            title={hasNotebook ? "Generate nodes via Claude API + NLM" : "Create notebook first"}
          >
            Generate (Claude)
          </button>
          <button
            onClick={() => setNlmBatchOpen(true)}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!hasNotebook || anyPending}
            title={hasNotebook ? "Generate nodes directly via NotebookLM (no Claude)" : "Create notebook first"}
          >
            Generate (NLM)
          </button>
          <button
            onClick={onValidate}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!hasNotebook || anyPending}
            title={hasNotebook ? "Validate nodes against NotebookLM" : "Create notebook first"}
          >
            Validate Nodes
          </button>
        </div>

        {batch.notebook_id && (
          <div className="mt-1 text-xs text-muted-foreground">
            notebook_id: <span className="font-mono">{batch.notebook_id}</span>
            {batch.n_uploaded ? <> · {batch.n_uploaded} uploaded</> : null}
          </div>
        )}

        {(submitGenerate.isError || submitNlmBatch.isError || submitValidate.isError) && (
          <div className="mt-1 text-xs text-red-600">
            {(submitGenerate.error as Error | null)?.message ??
              (submitNlmBatch.error as Error | null)?.message ??
              (submitValidate.error as Error | null)?.message}
          </div>
        )}
      </div>

      <ul className="flex-1 overflow-y-auto">
        {papers.map((p) => (
          <li
            key={p.citekey}
            className="flex items-start gap-2 border-b border-border px-3 py-2 text-xs"
          >
            <button
              onClick={() => removePapers.mutate({ batchId: batch.id, citekeys: [p.citekey] })}
              className="text-muted-foreground hover:text-red-600"
              title="Remove from batch"
            >
              ✕
            </button>
            <div className="flex-1">
              <div className="font-medium">{p.title || p.citekey}</div>
              <div className="text-muted-foreground">
                {p.authors.slice(0, 2).join(", ")}
                {p.authors.length > 2 && " et al."}
                {p.year ? ` · ${p.year}` : ""}
              </div>
            </div>
          </li>
        ))}
        {unknown_citekeys.length > 0 && (
          <li className="border-b border-border px-3 py-2 text-xs text-amber-600">
            {unknown_citekeys.length} citekey(s) not in current catalog:
            <span className="font-mono"> {unknown_citekeys.join(", ")}</span>
          </li>
        )}
      </ul>

      <SyncNlmDialog
        open={syncOpen}
        batchId={batch.id}
        batchTitle={batch.title || batch.id}
        paperCount={batch.paper_count}
        onClose={() => {
          setSyncOpen(false);
          refetch();
        }}
        onSuccess={() => refetch()}
      />

      <NlmBatchDialog
        open={nlmBatchOpen}
        batchId={batch.id}
        batchTitle={batch.title || batch.id}
        notebookId={batch.notebook_id ?? null}
        onLaunch={async (rec) => {
          setNlmBatchOpen(false);
          setNlmAction("generate-nlm");
          setNlmRunRecord(rec);
        }}
        onClose={() => setNlmBatchOpen(false)}
      />

      <NlmRunDialog
        open={!!nlmAction}
        action={nlmAction ?? "generate"}
        batchId={batch.id}
        batchTitle={batch.title || batch.id}
        runRecord={nlmRunRecord}
        onClose={() => {
          setNlmAction(null);
          setNlmRunRecord(null);
          refetch();
        }}
      />
    </div>
  );
}
