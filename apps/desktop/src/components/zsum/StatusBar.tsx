/**
 * Status bar — three regions, mirrors view.ts renderStatusBar():
 *   1. Brand block: "ZS" mark + "Zotero Summarizer" + config basename
 *   2. Stat cards: Collections / Papers / Cached / Pending / Degraded /
 *      No PDF / Digests OK / Stale / Missing — each shows value-over-label
 *   3. Live box: BBT-live dot + "indexed HH:MM:SS" timestamp
 */

import { useQuery } from "@tanstack/react-query";

import { getBbtStatus, getZsumIndex, type LibraryStats } from "../../api/zsum";
import { cn } from "../../lib/utils";

const STAT_KEYS: { key: keyof LibraryStats; label: string; tone?: "ok" | "warn" | "muted" | "bad" }[] = [
  { key: "total_collections", label: "Collections" },
  { key: "total_papers", label: "Papers" },
  { key: "cards_cached", label: "Cached", tone: "ok" },
  { key: "cards_pending", label: "Pending", tone: "muted" },
  { key: "cards_degraded", label: "Degraded", tone: "warn" },
  { key: "cards_no_pdf", label: "No PDF", tone: "muted" },
  { key: "digests_ok", label: "Digests OK", tone: "ok" },
  { key: "digests_stale", label: "Stale", tone: "warn" },
  { key: "digests_missing", label: "Missing", tone: "bad" },
];

const TONE_CLASS = {
  ok: "text-emerald-600 dark:text-emerald-400",
  warn: "text-amber-600 dark:text-amber-400",
  muted: "text-muted-foreground",
  bad: "text-red-600 dark:text-red-400",
};

export function StatusBar({ configBasename }: { configBasename: string }) {
  const idx = useQuery({ queryKey: ["zsum", "index"], queryFn: getZsumIndex, refetchInterval: 30_000 });
  const bbt = useQuery({ queryKey: ["zsum", "bbt-status"], queryFn: getBbtStatus, refetchInterval: 60_000 });

  const stats = idx.data?.kind === "ok" ? idx.data.stats : null;
  const indexedAt = idx.dataUpdatedAt ? new Date(idx.dataUpdatedAt) : null;

  return (
    <div className="flex items-stretch gap-3 border-b border-border px-3 py-2">
      {/* Brand block */}
      <div className="flex items-center gap-2 pr-3">
        <div className="flex h-9 w-9 items-center justify-center rounded bg-foreground font-bold text-background text-sm">
          ZS
        </div>
        <div className="leading-tight">
          <div className="text-xs font-semibold">Zotero Summarizer</div>
          <div className="font-mono text-[10px] text-muted-foreground">{configBasename}</div>
        </div>
      </div>

      {/* Stat cards */}
      <div className="flex flex-1 flex-wrap items-center gap-1.5">
        {STAT_KEYS.map(({ key, label, tone }) => {
          const v = stats ? stats[key] : "—";
          return (
            <div
              key={key}
              className="flex min-w-[68px] flex-col items-center justify-center rounded border border-border bg-background/40 px-2 py-1"
            >
              <div className={cn("font-mono text-sm font-semibold", tone && TONE_CLASS[tone])}>{v}</div>
              <div className="text-[10px] text-muted-foreground">{label}</div>
            </div>
          );
        })}
      </div>

      {/* BBT-live box */}
      <div className="flex flex-col items-end justify-center gap-0.5 pl-2 text-xs">
        <div
          className={cn(
            "flex items-center gap-1.5 rounded border border-border bg-background/40 px-2 py-1",
            bbt.data?.is_live ? "text-emerald-600" : "text-amber-600",
          )}
          title={bbt.data?.bbt_json ?? "BBT JSON path not configured"}
        >
          <span
            className={cn(
              "h-2 w-2 rounded-full",
              bbt.data?.is_live ? "bg-emerald-500 animate-pulse" : "bg-amber-500",
            )}
          />
          <span>{bbt.data?.is_live ? "BBT live" : "BBT stale"}</span>
        </div>
        {indexedAt && (
          <div className="font-mono text-[10px] text-muted-foreground">
            indexed {indexedAt.toTimeString().slice(0, 8)}
          </div>
        )}
      </div>
    </div>
  );
}
