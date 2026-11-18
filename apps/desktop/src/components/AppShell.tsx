import type { ComponentType, ReactNode } from "react";
import { Outlet } from "react-router-dom";

import { GlobalRunStatus } from "./GlobalRunStatus";
import { GlobalSearch } from "./GlobalSearch";
import { SectionSideNav } from "./SectionSideNav";
import { TopModeNav, type Mode } from "./TopModeNav";

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
 *   GlobalSearch (Cmd-K / Ctrl-K workspace search; Home renders it too)
 */
export function AppShell({ currentMode, onModeChange, gate: Gate }: AppShellProps) {
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
      <GlobalSearch />
    </div>
  );
}
