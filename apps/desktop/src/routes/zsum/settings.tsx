import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { useSearchParams } from "react-router-dom";

import {
  getConfig,
  pathExists,
  postConfig,
  testZsumEnvironment,
  type AppConfig,
  type ConfigUpdate,
  type ZsumTestResult,
} from "../../api/app";
import { ApiError } from "../../api/client";
import { postConfigDoc } from "../../api/zsum";
import { ConfigEditor } from "../../components/zsum/ConfigEditor";
import { ZSUM_CONFIG_NAV_GROUPS } from "../../zsum/config-nav";

type SettingsTabId = "logic-loom" | (typeof ZSUM_CONFIG_NAV_GROUPS)[number]["id"];

const SETTINGS_TABS: readonly {
  id: SettingsTabId;
  label: string;
  kind: "logic-loom" | "zsum";
  sectionIds?: readonly string[];
}[] = [
  { id: "logic-loom", label: "LogicLoom", kind: "logic-loom" },
  ...ZSUM_CONFIG_NAV_GROUPS.map((group) => ({
    id: group.id,
    label: group.label,
    kind: "zsum" as const,
    sectionIds: group.sectionIds,
  })),
];

const SETTINGS_TAB_IDS = new Set<SettingsTabId>(SETTINGS_TABS.map((tab) => tab.id));

function normalizeSettingsTab(value: string | null): SettingsTabId {
  return SETTINGS_TAB_IDS.has(value as SettingsTabId) ? (value as SettingsTabId) : "logic-loom";
}

