import { Group, Panel, Separator, type Layout } from "react-resizable-panels";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { getConfig } from "../../api/app";
import {
  cancelRun,
  getZsumIndex,
  listRuns,
  streamRun,
  submitRun,
  type LogEvent,
  type RunRecord,
  type ZsumStage,
} from "../../api/zsum";
import { CollectionTree } from "../../components/zsum/CollectionTree";
import { ConfigEditor } from "../../components/zsum/ConfigEditor";
import { EffectiveConfigBanner } from "../../components/zsum/EffectiveConfigBanner";
import { LogStream } from "../../components/LogStream";
import { PaperList } from "../../components/zsum/PaperList";
import { PipelineStrip } from "../../components/zsum/PipelineStrip";
import { StatusBar } from "../../components/zsum/StatusBar";
import { cn } from "../../lib/utils";

/**
 * Pipeline route — Library / Config tabs that mirror the Obsidian
 * "Zotero Summarizer" view. Library: status bar + tree + papers + stage
 * pad + log pane (with progress strip); Config: schema-driven editor
 * for zsum.config.yaml.
 */

type StageGroup = "local" | "llm";
type StageVariant = "default" | "primary" | "llm" | "danger";

interface StageButtonDef {
  label: string;
  stage: ZsumStage | "cancel";
  forceable: boolean;
  group: StageGroup;
  variant: StageVariant;
}

const STAGE_BUTTONS: readonly StageButtonDef[] = [
  { label: "Skeleton", stage: "skeleton", forceable: false, group: "local", variant: "default" },
  { label: "Citations", stage: "citations", forceable: false, group: "local", variant: "default" },
  { label: "Publish", stage: "publish", forceable: false, group: "local", variant: "default" },
  { label: "Reviews", stage: "reviews", forceable: false, group: "local", variant: "default" },
  { label: "Stats", stage: "stats", forceable: false, group: "local", variant: "default" },
  { label: "Cards", stage: "cards", forceable: true, group: "llm", variant: "llm" },
  { label: "Digests", stage: "digests", forceable: true, group: "llm", variant: "llm" },
  { label: "Redigest", stage: "redigest", forceable: true, group: "llm", variant: "llm" },
  { label: "Restructure", stage: "restructure", forceable: false, group: "llm", variant: "llm" },
  { label: "Full Run", stage: "all", forceable: false, group: "llm", variant: "primary" },
  { label: "Cancel", stage: "cancel", forceable: false, group: "llm", variant: "danger" },
];

const TERMINAL: Record<RunRecord["status"], boolean> = {
  pending: false,
  running: false,
  succeeded: true,
  failed: true,
  cancelled: true,
};

type Tab = "library" | "config";

