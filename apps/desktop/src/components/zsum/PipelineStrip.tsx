/**
 * Pipeline progress strip — parses log lines as they arrive and shows
 * the current stage + completed-stage chips. Mirrors view.ts
 * parsePipelineLine() and the static STAGE_RE / DONE_RE / PROGRESS_RE
 * / FINAL_RE regexes.
 *
 * State derives purely from the events array, so the strip resets
 * cleanly whenever the parent passes a fresh log buffer.
 */

import { useMemo } from "react";

import type { LogEvent } from "../../api/zsum";
import { cn } from "../../lib/utils";

const STAGE_RE = /^(\d)\/(\d)\s+(\w+)\.{3}$/;
const DONE_RE = /^\s*✓\s+(.+)$/;
const PROGRESS_RE = /Progress:\s*(\d+)\/(\d+)\s+(\w+)/;
const FINAL_RE = /^Done!\s+(.+)$/;

interface ParsedStrip {
  done: string[]; // text labels for completed stages, in order
  current: { spinning: true; label: string } | null;
  final: string | null; // "Done! …" text once the final line is hit
}

function parse(events: readonly LogEvent[]): ParsedStrip {
  const out: ParsedStrip = { done: [], current: null, final: null };
  for (const e of events) {
    const text = e.line ?? "";
    const stage = text.match(STAGE_RE);
    if (stage) {
      out.current = { spinning: true, label: `${stage[1]}/${stage[2]} ${stage[3]}…` };
      continue;
    }
    const done = text.match(DONE_RE);
    if (done) {
      out.done.push(done[1]);
      out.current = null;
      continue;
    }
    const prog = text.match(PROGRESS_RE);
    if (prog) {
      out.current = { spinning: true, label: `${prog[3]}: ${prog[1]}/${prog[2]}` };
      continue;
    }
    const final = text.match(FINAL_RE);
    if (final) {
      out.current = null;
      out.final = final[1];
      continue;
    }
  }
  return out;
}

export function PipelineStrip({ events }: { events: readonly LogEvent[] }) {
  const parsed = useMemo(() => parse(events), [events]);
  if (!parsed.done.length && !parsed.current && !parsed.final) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-muted/20 px-3 py-1.5 text-xs">
      {parsed.done.map((label, i) => (
        <Chip key={i} tone="ok">
          ✓ {label}
        </Chip>
      ))}
      {parsed.current && (
        <Chip tone="running">
          <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-current" />
          {parsed.current.label}
        </Chip>
      )}
      {parsed.final && <Chip tone="ok">✓ {parsed.final}</Chip>}
    </div>
  );
}

function Chip({
  tone,
  children,
}: {
  tone: "ok" | "running";
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "flex items-center rounded border px-2 py-0.5 font-mono text-[11px]",
        tone === "ok"
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300",
      )}
    >
      {children}
    </span>
  );
}
