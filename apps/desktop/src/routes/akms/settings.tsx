/**
 * AKMS Settings route — surfaces the AKMS workspace path plus the
 * Claude Assets pane. The workspace setting controls where
 * batches.json, prompt-library/, runs/, and node outputs live.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getConfig, postConfig } from "../../api/app";
import { getLlmProviders, reloadLlmProviders } from "../../api/llm";
import type { LearnProvider, LlmConsumer } from "../../api/llm";
import { ClaudeAssetsPane } from "../../components/akms/ClaudeAssetsPane";

export default function AkmsSettingsRoute() {
  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <AkmsWorkspaceSection />
      <LlmProviderSection />
      <div className="flex-1 min-h-0 border-t border-border">
        <ClaudeAssetsPane />
      </div>
    </div>
  );
}

function AkmsWorkspaceSection() {
  const qc = useQueryClient();
  const { data, refetch } = useQuery({ queryKey: ["app", "config"], queryFn: getConfig });

  const [draft, setDraft] = useState<string>("");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (data) setDraft(data.akms_workspace);
  }, [data]);

  const saveMut = useMutation({
    mutationFn: (value: string) => postConfig({ akms_workspace: value }),
    onSuccess: () => {
      setMessage("Saved.");
      qc.invalidateQueries();
      refetch();
    },
    onError: (e: unknown) =>
      setMessage(`Save failed: ${e instanceof Error ? e.message : String(e)}`),
  });

  if (!data) {
    return <div className="p-4 text-sm text-muted-foreground">Loading…</div>;
  }

  const dirty = draft !== data.akms_workspace;

  return (
    <section className="flex flex-col gap-2 border-b border-border bg-background/40 p-4">
      <header>
        <h2 className="text-base font-semibold">AKMS workspace</h2>
        <p className="text-xs text-muted-foreground max-w-prose">
          Logic-Loom-owned root. Holds <code className="font-mono">batches.json</code>,
          <code className="font-mono"> prompt-library/</code>,
          <code className="font-mono"> claude-runtime/</code>, run history, and
          generated nodes. Change this to point Logic-Loom at a different
          batches/runs root.
        </p>
      </header>
      <div className="flex items-center gap-2">
        <input
          className="flex-1 rounded border border-border bg-background px-2 py-1 font-mono text-xs"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button
          type="button"
          disabled={!dirty || saveMut.isPending}
          onClick={() => saveMut.mutate(draft)}
          className="rounded bg-foreground px-3 py-1 text-sm text-background disabled:opacity-40"
        >
          {saveMut.isPending ? "Saving…" : "Save"}
        </button>
      </div>
      <BatchesHint workspace={data.akms_workspace} />
      {message && <span className="text-xs text-muted-foreground">{message}</span>}
    </section>
  );
}

/** Sentinel select value for "no explicit choice" (global) / "use global" (override). */
const AUTO = "auto";
const USE_GLOBAL = "__global__";

/** Per-consumer override rows, in render order. */
const CONSUMERS: ReadonlyArray<{ key: LlmConsumer; label: string; hint: string }> = [
  { key: "learn", label: "Learn", hint: "AKMS Learn (llm_expanded)" },
  { key: "zsum", label: "zsum", hint: "Zotero Summarizer" },
  { key: "nodegen", label: "node-gen", hint: "AKMS node generation" },
];

/** A provider <select> reused by the global chooser and each override row. */
function ProviderSelect({
  value,
  onChange,
  providers,
  firstOption,
}: {
  value: string;
  onChange: (next: string) => void;
  providers: LearnProvider[];
  /** The leading "auto"/"use global" sentinel option. */
  firstOption: { value: string; label: string };
}) {
  return (
    <select
      className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value={firstOption.value}>{firstOption.label}</option>
      {providers.map((p) => (
        <option key={p.name} value={p.name}>
          {p.name} — {p.route}
          {p.available ? "" : " (unavailable)"}
        </option>
      ))}
    </select>
  );
}

/** The unavailable-provider warning banner (reused from the Learn chooser UX). */
function UnavailableBanner({ provider }: { provider: LearnProvider }) {
  return (
    <div className="rounded border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-xs text-amber-700">
      <strong>{provider.name}</strong> isn’t ready — {provider.detail}.{" "}
      {provider.route === "api"
        ? "Fill the matching API key below to proceed."
        : "Install / enable the CLI (or set its command) to proceed."}
    </div>
  );
}

