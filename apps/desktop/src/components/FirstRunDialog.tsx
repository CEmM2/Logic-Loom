import { useEffect, useState } from "react";

import { getFirstRunStatus, saveFirstRun, type FirstRunStatus } from "../api/app";

interface Props {
  status: FirstRunStatus;
  onComplete: () => void;
}

export function FirstRunDialog({ status, onComplete }: Props) {
  const [akms, setAkms] = useState(status.detected.akms_repo ?? status.configured.akms_repo ?? "");
  const [akmsGlobalVault, setAkmsGlobalVault] = useState(
    status.detected.akms_global_vault ?? status.configured.akms_global_vault ?? "",
  );
  const [zsum, setZsum] = useState(status.detected.zsum_repo ?? status.configured.zsum_repo ?? "");
  const [vault, setVault] = useState(
    status.detected.vault_path ?? status.configured.vault_path ?? "",
  );
  const [backend, setBackend] = useState<"ripgrep" | "qmd">("ripgrep");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSave = async () => {
    setSaving(true);
    setError(null);
    try {
      // Source checkouts are optional developer overrides: blank = unset.
      await saveFirstRun({
        akms_repo: akms || undefined,
        akms_global_vault: akmsGlobalVault || undefined,
        zsum_repo: zsum || undefined,
        vault_path: vault,
        search_backend: backend,
      });
      onComplete();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[36rem] rounded border border-border bg-background p-6 shadow-lg">
        <h2 className="text-lg font-semibold">Welcome to LogicLoom</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Confirm the paths LogicLoom should use. Only the vault is required; AKMS and
          zotero-summarizer run from the installed app, so their source checkouts are optional
          developer overrides. Detected values are pre-filled — adjust if needed. These are
          written to <span className="font-mono text-xs">{status.config_path}</span>.
        </p>

        <div className="mt-4 flex flex-col gap-3 text-sm">
          <Field
            label="AKMS source checkout (optional, developers)"
            value={akms}
            onChange={setAkms}
            placeholder="Optional: /path/to/AKMS"
          />
          <Field
            label="AKMS graph vault"
            value={akmsGlobalVault}
            onChange={setAkmsGlobalVault}
            placeholder="Optional: /path/to/Nodes_Vault"
          />
          <Field
            label="zotero-summarizer source checkout (optional, developers)"
            value={zsum}
            onChange={setZsum}
            placeholder="Optional: /path/to/zotero-summarizer"
          />
          <Field label="Vault path" value={vault} onChange={setVault} placeholder="/path/to/vault" />
          <label className="flex items-center justify-between gap-2">
            <span>Vault search backend</span>
            <select
              value={backend}
              onChange={(e) => setBackend(e.target.value as "ripgrep" | "qmd")}
              className="rounded border border-border bg-background px-2 py-1"
            >
              <option value="ripgrep">ripgrep</option>
              <option value="qmd">qmd</option>
            </select>
          </label>
        </div>

        {error && (
          <p className="mt-3 text-xs text-red-600">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2 text-sm">
          <button
            onClick={onSave}
            disabled={saving || !vault}
            className="rounded bg-foreground px-3 py-1 text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save & continue"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span>{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="rounded border border-border bg-background px-2 py-1 font-mono text-xs"
      />
    </label>
  );
}

export function useFirstRunGate() {
  const [status, setStatus] = useState<FirstRunStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    try {
      const s = await getFirstRunStatus();
      setStatus(s);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  return { status, loading, refresh };
}
