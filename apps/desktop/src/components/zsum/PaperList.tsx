/**
 * Paper list — mirrors view.ts renderPaperList()/renderPaperRows():
 *   - header with collection title, citekey filter, sort toggle, regen button
 *   - column header row (Citekey · Type · Year · Venue · Citations · Card)
 *   - rows split into "Review Papers" + "Papers" sections when reviews exist
 */

import { useState } from "react";

import type { ItemIndex, ZsumIndex } from "../../api/zsum";
import { cn } from "../../lib/utils";

const REVIEW_TYPES = new Set(["review", "survey", "meta-analysis"]);
const isReview = (p: ItemIndex) => !!p.paper_type && REVIEW_TYPES.has(p.paper_type);

const TYPE_PILL: Record<string, string> = {
  review: "bg-purple-500/10 text-purple-700 dark:text-purple-300",
  survey: "bg-purple-500/10 text-purple-700 dark:text-purple-300",
  "meta-analysis": "bg-purple-500/10 text-purple-700 dark:text-purple-300",
  tutorial: "bg-blue-500/10 text-blue-700 dark:text-blue-300",
};

const CARD_BADGE: Record<ItemIndex["card_state"], string> = {
  cached: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  pending: "bg-muted text-muted-foreground",
  degraded: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  no_pdf: "bg-muted text-muted-foreground italic",
};

const CARD_ICON: Record<ItemIndex["card_state"], string> = {
  cached: "✓",
  pending: "○",
  degraded: "⚠",
  no_pdf: "—",
};

export function PaperList({
  index,
  collectionKey,
  onOpenPaper,
  onRegenDigest,
}: {
  index: ZsumIndex;
  collectionKey: string;
  onOpenPaper?: (citekey: string) => void;
  onRegenDigest?: (collectionKey: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const [sortByCitations, setSortByCitations] = useState(false);

  const allPapers =
    collectionKey === "root"
      ? Object.values(index.items)
      : Object.values(index.items).filter((p) => p.collections.includes(collectionKey));

  let papers = filter.trim()
    ? allPapers.filter((p) => p.citekey.toLowerCase().includes(filter.trim().toLowerCase()))
    : [...allPapers];
  papers.sort((a, b) =>
    sortByCitations
      ? (b.citation_count ?? -1) - (a.citation_count ?? -1)
      : (b.year || 0) - (a.year || 0),
  );

  const reviews = papers.filter(isReview);
  const research = papers.filter((p) => !isReview(p));

  const counts = papers.reduce(
    (a, p) => {
      a[p.card_state]++;
      return a;
    },
    { cached: 0, pending: 0, degraded: 0, no_pdf: 0 } as Record<ItemIndex["card_state"], number>,
  );

  const title =
    collectionKey === "root" ? "All Collections" : index.collections[collectionKey]?.path ?? "—";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-border p-2">
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <div className="text-sm font-semibold">{title}</div>
            <div className="text-[11px] text-muted-foreground">
              {papers.length} papers · {counts.cached} cached · {counts.pending} pending
              {counts.degraded ? ` · ${counts.degraded} degraded` : ""}
              {filter.trim() && papers.length !== allPapers.length
                ? ` · showing ${papers.length} of ${allPapers.length}`
                : ""}
            </div>
          </div>
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search citekey"
            className="rounded border border-border bg-background px-2 py-1 text-xs"
          />
          <button
            onClick={() => setSortByCitations((v) => !v)}
            className="rounded border border-border px-2 py-1 font-mono text-xs text-muted-foreground hover:bg-muted"
            title={sortByCitations ? "Sort by year" : "Sort by citations"}
          >
            {sortByCitations ? "📅" : "📈"}
          </button>
          <button
            onClick={() => onRegenDigest?.(collectionKey)}
            disabled={collectionKey === "root"}
            className="rounded border border-border px-2 py-1 font-mono text-xs text-muted-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
            title="Regenerate digest for this collection"
          >
            ⟳
          </button>
        </div>
      </div>

      {/* Column header */}
      <div className="grid shrink-0 grid-cols-[24px_1.5fr_80px_60px_1.2fr_70px_90px] items-center gap-2 border-b border-border bg-muted/30 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        <span />
        <span>Citekey</span>
        <span>Type</span>
        <span>Year</span>
        <span>Venue</span>
        <span>Citations</span>
        <span>Card</span>
      </div>

      {/* Rows */}
      <div className="flex-1 overflow-y-auto">
        {reviews.length > 0 && <SectionHeader text={`Review Papers (${reviews.length})`} />}
        {reviews.map((p) => (
          <Row key={p.citekey} p={p} onOpen={onOpenPaper} />
        ))}
        {reviews.length > 0 && research.length > 0 && (
          <SectionHeader text={`Papers (${research.length})`} />
        )}
        {research.map((p) => (
          <Row key={p.citekey} p={p} onOpen={onOpenPaper} />
        ))}
        {!papers.length && filter.trim() && (
          <div className="p-4 text-center text-xs text-muted-foreground">
            No papers match "{filter}"
          </div>
        )}
        {!papers.length && !filter.trim() && (
          <div className="p-4 text-center text-xs text-muted-foreground">
            No papers in this collection.
          </div>
        )}
      </div>
    </div>
  );
}

function SectionHeader({ text }: { text: string }) {
  return (
    <div className="border-b border-border bg-muted/40 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
      {text}
    </div>
  );
}

const STATUS_COLOR: Record<ItemIndex["card_state"], string> = {
  cached: "text-emerald-600",
  pending: "text-muted-foreground",
  degraded: "text-amber-600",
  no_pdf: "text-muted-foreground",
};

function Row({ p, onOpen }: { p: ItemIndex; onOpen?: (citekey: string) => void }) {
  return (
    <div
      onClick={() => onOpen?.(p.citekey)}
      className="grid cursor-pointer grid-cols-[24px_1.5fr_80px_60px_1.2fr_70px_90px] items-center gap-2 border-b border-border/30 px-2 py-1 text-xs hover:bg-muted/40"
    >
      <span className={cn("text-center font-mono", STATUS_COLOR[p.card_state])}>
        {CARD_ICON[p.card_state]}
      </span>
      <span className="truncate font-mono text-[11px]">{p.citekey}</span>
      <span>
        {p.paper_type && (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] font-medium",
              TYPE_PILL[p.paper_type] ?? "bg-muted text-muted-foreground",
            )}
          >
            {p.paper_type}
          </span>
        )}
      </span>
      <span className="font-mono text-[11px] text-muted-foreground">{p.year || ""}</span>
      <span className="truncate text-[11px] text-muted-foreground">{p.venue}</span>
      <span className="font-mono text-[11px] text-muted-foreground">
        {typeof p.citation_count === "number" ? p.citation_count : "—"}
      </span>
      <span>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-medium",
            CARD_BADGE[p.card_state],
          )}
        >
          {p.card_state.replace("_", " ")}
        </span>
      </span>
    </div>
  );
}