function LlmProviderSection() {
  const qc = useQueryClient();
  const { data: config, refetch } = useQuery({
    queryKey: ["app", "config"],
    queryFn: getConfig,
  });
  const { data: prov } = useQuery({
    queryKey: ["llm", "providers"],
    queryFn: getLlmProviders,
  });

  // Global chooser: bound to llm_provider (falling back to the learn alias so a
  // legacy-config user still sees their existing global pick).
  const [provider, setProvider] = useState<string>(AUTO);
  // Per-consumer overrides: USE_GLOBAL sentinel = field is null/unset.
  const [overrides, setOverrides] = useState<Record<LlmConsumer, string>>({
    learn: USE_GLOBAL,
    zsum: USE_GLOBAL,
    nodegen: USE_GLOBAL,
  });
  const [keys, setKeys] = useState({
    anthropic_api_key: "",
    openai_api_key: "",
    google_api_key: "",
  });
  const [customClis, setCustomClis] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!config) return;
    setProvider(config.llm_provider ?? config.learn_llm_provider ?? AUTO);
    setOverrides({
      learn: config.learn_llm_provider ?? USE_GLOBAL,
      zsum: config.zsum_llm_provider ?? USE_GLOBAL,
      nodegen: config.nodegen_llm_provider ?? USE_GLOBAL,
    });
    setKeys({
      anthropic_api_key: config.anthropic_api_key ?? "",
      openai_api_key: config.openai_api_key ?? "",
      google_api_key: config.google_api_key ?? "",
    });
    setCustomClis(config.learn_llm_custom_clis ?? {});
  }, [config]);

  const reloadMut = useMutation({
    mutationFn: reloadLlmProviders,
    onSuccess: (data) => {
      setMessage(
        `Backend reloaded — keys live: ${data.injected.length}, providers: ${data.registered.length}. No restart needed.`,
      );
      qc.invalidateQueries({ queryKey: ["llm", "providers"] });
      qc.invalidateQueries({ queryKey: ["learn", "providers"] });
      qc.invalidateQueries({ queryKey: ["learn", "status"] });
      qc.invalidateQueries({ queryKey: ["app", "config"] });
    },
    onError: (e: unknown) =>
      setMessage(`Reload failed: ${e instanceof Error ? e.message : String(e)}`),
  });

  const saveMut = useMutation({
    mutationFn: () =>
      postConfig({
        // Empty string clears the field on the server (= auto / use-global).
        llm_provider: provider === AUTO ? "" : provider,
        learn_llm_provider:
          overrides.learn === USE_GLOBAL ? "" : overrides.learn,
        zsum_llm_provider: overrides.zsum === USE_GLOBAL ? "" : overrides.zsum,
        nodegen_llm_provider:
          overrides.nodegen === USE_GLOBAL ? "" : overrides.nodegen,
        anthropic_api_key: keys.anthropic_api_key,
        openai_api_key: keys.openai_api_key,
        google_api_key: keys.google_api_key,
        learn_llm_custom_clis: customClis,
      }),
    // Persist, then re-apply wiring in-process so the new keys/CLIs/choices are
    // live without an app restart (the reload mutation sets the final message).
    onSuccess: () => {
      qc.invalidateQueries();
      refetch();
      reloadMut.mutate();
    },
    onError: (e: unknown) =>
      setMessage(`Save failed: ${e instanceof Error ? e.message : String(e)}`),
  });

  if (!config) return null;

  const providers = prov?.providers ?? [];
  const effective = prov?.effective;
  const byName = (name: string | null | undefined) =>
    name ? providers.find((x) => x.name === name) : undefined;

  // Collect the distinct chosen-but-unavailable providers across the global
  // chooser + every override, so the same banner UX covers all of them.
  const chosenNames = new Set<string>();
  if (provider !== AUTO) chosenNames.add(provider);
  for (const v of Object.values(overrides)) {
    if (v !== USE_GLOBAL) chosenNames.add(v);
  }
  const unavailable = [...chosenNames]
    .map((n) => byName(n))
    .filter((p): p is LearnProvider => !!p && !p.available);

  const busy = saveMut.isPending || reloadMut.isPending;
  const dirty =
    (config.llm_provider ?? config.learn_llm_provider ?? AUTO) !== provider ||
    (config.learn_llm_provider ?? USE_GLOBAL) !== overrides.learn ||
    (config.zsum_llm_provider ?? USE_GLOBAL) !== overrides.zsum ||
    (config.nodegen_llm_provider ?? USE_GLOBAL) !== overrides.nodegen ||
    (config.anthropic_api_key ?? "") !== keys.anthropic_api_key ||
    (config.openai_api_key ?? "") !== keys.openai_api_key ||
    (config.google_api_key ?? "") !== keys.google_api_key ||
    JSON.stringify(config.learn_llm_custom_clis ?? {}) !==
      JSON.stringify(customClis);

  return (
    <section className="flex flex-col gap-3 border-b border-border bg-background/40 p-4">
      <header>
        <h2 className="text-base font-semibold">LLM provider</h2>
        <p className="max-w-prose text-xs text-muted-foreground">
          The global host-LLM provider for every consumer (Learn, zsum,
          node-gen). With an API key set, the API route (LiteLLM) is used;
          otherwise pick an installed agent CLI (
          <code className="font-mono">claude</code>,{" "}
          <code className="font-mono">codex</code>) or a custom command. CLI
          agents activate only when explicitly chosen. Each consumer can
          override the global below; an override defaults to{" "}
          <em>use global</em>.
        </p>
      </header>

      <label className="flex items-center gap-2 text-sm">
        <span className="w-28 text-xs text-muted-foreground">
          Global provider
        </span>
        <ProviderSelect
          value={provider}
          onChange={setProvider}
          providers={providers}
          firstOption={{ value: AUTO, label: "Auto (nlm / API key)" }}
        />
      </label>

      <div className="flex flex-col gap-2">
        <span className="text-xs text-muted-foreground">
          Per-consumer overrides (each defaults to “use global”):
        </span>
        {CONSUMERS.map(({ key, label, hint }) => (
          <label key={key} className="flex items-center gap-2 text-sm">
            <span
              className="w-28 text-xs text-muted-foreground"
              title={hint}
              aria-label={`${label} override`}
            >
              {label}
            </span>
            <ProviderSelect
              value={overrides[key]}
              onChange={(next) =>
                setOverrides((prev) => ({ ...prev, [key]: next }))
              }
              providers={providers}
              firstOption={{ value: USE_GLOBAL, label: "Use global" }}
            />
            <span className="w-40 shrink-0 text-[11px] text-muted-foreground">
              effective:{" "}
              <code className="font-mono">
                {effective?.[key] ?? "auto"}
              </code>
            </span>
          </label>
        ))}
      </div>

      <ul className="flex flex-col gap-0.5 text-[11px] text-muted-foreground">
        {providers.map((p) => (
          <li key={p.name} className="flex items-center gap-2">
            <span className={p.available ? "text-emerald-600" : "text-red-600"}>
              {p.available ? "●" : "○"}
            </span>
            <span className="font-mono">{p.name}</span>
            <span>[{p.route}]</span>
            <span>{p.detail}</span>
          </li>
        ))}
      </ul>

      {unavailable.map((p) => (
        <UnavailableBanner key={p.name} provider={p} />
      ))}

      <div className="flex flex-col gap-2">
        {(
          [
            ["anthropic_api_key", "Anthropic key"],
            ["openai_api_key", "OpenAI key"],
            ["google_api_key", "Google key"],
          ] as const
        ).map(([field, label]) => (
          <label key={field} className="flex items-center gap-2 text-sm">
            <span className="w-28 text-xs text-muted-foreground">{label}</span>
            <input
              type="password"
              autoComplete="off"
              placeholder="unset → CLI route"
              className="flex-1 rounded border border-border bg-background px-2 py-1 font-mono text-xs"
              value={keys[field]}
              // A saved key comes back as a mask; select it on focus so typing
              // replaces it instead of appending to it.
              onFocus={(e) => e.currentTarget.select()}
              onChange={(e) => setKeys({ ...keys, [field]: e.target.value })}
            />
          </label>
        ))}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs text-muted-foreground">
          Custom CLI commands (<code className="font-mono">{"{prompt}"}</code>{" "}
          arg-substituted, else piped to stdin):
        </span>
        {Object.entries(customClis).map(([label, cmd]) => (
          <label key={label} className="flex items-center gap-2 text-sm">
            <span className="w-28 font-mono text-xs">{label}</span>
            <input
              className="flex-1 rounded border border-border bg-background px-2 py-1 font-mono text-xs"
              placeholder="e.g. mytool chat --input {prompt}"
              value={cmd}
              onChange={(e) =>
                setCustomClis({ ...customClis, [label]: e.target.value })
              }
            />
          </label>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!dirty || busy}
          onClick={() => saveMut.mutate()}
          className="rounded bg-foreground px-3 py-1 text-sm text-background disabled:opacity-40"
        >
          {saveMut.isPending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => reloadMut.mutate()}
          title="Re-apply keys + CLI providers in-process (no app restart)"
          className="rounded border border-border px-3 py-1 text-sm disabled:opacity-40"
        >
          {reloadMut.isPending ? "Reloading…" : "Reload backend"}
        </button>
        {message && (
          <span className="text-xs text-muted-foreground">{message}</span>
        )}
      </div>
    </section>
  );
}

function BatchesHint({ workspace }: { workspace: string }) {
  const root = workspace.replace(/\/$/, "");
  return (
    <p className="text-[11px] text-muted-foreground">
      Batches live at <code className="font-mono">{root}/batches.json</code>;
      runs under <code className="font-mono">{root}/runs/</code>.
    </p>
  );
}

