/**
 * AddToBatchDialog.
 *
 * Wraps `addAssistantBatchSources` on the typed Assistant Batches client. Lets the
 * user pick an existing batch and adds the selected DiscoveredPaper(s)
 * to it. Closes on success; surfaces errors inline.
 */
import { useEffect, useState } from "react";
import {
  addAssistantBatchSources,
  type AssistantBatch,
} from "../../../api/assistantBatches";
import {
  discoveredPaperToSourceItem,
  type DiscoveredPaper,
} from "../../../api/assistantDiscover";

export interface AddToBatchDialogProps {
  isOpen: boolean;
  papers: DiscoveredPaper[];
  batches: AssistantBatch[];
  onClose: () => void;
  onAdded: (batch: AssistantBatch) => void;
}

export function AddToBatchDialog({
  isOpen,
  papers,
  batches,
  onClose,
  onAdded,
}: AddToBatchDialogProps) {
  const [selectedBatchId, setSelectedBatchId] = useState<string>(
    batches[0]?.id ?? "",
  );
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Whenever the batches list becomes non-empty (or changes), keep the
  // selected id pointing at the first batch unless the user has already
  // picked one that still exists. Prevents the dialog from latching on
  // to "" forever when the page mounts before /api/assistant/batches
  // resolves.
  useEffect(() => {
    if (!isOpen) return;
    if (!selectedBatchId && batches.length > 0) {
      setSelectedBatchId(batches[0].id);
    } else if (
      selectedBatchId &&
      !batches.some((b) => b.id === selectedBatchId) &&
      batches.length > 0
    ) {
      setSelectedBatchId(batches[0].id);
    }
  }, [isOpen, batches, selectedBatchId]);

  if (!isOpen) return null;

  async function handleAdd() {
    if (!selectedBatchId) return;
    setIsSaving(true);
    setError(null);
    try {
      const items = papers.map(discoveredPaperToSourceItem);
      const updated = await addAssistantBatchSources(selectedBatchId, items);
      onAdded(updated);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div
      data-testid="add-to-batch-dialog"
      role="dialog"
      aria-label="Add to batch"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
    >
      <div className="w-[28rem] rounded-md border bg-background p-4 shadow-xl">
        <h2 className="mb-2 text-lg font-semibold">Add to batch</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Adding {papers.length} paper{papers.length === 1 ? "" : "s"} to the
          selected batch.
        </p>
        <label className="mb-3 block text-sm">
          Batch
          <select
            data-testid="add-to-batch-select"
            value={selectedBatchId}
            onChange={(e) => setSelectedBatchId(e.target.value)}
            className="mt-1 w-full rounded border bg-background px-2 py-1"
          >
            {batches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.title}
              </option>
            ))}
          </select>
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
            className="rounded border bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            onClick={handleAdd}
            disabled={isSaving || !selectedBatchId}
            data-testid="add-to-batch-confirm"
          >
            {isSaving ? "Adding…" : "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}
