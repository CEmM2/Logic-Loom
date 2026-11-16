/**
 * Selectable list of Assistant batches.
 *
 * Mirrors `components/batches/BatchesPage.tsx`'s left-pane idiom but
 * scoped to a single component so the Assistant page can compose it
 * alongside the sources / notebook / workflow panels.
 */
import type { AssistantBatch } from "../../../api/assistantBatches";

export interface BatchListProps {
  batches: AssistantBatch[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading?: boolean;
}

export function BatchList({
  batches,
  selectedId,
  onSelect,
  isLoading = false,
}: BatchListProps) {
  if (isLoading) {
    return (
      <div className="p-4 text-sm text-muted-foreground" data-testid="batch-list-loading">
        Loading batches…
      </div>
    );
  }
  if (batches.length === 0) {
    return (
      <div
        className="p-4 text-sm text-muted-foreground"
        data-testid="batch-list-empty"
      >
        No batches yet. Use “New batch” above to create one.
      </div>
    );
  }
  return (
    <ul className="divide-y" data-testid="batch-list">
      {batches.map((b) => {
        const active = b.id === selectedId;
        return (
          <li key={b.id}>
            <button
              type="button"
              data-testid={`batch-list-item-${b.id}`}
              onClick={() => onSelect(b.id)}
              className={
                "flex w-full flex-col items-start gap-1 px-4 py-3 text-left hover:bg-muted/60 " +
                (active ? "bg-muted" : "")
              }
            >
              <span className="text-sm font-medium">{b.title}</span>
              <span className="text-xs text-muted-foreground">
                {b.id} · {b.status} · {b.sources.length} source
                {b.sources.length === 1 ? "" : "s"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
