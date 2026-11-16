/**
 * GlobalRunStatus — compact pill/bar showing active background runs.
 *
 * The Runs page feeds this store via `addRun` and `removeRun`: it pushes
 * a run when it starts streaming and removes it when the SSE stream
 * reports a terminal status. Contract: renders nothing when empty.
 */
import { create } from "zustand";

// ── Store ─────────────────────────────────────────────────────────────────────

export interface ActiveRun {
  id: string;
  label: string;
  progress?: number;
}

interface GlobalRunStatusState {
  activeRuns: ActiveRun[];
  addRun: (run: ActiveRun) => void;
  removeRun: (id: string) => void;
  clearAll: () => void;
}

export const useGlobalRunStatusStore = create<GlobalRunStatusState>((set) => ({
  activeRuns: [],
  addRun: (run) =>
    set((state) => {
      // Idempotent: replace any existing entry with the same id.
      const filtered = state.activeRuns.filter((r) => r.id !== run.id);
      return { activeRuns: [...filtered, run] };
    }),
  removeRun: (id) =>
    set((state) => ({
      activeRuns: state.activeRuns.filter((r) => r.id !== id),
    })),
  clearAll: () => set({ activeRuns: [] }),
}));

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * Renders a compact status bar when runs are active.
 * Returns null when the store is empty.
 */
export function GlobalRunStatus() {
  const activeRuns = useGlobalRunStatusStore((s) => s.activeRuns);

  if (activeRuns.length === 0) {
    return null;
  }

  return (
    <div
      data-testid="global-run-status"
      className="fixed bottom-0 left-0 right-0 flex items-center gap-2 border-t bg-background px-4 py-1.5 text-xs text-muted-foreground"
    >
      {activeRuns.map((run) => (
        <span
          key={run.id}
          className="inline-flex items-center gap-1 rounded-full border bg-card px-2 py-0.5"
        >
          {run.label}
          {run.progress !== undefined && (
            <span className="text-[10px] opacity-70">{Math.round(run.progress)}%</span>
          )}
        </span>
      ))}
    </div>
  );
}
