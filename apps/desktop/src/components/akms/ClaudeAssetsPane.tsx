/**
 * ClaudeAssetsPane — Settings → AKMS → Claude Assets.
 *
 * Surfaces the editable Claude asset tree (`/api/akms/claude-assets` routes).
 *  - Status pill (installed / uninstalled).
 *  - Buttons: Create editable copies, Open folder, Rebuild runtime workspace, Reset all.
 *  - Table of key files with Edit / Reset / Show default actions.
 *  - Reset all triggers a confirmation modal that mentions the backup folder
 *    (`claude-assets.backup/`) so the user knows their edits are recoverable.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { isTauriRuntime } from "../../api/client";
import {
  getClaudeAssetsStatus,
  installClaudeAssets,
  materializeRuntime,
  readClaudeAssetFile,
  resetClaudeAssetFile,
  type ClaudeAssetStatus,
} from "../../api/claudeAssets";

async function openInSystem(path: string, onError: (msg: string) => void) {
  if (!isTauriRuntime()) {
    // Outside the Tauri shell (e.g. browser dev preview) the opener plugin
    // can't invoke into the OS. Fall back to copying the path to the
    // clipboard so the user can paste it into a terminal or Finder.
    try {
      await navigator.clipboard?.writeText(path);
      onError(`Not running inside Tauri — copied path to clipboard: ${path}`);
    } catch {
      onError(`Cannot open ${path} from the browser preview.`);
    }
    return;
  }
  try {
    const { openPath } = await import("@tauri-apps/plugin-opener");
    await openPath(path);
  } catch (e) {
    onError(`Could not open ${path}: ${e instanceof Error ? e.message : String(e)}`);
  }
}

// Default file list shown when the manifest doesn't enumerate files. Paths
// must start with one of ASSET_ROOTS (".claude" or "scripts") — the backend
// rejects any path whose first segment isn't whitelisted.
const KEY_FILES = [
  ".claude/agents/akms-spec-reviewer.md",
  ".claude/agents/digest-refiner.md",
  ".claude/commands/node-gen-invoker.md",
  ".claude/skills/node-gen/SKILL.md",
  "scripts/akms_node_convert.py",
  "scripts/akms_node_clean.py",
];

function manifestEntries(status: ClaudeAssetStatus | undefined): string[] {
  if (!status?.manifest) return KEY_FILES;
  const m = status.manifest as Record<string, unknown>;
  // Manifest may carry a `files`/`entries` array, or be a flat map.
  const entries = (m.entries as Array<{ path: string }> | undefined)
    ?? (m.files as Array<{ path: string }> | undefined);
  if (Array.isArray(entries)) {
    const paths = entries.map((e) => e.path).filter(Boolean);
    return paths.length ? paths.slice(0, 24) : KEY_FILES;
  }
  // Try keys of flat map.
  const keys = Object.keys(m).filter((k) => k.includes("/") || k.endsWith(".md"));
  return keys.length ? keys.slice(0, 24) : KEY_FILES;
}

export function ClaudeAssetsPane() {
  const qc = useQueryClient();
  const { data: status, isLoading, error } = useQuery({
    queryKey: ["akms", "claude-assets", "status"],
    queryFn: getClaudeAssetsStatus,
    staleTime: 30_000,
  });

  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmResetFile, setConfirmResetFile] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ kind: "info" | "error"; text: string } | null>(null);
  const [defaultViewer, setDefaultViewer] = useState<
    { path: string; content: string } | null
  >(null);

  const installMut = useMutation({
    mutationFn: (overwrite: boolean) => installClaudeAssets(overwrite),
    onSuccess: (res) => {
      setBanner({
        kind: "info",
        text: res.warning
          ? `${res.warning} Backup at ${res.backup_path}.`
          : `Installed editable assets at ${res.path}.`,
      });
      qc.invalidateQueries({ queryKey: ["akms", "claude-assets", "status"] });
    },
    onError: (e: unknown) => setBanner({ kind: "error", text: errMsg(e) }),
  });

  const materializeMut = useMutation({
    mutationFn: () => materializeRuntime(),
    onSuccess: (res) =>
      setBanner({ kind: "info", text: `Runtime workspace rebuilt at ${res.path}.` }),
    onError: (e: unknown) => setBanner({ kind: "error", text: errMsg(e) }),
  });

  const resetFileMut = useMutation({
    mutationFn: (path: string) => resetClaudeAssetFile(path),
    onSuccess: (res) => {
      setBanner({ kind: "info", text: `Reset ${res.path} from bundled default.` });
      qc.invalidateQueries({ queryKey: ["akms", "claude-assets", "status"] });
    },
    onError: (e: unknown) => setBanner({ kind: "error", text: errMsg(e) }),
  });

  const paths = useMemo(() => manifestEntries(status), [status]);

  const installed = !!status?.installed;
  const backupPath = status ? `${status.path}.backup` : "claude-assets.backup/";

  return (
    <main className="flex h-full flex-col gap-4 p-6" aria-label="Claude Assets settings">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Claude Assets</h1>
          <p className="text-sm text-muted-foreground">
            Editable mirror of the bundled AKMS Claude assets.
          </p>
        </div>
        <span
          data-testid="claude-assets-status-pill"
          className={
            "inline-flex items-center rounded-full px-3 py-1 text-xs font-medium " +
            (installed
              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100"
              : "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100")
          }
        >
          {isLoading ? "Checking…" : installed ? "Installed" : "Uninstalled"}
        </span>
      </header>

      {banner && (
        <div
          role="status"
          className={
            "rounded border px-3 py-2 text-sm " +
            (banner.kind === "error"
              ? "border-red-300 bg-red-50 text-red-800"
              : "border-blue-300 bg-blue-50 text-blue-800")
          }
        >
          {banner.text}
        </div>
      )}

      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
          Failed to load Claude Assets status: {errMsg(error)}
        </div>
      )}

      <section className="flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded border px-3 py-1.5 text-sm hover:bg-accent"
          disabled={installMut.isPending}
          onClick={() => installMut.mutate(false)}
        >
          {installed ? "Reinstall (preserve)" : "Create editable copies"}
        </button>
        <button
          type="button"
          className="rounded border px-3 py-1.5 text-sm hover:bg-accent"
          disabled={!status}
          onClick={() => {
            const p = status?.path;
            if (p) openInSystem(p, (text) => setBanner({ kind: "error", text }));
          }}
        >
          Open folder
        </button>
        <button
          type="button"
          className="rounded border px-3 py-1.5 text-sm hover:bg-accent"
          disabled={materializeMut.isPending}
          onClick={() => materializeMut.mutate()}
        >
          Rebuild runtime workspace
        </button>
        <button
          type="button"
          className="ml-auto rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50"
          disabled={!installed}
          onClick={() => setConfirmReset(true)}
        >
          Reset all
        </button>
      </section>

      <section className="flex-1 overflow-auto rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
            <tr>
              <th className="px-3 py-2">File</th>
              <th className="px-3 py-2 w-48">Actions</th>
            </tr>
          </thead>
          <tbody>
            {paths.map((p) => (
              <tr key={p} className="border-t">
                <td className="px-3 py-2 font-mono text-xs">{p}</td>
                <td className="px-3 py-2">
                  <div className="flex gap-1">
                    <button
                      type="button"
                      className="rounded border px-2 py-0.5 text-xs hover:bg-accent"
                      onClick={() => {
                        const full = `${status?.path?.replace(/\/$/, "") ?? ""}/${p}`;
                        openInSystem(full, (text) => setBanner({ kind: "error", text }));
                      }}
                      disabled={!installed}
                      title={installed ? `Open ${p} in system editor` : "Install editable copies first"}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="rounded border px-2 py-0.5 text-xs hover:bg-accent"
                      onClick={() => setConfirmResetFile(p)}
                      disabled={!installed}
                    >
                      Reset
                    </button>
                    <button
                      type="button"
                      className="rounded border px-2 py-0.5 text-xs hover:bg-accent"
                      onClick={async () => {
                        try {
                          const res = await readClaudeAssetFile(p, { default: true });
                          setDefaultViewer({ path: p, content: res.content });
                        } catch (e) {
                          setBanner({ kind: "error", text: errMsg(e) });
                        }
                      }}
                      title="View the bundled default content (read-only)"
                    >
                      Show default
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {confirmReset && (
        <Modal
          title="Reset all Claude assets?"
          onClose={() => setConfirmReset(false)}
          onConfirm={() => {
            setConfirmReset(false);
            installMut.mutate(true);
          }}
          confirmLabel="Reset all"
          confirmTone="danger"
        >
          <p className="text-sm">
            This will replace the user-editable Claude assets folder at
            <code className="mx-1 rounded bg-muted px-1 font-mono text-xs">{status?.path}</code>
            with the bundled defaults.
          </p>
          <p className="text-sm">
            Your current edits will be moved to{" "}
            <code data-testid="reset-backup-path" className="mx-1 rounded bg-muted px-1 font-mono text-xs">
              {backupPath}
            </code>
            (claude-assets.backup) so they remain recoverable until the next reset.
          </p>
        </Modal>
      )}

      {confirmResetFile && (
        <Modal
          title={`Reset ${confirmResetFile}?`}
          onClose={() => setConfirmResetFile(null)}
          onConfirm={() => {
            const p = confirmResetFile;
            setConfirmResetFile(null);
            if (p) resetFileMut.mutate(p);
          }}
          confirmLabel="Reset file"
          confirmTone="danger"
        >
          <p className="text-sm">
            Restore the bundled default for
            <code className="mx-1 rounded bg-muted px-1 font-mono text-xs">{confirmResetFile}</code>?
            Your edits to this single file will be discarded.
          </p>
        </Modal>
      )}

      {defaultViewer && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Bundled default — ${defaultViewer.path}`}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <div className="flex h-[80vh] w-full max-w-3xl flex-col rounded bg-background p-5 shadow-lg">
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold">Bundled default</h2>
                <code className="mt-0.5 block font-mono text-xs text-muted-foreground">
                  {defaultViewer.path}
                </code>
              </div>
              <button
                type="button"
                className="rounded border px-3 py-1 text-sm hover:bg-accent"
                onClick={() => setDefaultViewer(null)}
              >
                Close
              </button>
            </div>
            <pre className="flex-1 overflow-auto rounded border bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap">
              {defaultViewer.content}
            </pre>
          </div>
        </div>
      )}
    </main>
  );
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

interface ModalProps {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  onConfirm: () => void;
  confirmLabel: string;
  confirmTone?: "danger" | "primary";
}

function Modal({ title, children, onClose, onConfirm, confirmLabel, confirmTone }: ModalProps) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-md rounded bg-background p-5 shadow-lg">
        <h2 className="mb-3 text-base font-semibold">{title}</h2>
        <div className="space-y-2">{children}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="rounded border px-3 py-1 text-sm hover:bg-accent"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            className={
              "rounded px-3 py-1 text-sm text-white " +
              (confirmTone === "danger" ? "bg-red-600 hover:bg-red-700" : "bg-blue-600 hover:bg-blue-700")
            }
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
