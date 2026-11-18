import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { WorkspaceSearch, type NavigationTarget } from "./assistant/WorkspaceSearch";

/**
 * GlobalSearch — the Cmd-K / Ctrl-K workspace search, shortcut and modal.
 *
 * Rendered by AppShell (every mode) and by the Home page, which sits outside
 * the shell; with the listener living only in AppShell, Ctrl-K did nothing on
 * Home (v0.3.3 retest).
 *
 * A keydown listener on `document`, in the capture phase, fires regardless of
 * which widget has focus, so a focused editor or dialog that stops
 * propagation cannot swallow the shortcut.
 */
export function GlobalSearch() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      // Cmd-K on macOS, Ctrl-K on Windows/Linux. Match on the physical key
      // too: `event.key` is "K" with Caps Lock or Shift held, which made the
      // first press look ignored (v0.3.1 QA).
      const isK = event.code === "KeyK" || event.key.toLowerCase() === "k";
      const isShortcut = isK && (event.metaKey || event.ctrlKey) && !event.altKey;
      if (!isShortcut) return;
      event.preventDefault();
      if (event.repeat) return;
      setOpen((prev) => !prev);
    }
    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, []);

  const handleNavigate = useCallback(
    (target: NavigationTarget) => {
      // Pass the full NavigationTarget as router state so the destination
      // route can deep-link to the specific recordId / batchId / runId.
      navigate(target.path, { state: target });
    },
    [navigate],
  );

  return <WorkspaceSearch open={open} onClose={() => setOpen(false)} onNavigate={handleNavigate} />;
}
