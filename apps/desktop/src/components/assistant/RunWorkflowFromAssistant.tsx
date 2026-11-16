/**
 * Run dialog UI for Assistant batches.
 *
 * Thin wrapper around the extracted ``RunWorkflowDialog``
 * (``components/batches/RunWorkflowDialog.tsx``) that pre-filters
 * workflows to the Assistant category set and pre-fills the output
 * directory hint to
 * ``<assistant_workspace>/assistant/outputs/<batch_id>/<workflow_id>/<run_id>``.
 *
 * Behaviour:
 *   1. ``workflowFilter={category => assistantCategories.includes(category)}``.
 *   2. Expose ``Preview effective prompts`` + ``Save as batch default``
 *      controls (inherited from the underlying dialog).
 *   3. Pre-fill ``out_dir`` cosmetic hint.
 *
 * Implementation note: the actual ``out_dir`` is recomputed
 * server-side by the run dispatcher (the backend
 * ``run_dir_for(...)``); the UI hint here is informational so the
 * user knows where the artefacts will land before pressing Run.
 *
 * The batch type accepted here is the AKMS-bundle
 * :class:`EditableBatch` — the Assistant wrapper expects callers to
 * have already mapped an :class:`AssistantBatch` onto
 * an EditableBatch via the same projection the assistant routes use
 * (``SourceBatch.to_editable_batch``).
 */
import {
  RunWorkflowDialog,
  type RunWorkflowDialogProps,
} from "../batches/RunWorkflowDialog";

/**
 * Closed set of Assistant workflow categories.
 * Mirrors the backend ``ASSISTANT_CATEGORIES`` frozenset in
 * ``services/.../routes/assistant_workflows.py``.
 */
export const ASSISTANT_CATEGORIES: ReadonlySet<string> = new Set([
  "Literature Review",
  "Paper Writing",
  "Proposal Writing",
  "Analysis",
]);

export function isAssistantCategory(category: string): boolean {
  return ASSISTANT_CATEGORIES.has(category);
}

export interface RunWorkflowFromAssistantProps
  extends Omit<RunWorkflowDialogProps, "workflowFilter" | "titleOverride"> {
  /**
   * The Assistant workspace root for the cosmetic out-dir preview.
   * Optional — callers that don't have the path resolved client-side
   * can omit it.
   */
  assistantWorkspaceRoot?: string;
  /**
   * Optional workflow id used for the out-dir preview. Defaults to a
   * ``<workflow_id>`` placeholder; the underlying dispatcher will
   * recompute the real path at run time.
   */
  workflowIdHint?: string;
  /**
   * Optional run id used for the out-dir preview. Defaults to a
   * ``<run_id>`` placeholder.
   */
  runIdHint?: string;
}

/**
 * Build the cosmetic out-dir preview path. Mirrors the backend
 * ``run_dir_for(...)`` semantics: the run dispatcher composes
 * ``<assistant_workspace>/assistant/outputs/<batch_id>/<workflow_id>/<run_id>``
 * so the UI hint is informational.
 *
 * Exported for direct unit testing.
 */
export function buildAssistantOutDirHint(args: {
  assistantWorkspaceRoot?: string;
  batchId: string;
  workflowId?: string;
  runId?: string;
}): string {
  const root = args.assistantWorkspaceRoot ?? "<assistant_workspace>";
  const workflowId = args.workflowId ?? "<workflow_id>";
  const runId = args.runId ?? "<run_id>";
  // Use forward slashes throughout: this is a cosmetic preview, not a
  // platform-specific filesystem path. The backend uses Path which
  // handles separators per OS.
  return `${root}/assistant/outputs/${args.batchId}/${workflowId}/${runId}`;
}

export function RunWorkflowFromAssistant(
  props: RunWorkflowFromAssistantProps,
) {
  const { assistantWorkspaceRoot, workflowIdHint, runIdHint, ...rest } = props;
  const outDirHint = buildAssistantOutDirHint({
    assistantWorkspaceRoot,
    batchId: rest.batch.id,
    workflowId: workflowIdHint,
    runId: runIdHint,
  });
  return (
    <RunWorkflowDialog
      {...rest}
      workflowFilter={isAssistantCategory}
      titleOverride={`Run Assistant workflow against ${rest.batch.title}`}
      outDirHint={outDirHint}
    />
  );
}

export default RunWorkflowFromAssistant;
