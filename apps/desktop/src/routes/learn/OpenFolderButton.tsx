/**
 * Open Export Folder button.
 *
 * Opens a compile artifact's export directory in the host OS file manager.
 *
 * Mechanism:
 *   - Uses `@tauri-apps/plugin-opener` (`openPath`), which is already registered
 *     in Cargo.toml (`tauri-plugin-opener = "2"`), initialised in lib.rs
 *     (`.plugin(tauri_plugin_opener::init())`), and permitted in the default
 *     capability (`"opener:allow-open-path"`). This is the SAME plugin+pattern
 *     used by `ClaudeAssetsPane.tsx` — reused here verbatim.
 *   - Relies on `isTauriRuntime()` from `api/client.ts` for the Tauri feature-detect
 *     (CLAUDE.md: "reuse `isTauriRuntime()` instead of poking `window.__TAURI_INTERNALS__`
 *     directly").
 *
 * Graceful degradation:
 *   - When `folderPath` is null/empty: button is disabled with a tooltip.
 *   - When NOT in Tauri runtime (browser/dev preview): button is disabled with a
 *     tooltip explaining it requires the desktop app. NEVER crashes.
 *   - OS portability: relies on the Tauri opener abstraction; no hard-coded
 *     `open`/`xdg-open`/`explorer.exe` shell commands.
 *
 * `folderPath` should be `cache_dir` from the compile response (the artifact
 * directory `<cache_path>/learning/{artifact_id}/`). If the compile response
 * has a null cache_dir, the button is disabled.
 */

import { useState } from "react";
import { isTauriRuntime } from "../../api/client";

interface Props {
  folderPath: string | null;
  /** Optional label; defaults to "Open export folder". */
  label?: string;
  className?: string;
}

/**
 * Opens `folderPath` in the host file manager via `openPath` (Tauri opener plugin).
 *
 * Falls back gracefully when:
 *   - `folderPath` is null/empty → button disabled ("no export folder known")
 *   - Not inside Tauri runtime   → button disabled (tooltip: "desktop app only")
 */
export function OpenFolderButton({ folderPath, label = "Open export folder", className }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);

  const inTauri = isTauriRuntime();
  const hasPath = folderPath != null && folderPath.trim().length > 0;
  const enabled = inTauri && hasPath;

  const disabledTitle = !hasPath
    ? "No export folder path available for this artifact."
    : "Open export folder is only available inside the desktop app.";

  const handleClick = async () => {
    if (!enabled) return;
    setError(null);
    setOpening(true);
    try {
      // Dynamic import keeps the opener plugin out of the browser bundle
      // (it would throw if imported eagerly outside Tauri). Same pattern as
      // ClaudeAssetsPane.tsx.
      const { openPath } = await import("@tauri-apps/plugin-opener");
      await openPath(folderPath!);
    } catch (e) {
      setError(
        `Could not open folder: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setOpening(false);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        data-testid="learn-open-folder"
        onClick={handleClick}
        disabled={!enabled || opening}
        title={enabled ? `Open: ${folderPath}` : disabledTitle}
        className={
          className ??
          "rounded border border-border px-3 py-1 text-sm hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
        }
      >
        {opening ? "Opening…" : label}
      </button>
      {!inTauri ? (
        <span
          data-testid="learn-open-folder-hint"
          className="text-[10px] text-muted-foreground"
        >
          Available in the desktop app only.
        </span>
      ) : null}
      {error ? (
        <span
          data-testid="learn-open-folder-error"
          className="text-xs text-red-600"
        >
          {error}
        </span>
      ) : null}
    </div>
  );
}