export default function PipelineRoute() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("library");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [force, setForce] = useState(false);
  const [pendingStage, setPendingStage] = useState<string | null>(null);
  const closeRef = useRef<(() => void) | null>(null);

  // Library state — selected collection + expanded tree nodes
  const [selectedCollection, setSelectedCollection] = useState<string>("root");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const { data: runs = [] } = useQuery({
    queryKey: ["zsum", "runs"],
    queryFn: listRuns,
    refetchInterval: 5_000,
  });
  const { data: cfg } = useQuery({ queryKey: ["app", "config"], queryFn: getConfig });
  const { data: indexResp } = useQuery({ queryKey: ["zsum", "index"], queryFn: getZsumIndex });

  const costPerCard = useMemo(() => {
    if (!cfg) return null;
    return (
      (cfg.zsum_tokens_per_card_in * cfg.zsum_cost_per_mtoken_in) / 1e6 +
      (cfg.zsum_tokens_per_card_out * cfg.zsum_cost_per_mtoken_out) / 1e6
    );
  }, [cfg]);

  // Cost line in the pipeline header now reflects pending-card count when
  // the index is available (matches view.ts renderCostHeader()).
  const pendingCount =
    indexResp?.kind === "ok"
      ? Object.values(indexResp.index.items).filter((p) => p.card_state === "pending").length
      : null;

  useEffect(() => {
    if (!activeRunId) return;
    setEvents([]);
    let alive = true;
    streamRun(
      activeRunId,
      (e) => {
        if (alive) setEvents((prev) => [...prev, e]);
      },
      () => {
        if (alive) {
          qc.invalidateQueries({ queryKey: ["zsum", "runs"] });
          qc.invalidateQueries({ queryKey: ["zsum", "index"] });
        }
      },
    ).then((close) => {
      closeRef.current = close;
      if (!alive) close();
    });
    return () => {
      alive = false;
      closeRef.current?.();
      closeRef.current = null;
    };
  }, [activeRunId, qc]);

  const activeRun = runs.find((r) => r.id === activeRunId) ?? null;
  const running = !!activeRun && !TERMINAL[activeRun.status];

  const onStage = async (def: StageButtonDef, opts: { extraArgs?: string[] } = {}) => {
    setError(null);
    if (def.stage === "cancel") {
      if (!activeRunId) return;
      try {
        await cancelRun(activeRunId);
      } catch (e) {
        setError((e as Error).message);
      }
      return;
    }
    if (running) return;
    setPendingStage(def.stage);
    try {
      const extra = [...(opts.extraArgs ?? []), ...(def.forceable && force ? ["--force"] : [])];
      const rec = await submitRun(def.stage, { extra_args: extra });
      setActiveRunId(rec.id);
      qc.invalidateQueries({ queryKey: ["zsum", "runs"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPendingStage(null);
    }
  };

  // "Regenerate digest" on a paper-list collection → run digests + redigest
  // for that collection name (mirrors plugin's startCollectionRefreshQueue).
  const onRegenDigest = async (collectionKey: string) => {
    if (collectionKey === "root" || indexResp?.kind !== "ok") return;
    const col = indexResp.index.collections[collectionKey];
    if (!col) return;
    const digestsBtn = STAGE_BUTTONS.find((b) => b.stage === "digests");
    if (!digestsBtn) return;
    await onStage(digestsBtn, { extraArgs: ["--collection", col.name] });
    // Note: the Obsidian plugin queues "redigest" after digests succeeds
    // by listening to runner "done". LogicLoom runs are persistent — kicking
    // off both here would race; leave the redigest follow-up to the user.
  };

  const onOpenPaper = (citekey: string) => {
    if (!cfg?.vault_path) return;
    // zsum publishes paper notes as "Papers/@citekey.md" inside vault_output.
    // The Vault route resolves paths under cfg.vault_path.
    navigate(`/zsum/vault/file/${encodeURIComponent(`Papers/@${citekey}.md`)}`);
  };

  const configBasename = useMemo(() => {
    if (!cfg) return "config.yaml";
    const p = cfg.zsum_config_path_effective;
    return p.split(/[\\/]/).pop() || "config.yaml";
  }, [cfg]);

  return (
    <Group orientation="horizontal" className="h-full">
      {/* Run history sidebar */}
      <Panel
        defaultSize={16}
        minSize={10}
        className="flex flex-col overflow-hidden border-r border-border"
      >
        <div className="shrink-0 border-b border-border p-3 text-xs uppercase tracking-wide text-muted-foreground">
          Run history
        </div>
        <ul className="flex-1 overflow-y-auto">
          {runs
            .slice()
            .reverse()
            .map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => setActiveRunId(r.id)}
                  className={cn(
                    "flex w-full flex-col rounded px-3 py-2 text-left text-xs hover:bg-muted",
                    activeRunId === r.id && "bg-muted",
                  )}
                >
                  <span className="font-medium">{r.stage}</span>
                  <span className="text-muted-foreground">{r.status}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {r.id.slice(0, 8)}
                  </span>
                </button>
              </li>
            ))}
        </ul>
      </Panel>

      <Separator className="w-1 cursor-col-resize bg-border transition-colors hover:bg-primary/30" />

      <Panel defaultSize={84} minSize={40} className="flex flex-col overflow-hidden">
        {/* Tab bar — Library / Config */}
        <div className="flex shrink-0 items-center gap-1 border-b border-border px-2">
          {(["library", "config"] as const).map((id) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                "border-b-2 px-3 py-2 text-xs uppercase tracking-wide",
                tab === id
                  ? "border-foreground font-semibold"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {id === "library" ? "Library" : "Config"}
            </button>
          ))}
        </div>

        {tab === "library" ? (
          <LibraryPanel
            cfg={cfg}
            indexResp={indexResp}
            configBasename={configBasename}
            costPerCard={costPerCard}
            pendingCount={pendingCount}
            running={running}
            activeStage={running ? activeRun?.stage ?? null : null}
            pendingStage={pendingStage}
            force={force}
            onForceChange={setForce}
            error={error}
            activeRun={activeRun}
            events={events}
            selectedCollection={selectedCollection}
            expanded={expanded}
            onSelectCollection={setSelectedCollection}
            onToggleCollection={(k) =>
              setExpanded((s) => {
                const n = new Set(s);
                if (n.has(k)) n.delete(k);
                else n.add(k);
                return n;
              })
            }
            onStage={onStage}
            onOpenPaper={onOpenPaper}
            onRegenDigest={onRegenDigest}
          />
        ) : (
          <ConfigEditor />
        )}
      </Panel>
    </Group>
  );
}

