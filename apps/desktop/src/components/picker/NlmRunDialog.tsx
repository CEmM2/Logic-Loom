/**
 * Dialog that launches an NLM task (generate / validate) and streams
 * its log output in real time via SSE.  Reuses the same LogStream
 * component used by the Pipeline page.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { LogEvent, RunRecord } from "../../api/nlm";
import { cancelNlmRun, streamNlmRun } from "../../api/nlm";
import { LogStream } from "../LogStream";

type NlmAction = "generate" | "generate-nlm" | "validate";

interface Props {
  open: boolean;
  action: NlmAction;
  batchId: string;
  batchTitle: string;
  runRecord: RunRecord | null;
  onClose: () => void;
}

const LABELS: Record<NlmAction, string> = {
  generate: "Generate Nodes (Claude)",
  "generate-nlm": "Generate Nodes (NLM direct)",
  validate: "Validate Nodes",
};

export function NlmRunDialog({ open, action, batchId, batchTitle, runRecord, onClose }: Props) {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [finalRecord, setFinalRecord] = useState<RunRecord | null>(null);
  const closeRef = useRef<(() => void) | null>(null);

  const handleEvent = useCallback((e: LogEvent) => {
    setEvents((prev) => [...prev, e]);
  }, []);

  const handleDone = useCallback((rec: RunRecord | null) => {
    setFinalRecord(rec);
  }, []);

  useEffect(() => {
    if (!open || !runRecord) return;
    setEvents([]);
    setFinalRecord(null);

    let cancelled = false;
    streamNlmRun(runRecord.id, handleEvent, handleDone).then((closeFn) => {
      if (cancelled) {
        closeFn();
      } else {
        closeRef.current = closeFn;
      }
    });
    return () => {
      cancelled = true;
      closeRef.current?.();
      closeRef.current = null;
    };
  }, [open, runRecord?.id, handleEvent, handleDone]);

  const onCancel = async () => {
    if (runRecord && !finalRecord) {
      try {
        await cancelNlmRun(runRecord.id);
      } catch {
        /* already done */
      }
    }
  };

  if (!open) return null;

  const isDone = !!finalRecord;
  const succeeded = finalRecord?.status === "succeeded";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="flex w-[48rem] max-h-[80vh] flex-col rounded border border-border bg-background shadow-lg">
        <div className="border-b border-border p-3">
          <h3 className="text-base font-semibold">{LABELS[action]}</h3>
          <p className="text-xs text-muted-foreground">
            {batchTitle} · {batchId}
          </p>
        </div>

        <div className="flex-1 min-h-[16rem] max-h-[50vh] overflow-hidden">
          {events.length === 0 && !isDone ? (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              Starting…
            </div>
          ) : (
            <LogStream events={events} />
          )}
        </div>

        <div className="flex items-center justify-between border-t border-border p-3">
          <div className="text-xs">
            {isDone ? (
              <span className={succeeded ? "text-emerald-600" : "text-red-600"}>
                {finalRecord.status} (exit {finalRecord.exit_code})
              </span>
            ) : (
              <span className="text-muted-foreground">Running…</span>
            )}
          </div>
          <div className="flex gap-2">
            {!isDone && (
              <button
                onClick={onCancel}
                className="rounded border border-border px-3 py-1 text-sm hover:bg-muted"
              >
                Cancel
              </button>
            )}
            <button
              onClick={() => {
                closeRef.current?.();
                closeRef.current = null;
                onClose();
              }}
              className="rounded bg-foreground px-3 py-1 text-sm text-background hover:opacity-90"
            >
              {isDone ? "Close" : "Background"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
