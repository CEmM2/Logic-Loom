import type { Batch } from "../../api/picker";
import { cn } from "../../lib/utils";

interface Props {
  batches: Batch[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function BatchList({ batches, selectedId, onSelect }: Props) {
  if (batches.length === 0) {
    return (
      <div className="p-3 text-xs text-muted-foreground space-y-1">
        <p>No batches found.</p>
        <p>Batches are read from the AKMS workspace&apos;s batches.json.</p>
        <p className="mt-2">Create one under AKMS → Batches.</p>
      </div>
    );
  }

  let lastRound: number | undefined;

  return (
    <ul className="flex flex-col overflow-y-auto p-2">
      {batches.map((b) => {
        const showRoundHeader = b.round !== undefined && b.round !== lastRound;
        if (b.round !== undefined) lastRound = b.round;
        return (
          <li key={b.id}>
            {showRoundHeader && b.round_title && (
              <div className="mt-2 px-2 pb-1 text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
                R{b.round}: {b.round_title}
              </div>
            )}
            <button
              onClick={() => onSelect(b.id)}
              className={cn(
                "flex w-full flex-col rounded px-2 py-1.5 text-left text-sm hover:bg-muted",
                selectedId === b.id && "bg-muted",
              )}
            >
              <span className="font-medium truncate text-xs">{b.id} — {b.title}</span>
              <span className="text-[10px] text-muted-foreground">
                {b.n_assigned} assigned
                {b.has_notebook && " · notebook"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