// ── Library panel ───────────────────────────────────────────────
function LibraryPanel(props: {
  cfg: ReturnType<typeof getConfig> extends Promise<infer T> ? T | undefined : never;
  indexResp: ReturnType<typeof getZsumIndex> extends Promise<infer T> ? T | undefined : never;
  configBasename: string;
  costPerCard: number | null;
  pendingCount: number | null;
  running: boolean;
  activeStage: string | null;
  pendingStage: string | null;
  force: boolean;
  onForceChange: (v: boolean) => void;
  error: string | null;
  activeRun: RunRecord | null;
  events: LogEvent[];
  selectedCollection: string;
  expanded: Set<string>;
  onSelectCollection: (k: string) => void;
  onToggleCollection: (k: string) => void;
  onStage: (def: StageButtonDef, opts?: { extraArgs?: string[] }) => Promise<void>;
  onOpenPaper: (citekey: string) => void;
  onRegenDigest: (collectionKey: string) => void;
}) {
  const {
    configBasename,
    costPerCard,
    pendingCount,
    running,
    activeStage,
    pendingStage,
    force,
    onForceChange,
    error,
    activeRun,
    events,
    indexResp,
    selectedCollection,
    expanded,
    onSelectCollection,
    onToggleCollection,
    onStage,
    onOpenPaper,
    onRegenDigest,
  } = props;

  const indexOk = indexResp?.kind === "ok";

  // Collapsible config strip (StatusBar + EffectiveConfigBanner). Persist
  // across reloads keyed by route — matches the divider persistence below.
  const [topCollapsed, setTopCollapsed] = useLocalBoolean(
    "zsum-pipeline.topCollapsed",
    false,
  );

  // Vertical split between content (tree+papers+logs) and pipeline commands.
  // Persist the layout per route via localStorage so the divider position
  // survives reloads.
  const [verticalDefault, onVerticalLayoutChanged] = useLayoutPersistence(
    "zsum-pipeline.vertical.v1",
  );
  const [topRowDefault, onTopRowLayoutChanged] = useLayoutPersistence(
    "zsum-pipeline.topRow.v1",
  );

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Collapsible top strip — chevron header summarises what's hidden */}
      <CollapsibleHeader
        collapsed={topCollapsed}
        onToggle={() => setTopCollapsed((v) => !v)}
        summary={configBasename}
      >
        <EffectiveConfigBanner />
        <StatusBar configBasename={configBasename} />
      </CollapsibleHeader>

      {/* Top zone (content) + draggable divider + bottom zone (commands) */}
      <Group
        orientation="vertical"
        defaultLayout={verticalDefault}
        onLayoutChanged={onVerticalLayoutChanged}
        className="flex-1"
      >
        <Panel
          id="zsum-pipeline.content"
          defaultSize={70}
          minSize={30}
          className="overflow-hidden"
        >
          {/* Tree | PaperList | LogStream — all internal-scrolling */}
          <Group
            orientation="horizontal"
            defaultLayout={topRowDefault}
            onLayoutChanged={onTopRowLayoutChanged}
            className="h-full"
          >
            <Panel
              id="zsum-pipeline.tree"
              defaultSize={22}
              minSize={14}
              className="overflow-hidden border-r border-border"
            >
              {indexOk ? (
                <CollectionTree
                  index={indexResp.index}
                  selectedKey={selectedCollection}
                  expanded={expanded}
                  onSelect={onSelectCollection}
                  onToggle={onToggleCollection}
                />
              ) : (
                <IndexFallback indexResp={indexResp} />
              )}
            </Panel>
            <Separator className="w-1 cursor-col-resize bg-border hover:bg-primary/30" />
            <Panel
              id="zsum-pipeline.papers"
              defaultSize={48}
              minSize={25}
              className="overflow-hidden"
            >
              {indexOk ? (
                <PaperList
                  index={indexResp.index}
                  collectionKey={selectedCollection}
                  onOpenPaper={onOpenPaper}
                  onRegenDigest={onRegenDigest}
                />
              ) : (
                <IndexFallback indexResp={indexResp} />
              )}
            </Panel>
            <Separator className="w-1 cursor-col-resize bg-border hover:bg-primary/30" />
            <Panel
              id="zsum-pipeline.logs"
              defaultSize={30}
              minSize={18}
              className="flex flex-col overflow-hidden border-l border-border"
            >
              <PipelineStrip events={events} />
              <LogStream events={events} />
            </Panel>
          </Group>
        </Panel>

        {/* Single horizontal draggable divider — wider hit target so it's
            obviously grabbable; cursor-row-resize signals direction. */}
        <Separator className="h-1.5 cursor-row-resize bg-border transition-colors hover:bg-primary/30" />

        <Panel
          id="zsum-pipeline.commands"
          defaultSize={30}
          minSize={15}
          className="flex flex-col overflow-hidden"
        >
          {/* Pipeline header — cost / pending / active-run / force */}
          <div className="flex shrink-0 items-center gap-3 border-t border-border bg-muted/20 px-3 py-1.5">
            <h3 className="text-xs font-semibold uppercase tracking-wide">Pipeline</h3>
            <span className="text-[11px] text-muted-foreground">
              {costPerCard != null ? `$${costPerCard.toFixed(4)} / card` : "—"}
              {pendingCount != null && ` · ${pendingCount} pending`}
              {activeRun && ` · ${activeRun.stage} · ${activeRun.status}`}
            </span>
            <label
              className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground"
              title="Append --force to forceable stages on the next run"
            >
              <input
                type="checkbox"
                checked={force}
                onChange={(e) => onForceChange(e.target.checked)}
              />
              <span>Force</span>
            </label>
          </div>

          {error && (
            <div className="border-y border-border bg-red-100 px-3 py-1.5 text-xs text-red-700">
              {error}
            </div>
          )}

          {activeRun && activeRun.status === "failed" && activeRun.last_lines.length > 0 && (
            <div className="shrink-0 border-b border-border bg-red-950 p-3 text-xs">
              <div className="mb-1 font-semibold text-red-400">Last lines</div>
              <pre className="max-h-24 overflow-y-auto text-red-300">
                {activeRun.last_lines.slice(-20).join("\n")}
              </pre>
              <button
                onClick={() => navigator.clipboard.writeText(activeRun.last_lines.join("\n"))}
                className="mt-2 rounded border border-red-700 px-2 py-1 text-red-300"
              >
                Copy logs
              </button>
            </div>
          )}

          <StagePad
            running={running}
            activeStage={activeStage}
            pendingStage={pendingStage}
            onStage={onStage}
          />
        </Panel>
      </Group>
    </div>
  );
}

