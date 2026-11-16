/**
 * Confirmation + progress dialog for `nlm` sync.
 *
 * The picker endpoint blocks while ``nlm`` runs (it shells out to
 * `nlm notebook create` then per-PDF `nlm source add`). v0.1 surfaces a
 * spinner and warns the user if the call hasn't returned within 120 s
 * — likely auth required.
 */

import { useEffect, useRef, useState } from "react";

import { createNotebook } from "../../api/picker";

interface Props {
  open: boolean;
  batchId: string;
  batchTitle: string;
  paperCount: number;
  onClose: () => void;
  onSuccess: (notebookId: string) => void;
}

const TIMEOUT_WARN_MS = 120_000;

export function SyncNlmDialog({
  open,
  batchId,
  batchTitle,
  paperCount,
  onClose,
  onSuccess,
}: Props) {
  const [phase, setPhase] = useState<"confirm" | "running" | "done" | "error">("confirm");
  const [upload, setUpload] = useState(true);
  const [wait, setWait] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [warnAuth, setWarnAuth] = useState(false);
  const [notebookId, setNotebookId] = useState<string | null>(null);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    if (!open) {
      setPhase("confirm");
      setError(null);
      setWarnAuth(false);
      setNotebookId(null);
    }
  }, [open]);

  const onConfirm = async () => {
    setPhase("running");
    setError(null);
    setWarnAuth(false);
    timeoutRef.current = window.setTimeout(() => setWarnAuth(true), TIMEOUT_WARN_MS);
    try {
      const res = (await createNotebook(batchId, { upload, wait })) as {
        notebook_id?: string | null;
      };
      const id = res.notebook_id ?? null;
      setNotebookId(id);
      setPhase("done");
      if (id) onSuccess(id);
    } catch (e) {
      setError((e as Error).message);
      setPhase("error");
    } finally {
      if (timeoutRef.current != null) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    }
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="w-[28rem] rounded border border-border bg-background p-4 shadow-lg">
        <h3 className="text-base font-semibold">Sync to NotebookLM</h3>
        <p className="mt-1 text-sm text-muted-foreground">{batchTitle}</p>
        <p className="text-xs text-muted-foreground">
          {paperCount} paper{paperCount === 1 ? "" : "s"} · {upload ? "PDFs uploaded" : "metadata only"}
        </p>

        {phase === "confirm" && (
          <>
            <div className="mt-3 flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={upload} onChange={(e) => setUpload(e.target.checked)} />
                Upload PDFs to the notebook
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={wait} onChange={(e) => setWait(e.target.checked)} />
                Wait for each upload to finish (slower, but more reliable)
              </label>
            </div>
            <div className="mt-4 flex justify-end gap-2 text-sm">
              <button onClick={onClose} className="rounded border border-border px-3 py-1 hover:bg-muted">
                Cancel
              </button>
              <button
                onClick={onConfirm}
                className="rounded bg-foreground px-3 py-1 text-background hover:opacity-90"
              >
                Run
              </button>
            </div>
          </>
        )}

        {phase === "running" && (
          <div className="mt-4 text-sm">
            <p>Running <span className="font-mono">nlm</span>…</p>
            {warnAuth && (
              <p className="mt-2 text-xs text-amber-700">
                This is taking longer than expected. <span className="font-mono">nlm</span> may be
                waiting for auth. Run <span className="font-mono">nlm login</span> in a terminal and
                retry.
              </p>
            )}
          </div>
        )}

        {phase === "done" && (
          <div className="mt-4 text-sm">
            <p className="text-emerald-700">Notebook created.</p>
            {notebookId && (
              <p className="mt-1 text-xs">
                notebook_id: <span className="font-mono">{notebookId}</span>
              </p>
            )}
            <div className="mt-4 flex justify-end">
              <button
                onClick={onClose}
                className="rounded bg-foreground px-3 py-1 text-background hover:opacity-90"
              >
                Close
              </button>
            </div>
          </div>
        )}

        {phase === "error" && (
          <div className="mt-4 text-sm">
            <p className="text-red-600">Sync failed.</p>
            <pre className="mt-2 max-h-40 overflow-y-auto rounded bg-red-50 p-2 text-xs text-red-900">
              {error}
            </pre>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={onClose} className="rounded border border-border px-3 py-1 hover:bg-muted">
                Close
              </button>
              <button
                onClick={() => setPhase("confirm")}
                className="rounded bg-foreground px-3 py-1 text-background hover:opacity-90"
              >
                Retry
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
