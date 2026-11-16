/**
 * CreateBatchFromResultsDialog.
 *
 * Calls `createAssistantBatch({ title })` then `addAssistantBatchSources`
 * with every discovered paper from the current results.
 */
import { useState } from "react";
import {
  addAssistantBatchSources,
  createAssistantBatch,
  type AssistantBatch,
} from "../../../api/assistantBatches";
import {
  discoveredPaperToSourceItem,
  type DiscoveredPaper,
} from "../../../api/assistantDiscover";

export interface CreateBatchFromResultsDialogProps {
  isOpen: boolean;
  papers: DiscoveredPaper[];
  defaultTitle?: string;
  onClose: () => void;
  onCreated: (batch: AssistantBatch) => void;
}

export function CreateBatchFromResultsDialog({
  isOpen,
  papers,
  defaultTitle = "Discovery batch",
  onClose,
  onCreated,
}: CreateBatchFromResultsDialogProps) {
  const [title, setTitle] = useState(defaultTitle);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  async function handleCreate() {
    if (!title.trim()) return;
    setIsSaving(true);
    setError(null);
    try {
      const newBatch = await createAssistantBatch({ title: title.trim() });
      const items = papers.map(discoveredPaperToSourceItem);
      const populated = items.length
        ? await addAssistantBatchSources(newBatch.id, items)
        : newBatch;
      onCreated(populated);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div
      data-testid="create-batch-from-results-dialog"
      role="dialog"
      aria-label="Create batch from results"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
    >
      <div className="w-[28rem] rounded-md border bg-background p-4 shadow-xl">
        <h2 className="mb-2 text-lg font-semibold">Create batch from results</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          {papers.length} paper{papers.length === 1 ? "" : "s"} will be added
          to the new batch.
        </p>
        <label className="mb-3 block text-sm">
          Title
          <input
            data-testid="create-batch-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mt-1 w-full rounded border bg-background px-2 py-1"
          />
        </label>
        {error ? (
          <p className="mb-2 text-sm text-red-600">{error}</p>
        ) : null}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="rounded border px-3 py-1 text-sm"
            onClick={onClose}
            disabled={isSaving}
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="create-batch-confirm"
            className="rounded border bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            onClick={handleCreate}
            disabled={isSaving || !title.trim()}
          >
            {isSaving ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
