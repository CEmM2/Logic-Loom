/**
 * Effective-config banner — answers "what is zsum actually doing?" with
 * three concrete paths (config / vault_output / index.json) and a colored
 * status row. Detects two flavors of misconfiguration:
 *
 *   1. LogicLoom's `vault_path` setting disagrees with zsum's `vault_output`
 *      (so the Vault browser shows one tree while zsum publishes to
 *      another — exactly the bug that produced "publish wrote to the
 *      wrong directory").
 *   2. A more specific `<vault_path>/zsum.config.yaml` exists but LogicLoom
 *      is using a different one. Offers a one-click switch.
 *
 * Mounted at the top of the Pipeline Library panel — out of the way when
 * everything matches, loud when it doesn't.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { postConfig } from "../../api/app";
import { getEffectiveConfig } from "../../api/zsum";
import { cn } from "../../lib/utils";

export function EffectiveConfigBanner() {
  const qc = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const eff = useQuery({
    queryKey: ["zsum", "effective-config"],
    queryFn: getEffectiveConfig,
    refetchInterval: 30_000,
  });

  const syncVault = useMutation({
    mutationFn: (vaultOutput: string) => postConfig({ vault_path: vaultOutput }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["app", "config"] });
      qc.invalidateQueries({ queryKey: ["zsum", "effective-config"] });
      qc.invalidateQueries({ queryKey: ["zsum", "index"] });
    },
  });

  const useVaultConfig = useMutation({
    mutationFn: (path: string) => postConfig({ zsum_config_path: path }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["app", "config"] });
      qc.invalidateQueries({ queryKey: ["zsum", "effective-config"] });
      qc.invalidateQueries({ queryKey: ["zsum", "index"] });
      qc.invalidateQueries({ queryKey: ["zsum", "config-doc"] });
    },
  });

  if (!eff.data) return null;
  const e = eff.data;

  // Severity: red if config doesn't exist, amber if any mismatch, else neutral.
  const hasError = !e.config_exists;
  const hasWarn = e.vault_mismatch || !!e.vault_candidate || !e.index_exists;
  const tone = hasError ? "err" : hasWarn ? "warn" : "ok";

  const toneClass = {
    err: "border-red-500/40 bg-red-500/10",
    warn: "border-amber-500/40 bg-amber-500/10",
    ok: "border-border bg-muted/20",
  }[tone];

  return (
    <div className={cn("border-b px-3 py-1.5 text-xs", toneClass)}>
      <div className="flex items-start gap-3">
        <div className="flex-1 space-y-0.5">
          <Row
            label="zsum config"
            value={e.config_path}
            note={e.config_source === "default" ? "default fallback" : "override"}
            missing={!e.config_exists}
          />
          <Row
            label="will publish to"
            value={e.vault_output_resolved ?? "—"}
            note={e.vault_output_raw ? `raw: ${e.vault_output_raw}` : undefined}
          />
          <Row
            label="reads index from"
            value={e.index_json_path}
            missing={!e.index_exists}
          />
          {e.logic_loom_vault_path && (
            <Row
              label="LogicLoom vault_path"
              value={e.logic_loom_vault_path}
              note={e.vault_mismatch ? "⚠ does not match vault_output" : undefined}
            />
          )}
        </div>
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="shrink-0 rounded border border-border px-2 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
        >
          {collapsed ? "Show" : "Hide"}
        </button>
      </div>

      {!collapsed && (hasError || hasWarn) && (
        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-border/50 pt-2">
          {!e.config_exists && (
            <Hint tone="err">
              Config file doesn't exist on disk. Set "Config path" in Settings or create the file.
            </Hint>
          )}
          {!e.index_exists && e.config_exists && (
            <Hint tone="warn">
              No index.json yet at the resolved cache_dir. Run "Skeleton" or "Full Run".
            </Hint>
          )}
          {e.vault_mismatch && e.vault_output_resolved && (
            <>
              <Hint tone="warn">
                LogicLoom's <code>vault_path</code> doesn't match zsum's <code>vault_output</code>. The
                Vault tab and the Pipeline output are pointing at different folders.
              </Hint>
              <button
                onClick={() => syncVault.mutate(e.vault_output_resolved!)}
                disabled={syncVault.isPending}
                className="rounded bg-foreground px-2 py-1 text-[11px] text-background hover:opacity-90 disabled:opacity-50"
              >
                {syncVault.isPending
                  ? "Syncing…"
                  : "Set LogicLoom vault_path = zsum vault_output"}
              </button>
            </>
          )}
          {e.vault_candidate && (
            <>
              <Hint tone="warn">
                Found a vault-local config at <code>{e.vault_candidate}</code> — likely the one your
                Obsidian plugin used.
              </Hint>
              <button
                onClick={() => useVaultConfig.mutate(e.vault_candidate!)}
                disabled={useVaultConfig.isPending}
                className="rounded border border-border bg-background px-2 py-1 text-[11px] hover:bg-muted disabled:opacity-50"
              >
                {useVaultConfig.isPending ? "Switching…" : "Use vault zsum.config.yaml"}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  note,
  missing,
}: {
  label: string;
  value: string;
  note?: string;
  missing?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="w-32 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "flex-1 truncate font-mono text-[11px]",
          missing && "text-red-600 line-through",
        )}
        title={value}
      >
        {value}
      </span>
      {note && <span className="text-[10px] text-muted-foreground">({note})</span>}
    </div>
  );
}

function Hint({ tone, children }: { tone: "warn" | "err"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "rounded px-2 py-1 text-[11px] leading-relaxed",
        tone === "err"
          ? "bg-red-500/15 text-red-700 dark:text-red-300"
          : "bg-amber-500/15 text-amber-700 dark:text-amber-300",
      )}
    >
      {children}
    </span>
  );
}
