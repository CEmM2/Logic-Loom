import { useCallback, useEffect, useState } from "react";
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
export function AppShell({ currentMode, onModeChange }: AppShellProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      // Cmd-K on macOS, Ctrl-K on Windows/Linux. `metaKey` is the
      // platform-agnostic name for the Cmd key in DOM events.
      const isShortcut = event.key === "k" && (event.metaKey || event.ctrlKey);
      if (isShortcut) {
        event.preventDefault();
        setSearchOpen((prev) => !prev);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
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
          <Outlet />
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
