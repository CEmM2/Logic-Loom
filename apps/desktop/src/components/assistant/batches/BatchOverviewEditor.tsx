/**
 * Overview tab — title / description / status / tags / purpose.
 *
 * Edits are local-state until "Save" calls onSave. The parent owns the
 * PATCH mutation and re-fetches on success.
 */
import { useEffect, useState } from "react";

import type {
  AssistantBatch,
  PatchAssistantBatchBody,
  SourceBatchStatus,
} from "../../../api/assistantBatches";

export interface BatchOverviewEditorProps {
  batch: AssistantBatch;
  onSave: (patch: PatchAssistantBatchBody) => void;
  saving?: boolean;
}

export function BatchOverviewEditor({
  batch,
  onSave,
  saving = false,
}: BatchOverviewEditorProps) {
  const [title, setTitle] = useState(batch.title);
  const [description, setDescription] = useState(batch.description);
  const [status, setStatus] = useState<SourceBatchStatus>(batch.status);
  const [tagsRaw, setTagsRaw] = useState(batch.tags.join(", "));
  const [purpose, setPurpose] = useState(batch.purpose ?? "");

  // Reset local state if the underlying batch changes (e.g. user
  // selects a different batch in the list).
  useEffect(() => {
    setTitle(batch.title);
    setDescription(batch.description);
    setStatus(batch.status);
    setTagsRaw(batch.tags.join(", "));
    setPurpose(batch.purpose ?? "");
  }, [batch.id]);

  const dirty =
    title !== batch.title ||
    description !== batch.description ||
    status !== batch.status ||
    tagsRaw !== batch.tags.join(", ") ||
    (purpose || "") !== (batch.purpose ?? "");

  return (
    <form
      data-testid="batch-overview-editor"
      onSubmit={(e) => {
        e.preventDefault();
        const tags = tagsRaw
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean);
        onSave({
          title,
          description,
          status,
          tags,
          purpose: purpose ? purpose : null,
        });
      }}
      className="flex flex-col gap-3"
    >
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Title
        </span>
        <input
          data-testid="overview-title"
          className="rounded border px-2 py-1 text-sm"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Description
        </span>
        <textarea
          data-testid="overview-description"
          className="rounded border px-2 py-1 text-sm"
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Status
        </span>
        <select
          data-testid="overview-status"
          className="rounded border px-2 py-1 text-sm"
          value={status}
          onChange={(e) => setStatus(e.target.value as SourceBatchStatus)}
        >
          <option value="draft">draft</option>
          <option value="active">active</option>
          <option value="archived">archived</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Tags (comma-separated)
        </span>
        <input
          data-testid="overview-tags"
          className="rounded border px-2 py-1 text-sm"
          value={tagsRaw}
          onChange={(e) => setTagsRaw(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Purpose
        </span>
        <input
          data-testid="overview-purpose"
          className="rounded border px-2 py-1 text-sm"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
        />
      </label>
      <div className="flex justify-end">
        <button
          type="submit"
          data-testid="overview-save"
          disabled={!dirty || saving}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}
