/**
 * Collection tree — mirrors view.ts renderTree(): "All Collections" root
 * + nested children sorted by name, with a chevron toggle for each node
 * that has children, a digest-state dot, and a per-node item count.
 *
 * State (selected key, expanded set) is owned by the parent route so
 * selection survives across reloads.
 */

import type { CollectionIndex, ZsumIndex } from "../../api/zsum";
import { cn } from "../../lib/utils";

const DOT_CLASS = {
  ok: "bg-emerald-500",
  stale: "bg-amber-500",
  missing: "bg-muted-foreground/40",
};

export function CollectionTree({
  index,
  selectedKey,
  expanded,
  onSelect,
  onToggle,
}: {
  index: ZsumIndex;
  selectedKey: string;
  expanded: Set<string>;
  onSelect: (key: string) => void;
  onToggle: (key: string) => void;
}) {
  // Build parent → children map. A parent missing from the dict gets
  // its kids re-attached to root so orphaned subtrees still render.
  const childrenOf: Record<string, string[]> = {};
  for (const [key, col] of Object.entries(index.collections)) {
    const parent = col.parent && index.collections[col.parent] ? col.parent : "__root__";
    (childrenOf[parent] ??= []).push(key);
  }
  for (const arr of Object.values(childrenOf)) {
    arr.sort((a, b) => index.collections[a].name.localeCompare(index.collections[b].name));
  }

  const renderNode = (key: string, depth: number): React.ReactElement | null => {
    const col: CollectionIndex | undefined = index.collections[key];
    if (!col) return null;
    const kids = childrenOf[key] ?? [];
    const hasKids = kids.length > 0;
    const isExpanded = expanded.has(key);
    return (
      <div key={key}>
        <div
          onClick={() => onSelect(key)}
          className={cn(
            "flex cursor-pointer items-center gap-1 px-2 py-0.5 text-xs hover:bg-muted",
            selectedKey === key && "bg-muted font-medium",
          )}
          style={{ paddingLeft: 8 + depth * 14 }}
        >
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (hasKids) onToggle(key);
            }}
            className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground"
          >
            {hasKids ? (
              <span className="font-mono text-[11px] leading-none">
                {isExpanded ? "▼" : "▶"}
              </span>
            ) : null}
          </button>
          <span className={cn("h-2 w-2 shrink-0 rounded-full", DOT_CLASS[col.digest_state])} />
          <span className="flex-1 truncate">{col.name}</span>
          <span className="font-mono text-[10px] text-muted-foreground">{col.item_count}</span>
        </div>
        {isExpanded && kids.map((k) => renderNode(k, depth + 1))}
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b border-border px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Collections
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {/* "All Collections" root row */}
        <div
          onClick={() => onSelect("root")}
          className={cn(
            "flex cursor-pointer items-center gap-1 px-2 py-0.5 text-xs hover:bg-muted",
            selectedKey === "root" && "bg-muted font-medium",
          )}
        >
          <span className="h-4 w-4 shrink-0" />
          <span className="h-2 w-2 shrink-0 rounded-full bg-muted-foreground/40" />
          <span className="flex-1 truncate">All Collections</span>
          <span className="font-mono text-[10px] text-muted-foreground">
            {Object.keys(index.items).length}
          </span>
        </div>
        {(childrenOf["__root__"] ?? []).map((k) => renderNode(k, 0))}
      </div>
      <div className="shrink-0 border-t border-border px-2 py-1.5 text-[10px] text-muted-foreground">
        <span className="mr-2">
          <span className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-500" /> ok
        </span>
        <span className="mr-2">
          <span className="mr-1 inline-block h-2 w-2 rounded-full bg-amber-500" /> stale
        </span>
        <span>
          <span className="mr-1 inline-block h-2 w-2 rounded-full bg-muted-foreground/40" /> missing
        </span>
      </div>
    </div>
  );
}
