import { useCallback, useEffect, useState, type ComponentType, type ReactNode } from "react";
import { Outlet, useNavigate } from "react-router-dom";

import { GlobalRunStatus } from "./GlobalRunStatus";
import { SectionSideNav } from "./SectionSideNav";
import { TopModeNav, type Mode } from "./TopModeNav";
import {
  WorkspaceSearch,
  type NavigationTarget,
} from "./assistant/WorkspaceSearch";

interface AppShellProps {
  currentMode: Mode;
  onModeChange: (mode: Mode) => void;
  /**
   * Optional availability gate (AkmsGate, MechdslGate, ...). It wraps only the
   * routed content, so the mode switcher and side nav stay usable while the
   * gate explains why the mode is unavailable. Wrapping the whole shell in a
   * gate left the user with no way out of the explanation screen.
   */
  gate?: ComponentType<{ children: ReactNode }>;
}

/**
 * AppShell — the top-level mode shell.
 *
 * Layout:
 *   ┌─────────────────────────────┐
 *   │  TopModeNav (mode switcher) │
 *   ├──────────┬──────────────────┤
 *   │ SideNav  │  <Outlet />      │
 *   └──────────┴──────────────────┘
 *   GlobalRunStatus (outside grid — renders nothing when store is empty)
 *   WorkspaceSearch (modal, opens on Cmd-K / Ctrl-K from any sub-tab)
 *
 * Cmd-K / Ctrl-K registration: a global keydown listener on `document`
 * fires regardless of which sub-tab is focused, so the shortcut works
 * from any Assistant route (and any other mode too — the modal is
 * shell-level).
 */
export function AppShell({ currentMode, onModeChange, gate: Gate }: AppShellProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      // Cmd-K on macOS, Ctrl-K on Windows/Linux. `metaKey` is the
      // platform-agnostic name for the Cmd key in DOM events.
      // Match on the physical key too: `event.key` is "K" with Caps Lock or
      // Shift held, which made the first press look ignored (v0.3.1 QA).
      const isK = event.code === "KeyK" || event.key.toLowerCase() === "k";
      const isShortcut = isK && (event.metaKey || event.ctrlKey) && !event.altKey;
      if (isShortcut) {
        if (event.repeat) {
          event.preventDefault();
          return;
        }
        event.preventDefault();
        setSearchOpen((prev) => !prev);
      }
    }
    // Capture phase, so a focused widget that stops propagation (an editor,
    // a dialog) cannot swallow the shortcut.
    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, []);

  const handleSearchNavigate = useCallback(
    (target: NavigationTarget) => {
      // Pass the full NavigationTarget as router state so the destination
      // route can deep-link to the specific recordId / batchId / runId
      // (e.g. open the matching batch, scroll to the row, highlight it).
      navigate(target.path, { state: target });
    },
    [navigate],
  );

  return (
    <div className="min-h-screen grid grid-rows-[auto_1fr]">
      <TopModeNav currentMode={currentMode} onModeChange={onModeChange} />
      <div className="grid grid-cols-[240px_1fr]">
        <SectionSideNav mode={currentMode} />
        <main className="overflow-auto">
          {Gate ? (
            <Gate>
              <Outlet />
            </Gate>
          ) : (
            <Outlet />
          )}
        </main>
      </div>
      <GlobalRunStatus />
      <WorkspaceSearch
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onNavigate={handleSearchNavigate}
      />
    </div>
  );
}
