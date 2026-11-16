import { Suspense, lazy } from "react";
import { Link, useSearchParams } from "react-router-dom";

const ZsumSettingsRoute = lazy(() => import("./zsum/settings"));
const AkmsSettingsRoute = lazy(() => import("./akms/settings"));
const AssistantSettingsRoute = lazy(() => import("./assistant/settings"));

type Subsystem = "zsum" | "akms" | "assistant";

const SUBSYSTEMS: { id: Subsystem; label: string; description: string }[] = [
  {
    id: "zsum",
    label: "Zsum",
    description: "Project paths, command, cost estimation, nodes review.",
  },
  {
    id: "akms",
    label: "AKMS",
    description: "Editable Claude assets — bundled defaults and per-file reset.",
  },
  {
    id: "assistant",
    label: "Assistant",
    description: "OpenAlex / Semantic Scholar credentials and assistant workspace.",
  },
];

function normalizeSubsystem(value: string | null): Subsystem {
  if (value === "akms" || value === "assistant" || value === "zsum") return value;
  return "zsum";
}

function SubsystemFallback() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      Loading settings…
    </div>
  );
}

export default function UnifiedSettingsRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const active = normalizeSubsystem(searchParams.get("subsystem"));

  const select = (id: Subsystem) => {
    const next = new URLSearchParams(searchParams);
    next.set("subsystem", id);
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-border bg-background px-4 py-2">
        <div className="flex items-center gap-3">
          <Link
            to="/"
            className="text-sm font-bold tracking-wide hover:opacity-70"
            aria-label="Logic-Loom home"
          >
            LogicLoom
          </Link>
          <span className="text-sm text-muted-foreground">/ Settings</span>
        </div>
        <Link
          to="/"
          className="rounded px-3 py-1 text-xs text-muted-foreground hover:bg-accent/50 hover:text-foreground"
        >
          ← Home
        </Link>
      </header>

      <div className="grid flex-1 grid-cols-[240px_1fr] overflow-hidden">
        <aside className="flex flex-col border-r border-border bg-background/40">
          <div className="border-b border-border px-4 py-3">
            <h1 className="text-sm font-semibold">Subsystems</h1>
            <p className="mt-1 text-xs text-muted-foreground">
              Choose which subsystem's settings to edit.
            </p>
          </div>
          <nav className="flex-1 overflow-y-auto p-2">
            {SUBSYSTEMS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => select(s.id)}
                className={`mb-1 block w-full rounded px-3 py-2 text-left text-sm ${
                  active === s.id
                    ? "bg-muted font-semibold"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                }`}
              >
                <div>{s.label}</div>
                <div className="mt-0.5 text-xs font-normal text-muted-foreground">
                  {s.description}
                </div>
              </button>
            ))}
          </nav>
        </aside>

        <main className="overflow-hidden">
          <Suspense fallback={<SubsystemFallback />}>
            {active === "zsum" && <ZsumSettingsRoute />}
            {active === "akms" && <AkmsSettingsRoute />}
            {active === "assistant" && <AssistantSettingsRoute />}
          </Suspense>
        </main>
      </div>
    </div>
  );
}
