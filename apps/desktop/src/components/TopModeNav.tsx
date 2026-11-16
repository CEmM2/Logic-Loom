import { Link, useNavigate } from "react-router-dom";

import { ThemeToggle } from "./ThemeToggle";

export type Mode = "zsum" | "akms" | "assistant" | "mechdsl" | "failure-memory" | "records";

export interface TopLevelMode {
  id: Mode;
  label: string;
  subtitle: string;
}

/** The top-level modes, in mode-switcher order. */
export const topLevelModes: readonly TopLevelMode[] = [
  { id: "zsum", label: "Zsum", subtitle: "Summarize and publish Zotero literature" },
  { id: "akms", label: "AKMS", subtitle: "Generate and review knowledge nodes" },
  { id: "assistant", label: "Assistant", subtitle: "Discover papers and run research workflows" },
  // The MechDSL executable-bridge plugin mode (not from the bundling guide;
  // gated on /api/mechdsl/status).
  { id: "mechdsl", label: "MechDSL", subtitle: "Compile AKMS nodes into Taichi solvers" },
  // The Failure Memory workspace (not from
  // the bundling guide; gated on /api/failure-memory/projects). Read-only:
  // Logic-Loom inspects and previews project failure registries, and the package
  // remains the sole recorder/compiler/provider.
  {
    id: "failure-memory",
    label: "Failure Memory",
    subtitle: "Inspect project failure registries, routes, and resolution previews",
  },
  // akms-records stores (not from the bundling guide; gated on
  // /api/records/status). Read-only browse of user-schema records + their graph.
  {
    id: "records",
    label: "Records",
    subtitle: "Browse akms-records stores, their schemas, and the record graph",
  },
] as const;

interface TopModeNavProps {
  currentMode: Mode;
  onModeChange: (mode: Mode) => void;
}

export function TopModeNav({ currentMode, onModeChange }: TopModeNavProps) {
  const navigate = useNavigate();

  function handleModeChange(mode: Mode) {
    onModeChange(mode);
    navigate(`/${mode}`);
  }

  const active = topLevelModes.find((m) => m.id === currentMode)!;

  return (
    <header className="border-b border-border bg-background px-4 py-2">
      <div className="flex items-center gap-4">
        <Link
          to="/"
          className="text-sm font-bold tracking-wide hover:opacity-70"
          aria-label="Logic-Loom home"
          title="Logic-Loom home"
        >
          LogicLoom
        </Link>
        <nav className="flex gap-1" aria-label="Top-level mode switcher">
          {topLevelModes.map((mode) => (
            <button
              key={mode.id}
              onClick={() => handleModeChange(mode.id)}
              aria-pressed={mode.id === currentMode}
              className={
                mode.id === currentMode
                  ? "rounded px-3 py-1 text-sm font-semibold bg-accent text-accent-foreground"
                  : "rounded px-3 py-1 text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50"
              }
            >
              {mode.label}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          <ThemeToggle />
          <Link
            to="/settings"
            className="rounded px-3 py-1 text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50"
          >
            Settings
          </Link>
        </div>
      </div>
      {active && (
        <p className="mt-1 text-xs text-muted-foreground">{active.subtitle}</p>
      )}
    </header>
  );
}