// ── Collapsible header ──────────────────────────────────────────
function CollapsibleHeader({
  collapsed,
  onToggle,
  summary,
  children,
}: {
  collapsed: boolean;
  onToggle: () => void;
  summary: string;
  children: React.ReactNode;
}) {
  return (
    <div className="shrink-0 border-b border-border">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 bg-muted/20 px-3 py-1 text-left text-[11px] text-muted-foreground hover:bg-muted/40"
        title={collapsed ? "Show config / index status" : "Hide config / index status"}
      >
        <span
          className={cn(
            "inline-block transition-transform",
            collapsed ? "rotate-0" : "rotate-90",
          )}
        >
          ▸
        </span>
        <span className="font-semibold uppercase tracking-wide">Status</span>
        <span className="truncate">· {summary}</span>
      </button>
      {!collapsed && <div>{children}</div>}
    </div>
  );
}

// Persist a panel-group layout (id → size) under a localStorage key.
// Returns the layout to seed the Group with and an onLayoutChanged handler
// to wire to the same Group. Guarded against missing/blocked storage so the
// hook is safe to use outside a Tauri/browser context (e.g. jsdom tests).
function useLayoutPersistence(
  key: string,
): [Layout | undefined, (layout: Layout) => void] {
  const initial = useMemo<Layout | undefined>(() => {
    if (typeof window === "undefined") return undefined;
    try {
      const raw = window.localStorage.getItem(key);
      if (!raw) return undefined;
      const parsed = JSON.parse(raw) as unknown;
      if (
        parsed &&
        typeof parsed === "object" &&
        !Array.isArray(parsed) &&
        Object.values(parsed as Record<string, unknown>).every(
          (v) => typeof v === "number",
        )
      ) {
        return parsed as Layout;
      }
    } catch {
      /* corrupted entry — fall back to defaults */
    }
    return undefined;
  }, [key]);

  const onChange = (layout: Layout) => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(key, JSON.stringify(layout));
    } catch {
      /* localStorage blocked — keep in-memory state only */
    }
  };

  return [initial, onChange];
}

