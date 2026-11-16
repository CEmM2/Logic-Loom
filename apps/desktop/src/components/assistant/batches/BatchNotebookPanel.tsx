/**
 * Notebook attachment panel — single input for NotebookLM notebook id.
 *
 * This panel only stores the id; the NotebookLM client consumes it
 * downstream. PATCH on save.
 */
import { useEffect, useState } from "react";

import type { AssistantBatch } from "../../../api/assistantBatches";

export interface BatchNotebookPanelProps {
  batch: AssistantBatch;
  onSave: (notebookId: string | null) => void;
  saving?: boolean;
}

export function BatchNotebookPanel({
  batch,
  onSave,
  saving = false,
}: BatchNotebookPanelProps) {
  const [notebookId, setNotebookId] = useState(batch.notebook_id ?? "");
  useEffect(() => {
    setNotebookId(batch.notebook_id ?? "");
  }, [batch.id]);

  const dirty = (notebookId || null) !== (batch.notebook_id ?? null);

  return (
    <div data-testid="batch-notebook-panel" className="flex flex-col gap-2">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          NotebookLM notebook id
        </span>
        <input
          data-testid="notebook-id"
          value={notebookId}
          onChange={(e) => setNotebookId(e.target.value)}
          className="rounded border px-2 py-1 text-sm"
          placeholder="notebook-id-or-url"
        />
      </label>
      <div className="flex justify-end">
        <button
          type="button"
          data-testid="notebook-save"
          disabled={!dirty || saving}
          onClick={() => onSave(notebookId.trim() || null)}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          {saving ? "Saving…" : "Attach"}
        </button>
      </div>
    </div>
  );
}