export default function SettingsRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState<SettingsTabId>(() =>
    normalizeSettingsTab(searchParams.get("tab")),
  );

  useEffect(() => {
    const nextTab = normalizeSettingsTab(searchParams.get("tab"));
    if (nextTab !== activeTab) setActiveTab(nextTab);
  }, [activeTab, searchParams]);

  const activeTabDef =
    SETTINGS_TABS.find((tab) => tab.id === activeTab) ?? SETTINGS_TABS[0];

  const selectTab = (tabId: SettingsTabId) => {
    setActiveTab(tabId);
    const nextParams = new URLSearchParams(searchParams);
    if (tabId === "logic-loom") {
      nextParams.delete("tab");
    } else {
      nextParams.set("tab", tabId);
    }
    setSearchParams(nextParams, { replace: true });
  };

  return (
    <div className="h-full overflow-hidden">
      <div className="grid h-full grid-cols-[220px_1fr]">
        <aside className="flex h-full flex-col border-r border-border bg-background/40">
          <div className="border-b border-border px-4 py-4">
            <h1 className="text-sm font-semibold">Settings</h1>
            <p className="mt-1 text-xs text-muted-foreground">
              LogicLoom platform settings plus the full zotero-summarizer config groups.
            </p>
          </div>
          <nav className="flex-1 overflow-y-auto p-2">
            {SETTINGS_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => selectTab(tab.id)}
                className={`mb-1 block w-full rounded px-3 py-2 text-left text-sm ${
                  activeTab === tab.id
                    ? "bg-muted font-semibold"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </aside>

        <section className="h-full overflow-hidden">
          {activeTab === "logic-loom" ? (
            <LogicLoomSettingsPanel />
          ) : (
            <ConfigEditor
              title={activeTabDef.label}
              navigationMode="none"
              visibleSectionIds={activeTabDef.sectionIds}
            />
          )}
        </section>
      </div>
    </div>
  );
}

/**
 * Wrap a Tauri file/directory picker so callers stay declarative.
 * Returns null when the user cancels, or a single absolute path string.
 *
 * Note: ``openDialog()`` works only inside the Tauri shell; in a plain
 * browser dev preview the call rejects, which we surface as a no-op so
 * the page keeps functioning (the user can still type the path manually).
 */
async function pickPath(opts: {
  directory?: boolean;
  defaultPath?: string;
  title?: string;
  yaml?: boolean;
}): Promise<string | null> {
  try {
    const sel = await openDialog({
      directory: !!opts.directory,
      multiple: false,
      defaultPath: opts.defaultPath || undefined,
      title: opts.title,
      filters: opts.yaml
        ? [{ name: "YAML", extensions: ["yaml", "yml"] }]
        : undefined,
    });
    if (typeof sel === "string") return sel;
    return null;
  } catch {
    return null;
  }
}

/**
 * Settings — visually mirrors the Obsidian "Zotero Summarizer" plugin's
 * settings drawer (see plugin_dev/src/view.ts buildDrawerCommandBlock /
 * buildDrawerCostBlock):
 *
 *   ┌─ <Block title> ──────────────────────────┐
 *   │ <label>          [input              ]   │
 *   │ <label>          [input              ]   │
 *   │ ...                                       │
 *   └───────────────────────────────────────────┘
 *
 * Horizontal label/input rows scan much faster than stacked labels.
 * The page itself is the scroll container — fixes the "can't reach
 * fields below tokens-per-card" bug.
 */
function LogicLoomSettingsPanel() {
  const qc = useQueryClient();
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["app", "config"],
    queryFn: getConfig,
  });

  const [draft, setDraft] = useState<AppConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (data && !draft) setDraft(data);
  }, [data, draft]);

  if (isLoading || !draft) {
    return (
      <div className="h-full overflow-y-auto p-6 text-sm text-muted-foreground">
        Loading settings…
      </div>
    );
  }

  const dirty = !!data && JSON.stringify(data) !== JSON.stringify(draft);

  const onSave = async () => {
    if (!draft) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const patch: ConfigUpdate = {
        // Empty string clears an optional checkout override on the server.
        akms_repo: draft.akms_repo ?? "",
        akms_global_vault: draft.akms_global_vault ?? "",
        zsum_repo: draft.zsum_repo ?? "",
        vault_path: draft.vault_path ?? "",
        search_backend: draft.search_backend,
        vault_qmd_collection: draft.vault_qmd_collection,
        zsum_config_path: draft.zsum_config_path ?? "",
        zsum_python_cmd: draft.zsum_python_cmd,
        zsum_cost_per_mtoken_in: draft.zsum_cost_per_mtoken_in,
        zsum_cost_per_mtoken_out: draft.zsum_cost_per_mtoken_out,
        zsum_tokens_per_card_in: draft.zsum_tokens_per_card_in,
        zsum_tokens_per_card_out: draft.zsum_tokens_per_card_out,
        nodes_folders: draft.nodes_folders,
        nodes_editor: draft.nodes_editor ?? "",
      };
      await postConfig(patch);
      setMessage("Saved.");
      await refetch();
      qc.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const onRevert = () => {
    if (data) setDraft(data);
    setMessage(null);
    setError(null);
  };

  const update = <K extends keyof AppConfig>(key: K, value: AppConfig[K]) =>
    setDraft({ ...draft, [key]: value });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-2xl flex-col gap-5 p-6 pb-24">
        <h2 className="text-xl font-semibold">LogicLoom</h2>
        <p className="text-sm text-muted-foreground">
          App-level paths, search, nodes review, and command wiring for the desktop shell.
        </p>

        {/* ── LogicLoom platform settings (LogicLoom has more concerns than the
             Obsidian plugin's drawer; keep them in their own block.) ───── */}
        <Block title="LogicLoom">
          <TextRow
            label="LogicLoom root"
            value={draft.logic_loom_root ?? ""}
            placeholder="Not set (developer override)"
            readOnly
          />
          <TextRow
            label="AKMS checkout"
            value={draft.akms_repo ?? ""}
            onChange={(v) => update("akms_repo", v || null)}
            placeholder="Optional: AKMS source checkout (developers)"
            browse={{ kind: "directory", title: "Choose AKMS source checkout" }}
          />
          <div className="ml-44 -mt-1 pl-3 text-[11px] text-muted-foreground">
            Not needed: AKMS runs from the installed app. Set it only to use a development
            checkout (its legacy plan, <code>Packages/Nodes_Vault</code>, local graph nodes and
            developer scripts).
          </div>
          <TextRow
            label="AKMS graph vault"
            value={draft.akms_global_vault ?? ""}
            onChange={(v) => update("akms_global_vault", v || null)}
            placeholder={draft.akms_global_vault_effective}
            browse={{ kind: "directory", title: "Choose AKMS graph source vault" }}
          />
          <div className="ml-44 -mt-1 pl-3 text-[11px] text-muted-foreground">
            Used by the Graph tab. Leave unset to auto-resolve via the AKMS checkout's
            <code>Packages/Nodes_Vault</code> (if set), <code>AKMS_GLOBAL_VAULT</code>, or the AKMS
            default.
          </div>
          <TextRow
            label="Vault path"
            value={draft.vault_path ?? ""}
            onChange={(v) => update("vault_path", v || null)}
            placeholder="/path/to/your/Obsidian/vault"
            browse={{ kind: "directory", title: "Choose Obsidian vault" }}
          />
          <div className="ml-44 -mt-1 pl-3 text-[11px] text-muted-foreground">
            Used by the LogicLoom Vault tab only. zsum publishes to{" "}
            <code>vault_output</code> inside <code>zsum.config.yaml</code> — those should usually
            match. The Pipeline tab warns when they don't.
          </div>
          <TextRow label="Cache path" value={draft.cache_path} readOnly />
          <TextRow label="Graph cache" value={draft.graph_cache_path} readOnly />
          <SelectRow
            label="Vault search backend"
            value={draft.search_backend}
            options={[
              { value: "ripgrep", label: "ripgrep" },
              { value: "qmd", label: "qmd" },
            ]}
            onChange={(v) => update("search_backend", v as AppConfig["search_backend"])}
          />
          {draft.search_backend === "qmd" && (
            <TextRow
              label="Vault QMD collection"
              value={draft.vault_qmd_collection ?? ""}
              onChange={(v) => update("vault_qmd_collection", v || null)}
            />
          )}
        </Block>

        {/* ── Nodes Review ─────────────────────────────────────────── */}
        <NodesBlock draft={draft} update={update} />

        {/* ── Command (mirrors zsum-setting-block "Command") ─────────── */}
        <CommandBlock draft={draft} saved={data ?? null} update={update} />

        {/* ── Cost estimation (mirrors zsum-setting-block "Cost estimation") */}
        <CostBlock draft={draft} update={update} />

        {/* ── Save / Revert footer ──────────────────────────────────── */}
        <div className="sticky bottom-0 -mx-6 flex items-center gap-2 border-t border-border bg-background/95 px-6 py-3 text-sm backdrop-blur">
          <button
            onClick={onSave}
            disabled={!dirty || saving}
            className="rounded bg-foreground px-3 py-1 text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <button
            onClick={onRevert}
            disabled={!dirty || saving}
            className="rounded border border-border px-3 py-1 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
          >
            Revert
          </button>
          {dirty && <span className="text-xs text-amber-600">Unsaved changes</span>}
          {message && <span className="text-xs text-emerald-700">{message}</span>}
          {error && <span className="text-xs text-red-600">{error}</span>}
        </div>
      </div>
    </div>
  );
}

// ── Command block ────────────────────────────────────────────
function CommandBlock({
  draft,
  saved,
  update,
}: {
  draft: AppConfig;
  saved: AppConfig | null;
  update: <K extends keyof AppConfig>(key: K, value: AppConfig[K]) => void;
}) {
  const qc = useQueryClient();
  // Same "warn if config file missing" behavior as the Obsidian plugin.
  const effective = draft.zsum_config_path?.trim() || draft.zsum_config_path_effective;
  const probe = useQuery({
    queryKey: ["app", "path-exists", effective],
    queryFn: () => pathExists(effective),
    enabled: !!effective,
    staleTime: 2_000,
  });
  const missing = probe.data && !probe.data.exists ? probe.data.path : null;

  // "Create it" writes a starter zsum.config.yaml at the saved effective path
  // (the config-doc endpoint always targets that one), so it is offered only
  // while the Config path field has no unsaved edit.
  const pathUnsaved =
    (draft.zsum_config_path ?? "").trim() !== (saved?.zsum_config_path ?? "").trim();
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const createConfig = async () => {
    setCreating(true);
    setCreateError(null);
    try {
      await postConfigDoc([
        { path: "vault_output", value: saved?.vault_path || "./vault" },
        { path: "cache_dir", value: "./cache" },
      ]);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["app", "path-exists"] }),
        qc.invalidateQueries({ queryKey: ["zsum"] }),
      ]);
    } catch (e) {
      setCreateError((e as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ZsumTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    setTestError(null);
    try {
      setResult(await testZsumEnvironment());
    } catch (e) {
      // A failed run is a 502 whose envelope detail holds the run's output.
      const detail = (e as ApiError).detail as { error?: { detail?: ZsumTestResult } } | null;
      if (e instanceof ApiError && detail?.error?.detail?.exit_code !== undefined) {
        setResult(detail.error.detail);
      } else {
        setTestError((e as Error).message);
      }
    } finally {
      setTesting(false);
    }
  };

  return (
    <Block title="Command">
      <TextRow
        label="Project root"
        value={draft.zsum_repo ?? ""}
        onChange={(v) => update("zsum_repo", v || null)}
        placeholder="Optional: zotero-summarizer checkout (developers)"
        browse={{ kind: "directory", title: "Choose zotero-summarizer checkout" }}
      />
      <div className="ml-44 -mt-1 pl-3 text-[11px] text-muted-foreground">
        Not needed: zsum runs from the installed app. When unset, runs start in the config
        file&apos;s folder.
      </div>
      <TextRow
        label="Config path"
        value={draft.zsum_config_path ?? ""}
        onChange={(v) => update("zsum_config_path", v || null)}
        placeholder={draft.zsum_config_path_effective}
        warn={missing ? `⚠ File not found: ${missing}` : null}
        browse={{
          kind: "file",
          yaml: true,
          title: "Choose zsum.config.yaml",
        }}
      />
      {missing && (
        <div className="ml-44 -mt-1 flex flex-wrap items-center gap-2 pl-3 text-[11px] text-muted-foreground">
          <span>Pick an existing zsum.config.yaml with Browse, or</span>
          <button
            type="button"
            onClick={() => void createConfig()}
            disabled={creating || pathUnsaved}
            title={pathUnsaved ? "Save settings first, then create the file at the new path." : undefined}
            className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="zsum-create-config"
          >
            {creating ? "Creating…" : "Create a starter config here"}
          </button>
          {pathUnsaved && <span>(save settings first)</span>}
          {createError && <span className="text-red-600">{createError}</span>}
        </div>
      )}
      <TextRow
        label="Python command"
        value={draft.zsum_python_cmd}
        onChange={(v) => update("zsum_python_cmd", v)}
        placeholder="uv run zsum"
      />
      <div className="flex justify-end">
        <button
          onClick={runTest}
          disabled={testing}
          className="rounded bg-foreground px-3 py-1 text-xs text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {testing ? "Running…" : "Test command"}
        </button>
      </div>
      {testError && (
        <pre className="max-h-64 overflow-auto rounded border border-border bg-muted/40 p-2 font-mono text-[11px] text-red-600 whitespace-pre-wrap">
          {testError}
        </pre>
      )}
      {result && (
        <pre className="max-h-64 overflow-auto rounded border border-border bg-muted/40 p-2 font-mono text-[11px] whitespace-pre-wrap">
          {(() => {
            const head =
              result.exit_code === 0
                ? "zsum stats — OK"
                : `zsum stats — exit ${result.exit_code}`;
            const body =
              [result.stdout, result.stderr].filter(Boolean).join("\n").trim() ||
              "(no output)";
            return `${head}\n${body}`;
          })()}
        </pre>
      )}
    </Block>
  );
}

// ── Cost estimation block ─────────────────────────────────────
function CostBlock({
  draft,
  update,
}: {
  draft: AppConfig;
  update: <K extends keyof AppConfig>(key: K, value: AppConfig[K]) => void;
}) {
  // Live "$/card" preview — same formula as the Obsidian plugin (cost.ts).
  const perCard = useMemo(
    () =>
      (draft.zsum_tokens_per_card_in * draft.zsum_cost_per_mtoken_in) / 1e6 +
      (draft.zsum_tokens_per_card_out * draft.zsum_cost_per_mtoken_out) / 1e6,
    [
      draft.zsum_cost_per_mtoken_in,
      draft.zsum_cost_per_mtoken_out,
      draft.zsum_tokens_per_card_in,
      draft.zsum_tokens_per_card_out,
    ],
  );

  return (
    <Block title="Cost estimation">
      <NumberRow
        label="Input $/M tokens"
        value={draft.zsum_cost_per_mtoken_in}
        step={0.01}
        onChange={(n) => update("zsum_cost_per_mtoken_in", n)}
      />
      <NumberRow
        label="Output $/M tokens"
        value={draft.zsum_cost_per_mtoken_out}
        step={0.01}
        onChange={(n) => update("zsum_cost_per_mtoken_out", n)}
      />
      <NumberRow
        label="Tokens per card (in)"
        value={draft.zsum_tokens_per_card_in}
        step={1}
        onChange={(n) => update("zsum_tokens_per_card_in", n)}
      />
      <NumberRow
        label="Tokens per card (out)"
        value={draft.zsum_tokens_per_card_out}
        step={1}
        onChange={(n) => update("zsum_tokens_per_card_out", n)}
      />
      <div className="pt-1 text-xs text-muted-foreground">
        Current estimate: ${perCard.toFixed(4)} per card
      </div>
    </Block>
  );
}

// ── Block / row primitives — mirror the .zsum-setting-block /
//   .zsum-setting-row CSS in plugin_dev/styles.css. ─────────────
function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded border border-border bg-background/40">
      <header className="border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </header>
      <div className="flex flex-col gap-2 p-3">{children}</div>
    </section>
  );
}

/**
 * Horizontal row: fixed-width label on the left, flex input on the right.
 * Mirrors `.zsum-setting-row { display:flex; align-items:center; gap:8px }`.
 *
 * When ``browse`` is set, also renders a "Browse…" button that opens a
 * native Tauri picker (file or directory) and writes the chosen path back
 * via ``onChange``. Cancellation is a no-op.
 */
function TextRow({
  label,
  value,
  onChange,
  readOnly,
  placeholder,
  warn,
  browse,
}: {
  label: string;
  value: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
  placeholder?: string;
  warn?: string | null;
  browse?: { kind: "file" | "directory"; yaml?: boolean; title?: string };
}) {
  const onBrowse = async () => {
    if (!browse || !onChange) return;
    const picked = await pickPath({
      directory: browse.kind === "directory",
      defaultPath: value || undefined,
      title: browse.title ?? `Choose ${label.toLowerCase()}`,
      yaml: browse.yaml,
    });
    if (picked) onChange(picked);
  };
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3">
        <label className="w-44 shrink-0 text-sm">{label}</label>
        <input
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          readOnly={readOnly}
          placeholder={placeholder}
          className="flex-1 rounded border border-border bg-background px-2 py-1 font-mono text-xs read-only:opacity-60"
        />
        {browse && !readOnly && (
          <button
            type="button"
            onClick={onBrowse}
            className="shrink-0 rounded border border-border px-2 py-1 text-xs hover:bg-muted"
            title={browse.kind === "file" ? "Choose a file" : "Choose a directory"}
          >
            Browse…
          </button>
        )}
      </div>
      {warn && <div className="ml-44 pl-3 text-xs text-amber-600">{warn}</div>}
    </div>
  );
}

function NumberRow({
  label,
  value,
  step,
  onChange,
}: {
  label: string;
  value: number;
  step: number;
  onChange: (n: number) => void;
}) {
  // Local string buffer — lets the user type partial values like "0." or
  // clear the field without us re-formatting from the parsed number mid-edit.
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return (
    <div className="flex items-center gap-3">
      <label className="w-44 shrink-0 text-sm">{label}</label>
      <input
        type="number"
        step={step}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const n = parseFloat(e.target.value);
          if (!Number.isNaN(n)) onChange(n);
        }}
        className="flex-1 rounded border border-border bg-background px-2 py-1 font-mono text-xs"
      />
    </div>
  );
}

function SelectRow({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <label className="w-44 shrink-0 text-sm">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

// ── Nodes Review block ────────────────────────────────────────
function NodesBlock({
  draft,
  update,
}: {
  draft: AppConfig;
  update: <K extends keyof AppConfig>(key: K, value: AppConfig[K]) => void;
}) {
  return (
    <Block title="Nodes Review">
      <FoldersRow
        label="Node folders"
        folders={draft.nodes_folders}
        onChange={(v) => update("nodes_folders", v)}
      />
      <TextRow
        label="Editor command"
        value={draft.nodes_editor ?? ""}
        onChange={(v) => update("nodes_editor", v || null)}
        placeholder="code  (empty = system default)"
      />
    </Block>
  );
}

/**
 * Multi-path list row: shows each configured folder with a Remove button,
 * plus an "Add Folder…" button that opens a native directory picker.
 */
function FoldersRow({
  label,
  folders,
  onChange,
}: {
  label: string;
  folders: string[];
  onChange: (folders: string[]) => void;
}) {
  const addFolder = async () => {
    const picked = await pickPath({ directory: true, title: "Choose nodes folder" });
    if (picked && !folders.includes(picked)) onChange([...folders, picked]);
  };

  return (
    <div className="flex gap-3">
      <label className="w-44 shrink-0 pt-1 text-sm">{label}</label>
      <div className="flex flex-1 flex-col gap-1">
        {folders.length === 0 && (
          <div className="text-xs italic text-muted-foreground">No folders configured</div>
        )}
        {folders.map((f, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="flex-1 truncate rounded border border-border bg-background px-2 py-1 font-mono text-xs">
              {f}
            </span>
            <button
              type="button"
              onClick={() => onChange(folders.filter((_, j) => j !== i))}
              className="shrink-0 rounded border border-border px-2 py-1 text-xs hover:border-red-300 hover:bg-red-50 hover:text-red-600"
            >
              Remove
            </button>
          </div>
        ))}
        <div>
          <button
            type="button"
            onClick={addFolder}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted"
          >
            Add Folder…
          </button>
        </div>
      </div>
    </div>
  );
}