// localStorage-backed boolean — module-local so we don't pull in a hook lib.
function useLocalBoolean(
  key: string,
  fallback: boolean,
): [boolean, (updater: (prev: boolean) => boolean) => void] {
  const [val, setVal] = useState<boolean>(() => {
    if (typeof window === "undefined") return fallback;
    try {
      const raw = window.localStorage.getItem(key);
      return raw == null ? fallback : raw === "1";
    } catch {
      return fallback;
    }
  });
  const update = (updater: (prev: boolean) => boolean) =>
    setVal((prev) => {
      const next = updater(prev);
      try {
        window.localStorage.setItem(key, next ? "1" : "0");
      } catch {
        /* localStorage blocked — keep in-memory state */
      }
      return next;
    });
  return [val, update];
}

function IndexFallback({
  indexResp,
}: {
  indexResp: ReturnType<typeof getZsumIndex> extends Promise<infer T> ? T | undefined : never;
}) {
  if (!indexResp) {
    return <div className="p-4 text-xs text-muted-foreground">Loading index…</div>;
  }
  if (indexResp.kind === "ok") return null;
  return (
    <div className="flex h-full items-center justify-center p-6 text-center text-xs text-muted-foreground">
      <div>
        <div className="font-semibold">No index</div>
        <div className="mt-1">{indexResp.message}</div>
        <div className="mt-2">Run "Skeleton" or "Full Run" to generate one.</div>
      </div>
    </div>
  );
}

// ── Stage pad ───────────────────────────────────────────────────
const VARIANT_CLASS: Record<StageVariant, string> = {
  default: "border-border bg-background hover:bg-muted",
  primary: "border-foreground bg-foreground text-background hover:opacity-90",
  llm: "border-blue-500/50 bg-blue-500/10 text-blue-700 hover:bg-blue-500/20 dark:text-blue-300",
  danger: "border-red-500/50 bg-red-500/10 text-red-700 hover:bg-red-500/20",
};

function StagePad({
  running,
  activeStage,
  pendingStage,
  onStage,
}: {
  running: boolean;
  activeStage: string | null;
  pendingStage: string | null;
  onStage: (def: StageButtonDef) => void;
}) {
  const local = STAGE_BUTTONS.filter((b) => b.group === "local");
  const llm = STAGE_BUTTONS.filter((b) => b.group === "llm");
  return (
    <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-2">
      <StageSection title="Local & network">
        {local.map((b) => (
          <StageBtn
            key={b.stage}
            def={b}
            disabled={running}
            isActive={false}
            isPending={pendingStage === b.stage}
            onClick={() => onStage(b)}
          />
        ))}
      </StageSection>
      <StageSection title="LLM stages">
        {llm.map((b) => (
          <StageBtn
            key={b.stage}
            def={b}
            disabled={b.stage === "cancel" ? !running : running}
            isActive={running && activeStage === b.stage}
            isPending={pendingStage === b.stage}
            onClick={() => onStage(b)}
          />
        ))}
      </StageSection>
    </div>
  );
}

function StageSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex items-center gap-2">
      <h4 className="w-28 shrink-0 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h4>
      <div className="flex flex-wrap gap-2">{children}</div>
    </section>
  );
}

function StageBtn({
  def,
  disabled,
  isActive,
  isPending,
  onClick,
}: {
  def: StageButtonDef;
  disabled: boolean;
  isActive: boolean;
  isPending: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={def.forceable ? `${def.label} — toggle Force to add --force` : def.label}
      className={cn(
        "flex w-24 flex-col items-start gap-0.5 rounded border px-2 py-1.5 text-left text-xs transition disabled:cursor-not-allowed disabled:opacity-50",
        VARIANT_CLASS[def.variant],
        isActive && "ring-2 ring-blue-500",
      )}
    >
      <span className="flex items-center gap-1.5 font-medium">
        {(isActive || isPending) && (
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-current" />
        )}
        {def.label}
      </span>
      {def.forceable && <span className="text-[10px] text-muted-foreground">forceable</span>}
    </button>
  );
}
