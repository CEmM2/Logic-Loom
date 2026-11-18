import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import bannerUrl from "../assets/banner.png";
import { api, getApiBase } from "../api/client";
import { GlobalSearch } from "../components/GlobalSearch";
import { topLevelModes } from "../components/TopModeNav";
import { ModelsDialog } from "../components/home/ModelsDialog";

type HealthResponse = { status: string; version: string };

/** Modes that need an optional extra or config before they show anything. */
const OPTIONAL_MODES: ReadonlySet<string> = new Set(["mechdsl", "failure-memory", "records"]);

/** One card per top-level mode, in mode-switcher order. */
const MODES = topLevelModes.map((m) => ({
  id: m.id,
  label: m.label,
  to: `/${m.id}`,
  description: m.subtitle,
  optional: OPTIONAL_MODES.has(m.id),
}));

export default function HomeRoute() {
  const [base, setBase] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modelsOpen, setModelsOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const resolved = await getApiBase();
        if (!alive) return;
        setBase(resolved);
        const h = await api<HealthResponse>("/api/app/health");
        if (alive) setHealth(h);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return (
    <main
      className="ll-home relative flex min-h-screen flex-col items-center justify-center gap-8 overflow-hidden bg-background p-8"
    >
      <header className="flex w-full max-w-4xl flex-col items-center gap-3">
        <img
          src={bannerUrl}
          alt="Logic-Loom"
          className="w-full max-w-3xl object-contain drop-shadow-lg"
          style={{ aspectRatio: "3172 / 1344" }}
        />
        <p className="text-base text-muted-foreground">
          Unified desktop shell for zsum, AKMS, and research workflows.
        </p>
        <p className="text-xs text-muted-foreground">
          Press <kbd className="rounded border border-border px-1 font-mono">Ctrl</kbd>+
          <kbd className="rounded border border-border px-1 font-mono">K</kbd> (
          <kbd className="rounded border border-border px-1 font-mono">⌘</kbd>
          <kbd className="rounded border border-border px-1 font-mono">K</kbd> on macOS) to search the
          workspace.
        </p>
      </header>

      <section className="grid w-full max-w-3xl gap-4 sm:grid-cols-3">
        {MODES.map((m) => (
          <Link
            key={m.id}
            to={m.to}
            className="flex flex-col gap-2 rounded-lg border border-border bg-background/80 p-5 shadow-sm backdrop-blur transition hover:border-foreground/40 hover:bg-muted/70 hover:shadow-md"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-lg font-semibold">{m.label}</span>
              {m.optional && (
                <span
                  className="rounded border border-border px-1.5 text-[10px] uppercase tracking-wide text-muted-foreground"
                  title="Needs an optional install or config; the mode explains what to set up."
                >
                  optional
                </span>
              )}
            </div>
            <div className="text-xs text-muted-foreground">{m.description}</div>
          </Link>
        ))}
      </section>

      <nav className="flex gap-3 text-xs">
        <Link to="/settings" className="rounded border border-border px-3 py-1 hover:bg-muted">
          Settings
        </Link>
        <button
          type="button"
          onClick={() => setModelsOpen(true)}
          className="rounded border border-border px-3 py-1 hover:bg-muted"
        >
          Models
        </button>
      </nav>

      <footer className="flex flex-col items-center gap-1 text-[11px] text-muted-foreground">
        {base && <span className="font-mono">{base}</span>}
        {health && (
          <span>
            API <span className="font-mono">{health.status}</span> · v{health.version}
          </span>
        )}
        {error && (
          <span className="text-red-600">
            API unreachable: <span className="font-mono">{error}</span>
          </span>
        )}
      </footer>

      <ModelsDialog open={modelsOpen} onClose={() => setModelsOpen(false)} />
      <GlobalSearch />
    </main>
  );
}
