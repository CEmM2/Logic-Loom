/**
 * Modal dialog for running akms_node_promote.py on the folder containing
 * the currently-viewed node.  Shows dry-run output first; the Execute
 * button actually moves files.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPromoteStream } from "../../api/nodes";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

interface Props {
  /** Absolute path to the file being viewed — its parent dir is the source. */
  filePath: string;
  onClose: () => void;
}

export function PromoteDialog({ filePath, onClose }: Props) {
  const sourceFolder = filePath.split("/").slice(0, -1).join("/");
  const [promote, setPromote] = useState(false);
  const [vaultOverride, setVaultOverride] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const cleanup = () => {
    esRef.current?.close();
    esRef.current = null;
  };
  useEffect(() => () => cleanup(), []);

  // Auto-scroll log to bottom on new lines.
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines]);

  const run = useCallback(
    async (execute: boolean) => {
      cleanup();
      setLines([]);
      setExitCode(null);
      setRunning(true);

      const es = await createPromoteStream({
        source: sourceFolder,
        vault: vaultOverride || undefined,
        promote,
        execute,
      });
      esRef.current = es;

      es.onmessage = (e: MessageEvent) => {
        setLines((prev) => [...prev, e.data as string]);
      };
      es.addEventListener("done", (e) => {
        setExitCode(parseInt((e as MessageEvent).data, 10));
        setRunning(false);
        cleanup();
      });
      es.addEventListener("error", () => {
        setLines((prev) => [...prev, "[connection error]"]);
        setRunning(false);
        cleanup();
      });
      es.onerror = () => {
        // SSE onerror fires on normal stream end in some browsers — only add
        // a message if we haven't received a done event yet.
        if (running) {
          setLines((prev) => [...prev, "[stream closed]"]);
          setRunning(false);
          cleanup();
        }
      };
    },
    [sourceFolder, promote, vaultOverride, running],
  );

  const browseVault = async () => {
    try {
      const sel = await openDialog({ directory: true, title: "Choose vault directory" });
      if (typeof sel === "string") setVaultOverride(sel);
    } catch {
      /* Tauri not available in dev preview */
    }
  };

  const isDone = exitCode !== null;
  const succeeded = exitCode === 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="flex w-[52rem] max-h-[85vh] flex-col rounded border border-border bg-background shadow-lg">
        {/* ── Header ─────────────────────────────────────────── */}
        <div className="border-b border-border p-3">
          <h3 className="text-base font-semibold">Promote / Move Nodes</h3>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
            {sourceFolder}
          </p>
        </div>

        {/* ── Options ────────────────────────────────────────── */}
        <div className="flex flex-col gap-3 border-b border-border p-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={promote}
              onChange={(e) => setPromote(e.target.checked)}
            />
            Promote <code className="text-xs">tentative → established</code>
          </label>
          <div className="flex items-center gap-3">
            <label className="w-32 shrink-0 text-sm">Vault override</label>
            <input
              type="text"
              value={vaultOverride}
              onChange={(e) => setVaultOverride(e.target.value)}
              placeholder="(use configured vault)"
              className="flex-1 rounded border border-border bg-background px-2 py-1 font-mono text-xs"
            />
            <button
              type="button"
              onClick={browseVault}
              className="shrink-0 rounded border border-border px-2 py-1 text-xs hover:bg-muted"
            >
              Browse…
            </button>
          </div>
        </div>

        {/* ── Log output ─────────────────────────────────────── */}
        <div
          ref={logRef}
          className="min-h-[10rem] flex-1 overflow-y-auto bg-muted/20 p-3 font-mono text-xs"
        >
          {lines.length === 0 && !running && !isDone ? (
            <span className="text-muted-foreground">
              Click <strong>Dry Run</strong> to preview what would be moved, then{" "}
              <strong>Execute</strong> to move files.
            </span>
          ) : (
            lines.map((line, i) => (
              <div key={i} className="whitespace-pre-wrap py-px leading-relaxed">
                {line || " "}
              </div>
            ))
          )}
        </div>

        {/* ── Footer ─────────────────────────────────────────── */}
        <div className="flex items-center justify-between border-t border-border p-3">
          <div className="text-xs">
            {isDone ? (
              <span className={succeeded ? "text-emerald-600" : "text-red-600"}>
                Exited {exitCode}
              </span>
            ) : running ? (
              <span className="text-muted-foreground">Running…</span>
            ) : null}
          </div>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="rounded border border-border px-3 py-1 text-sm hover:bg-muted"
            >
              Close
            </button>
            <button
              disabled={running}
              onClick={() => run(false)}
              className="rounded border border-border px-3 py-1 text-sm hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            >
              {running ? "Running…" : "Dry Run"}
            </button>
            <button
              disabled={running}
              onClick={() => run(true)}
              className="rounded bg-indigo-600 px-3 py-1 text-sm text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Execute
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
