import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef } from "react";

import type { LogEvent } from "../api/zsum";
import { cn } from "../lib/utils";

interface Props {
  events: LogEvent[];
  autoScroll?: boolean;
}

const levelColor: Record<string, string> = {
  DEBUG: "text-zinc-400",
  INFO: "text-zinc-200",
  WARNING: "text-amber-400",
  ERROR: "text-red-400",
  CRITICAL: "text-red-300 font-semibold",
};

export function LogStream({ events, autoScroll = true }: Props) {
  const parentRef = useRef<HTMLDivElement | null>(null);
  const rowVirt = useVirtualizer({
    count: events.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 18,
    overscan: 25,
  });

  useEffect(() => {
    if (!autoScroll) return;
    const el = parentRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [events.length, autoScroll]);

  return (
    <div
      ref={parentRef}
      className="flex-1 overflow-y-auto bg-black/90 font-mono text-xs text-zinc-200"
    >
      <div style={{ height: `${rowVirt.getTotalSize()}px`, position: "relative" }}>
        {rowVirt.getVirtualItems().map((vr) => {
          const evt = events[vr.index];
          return (
            <div
              key={vr.key}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${vr.start}px)`,
              }}
              className={cn(
                "whitespace-pre px-3 py-0.5",
                levelColor[evt.level] ?? "text-zinc-200",
              )}
            >
              {evt.line}
            </div>
          );
        })}
      </div>
    </div>
  );
}
