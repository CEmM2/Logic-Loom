/**
 * Prompt defaults panel — sets `prompt_profile_id` + free-text overrides.
 *
 * Overrides are stored as a Record<string, string>; the user edits them
 * as `slot=value` lines (one per line). The profile picker lists
 * profiles from the Prompt Library (`listProfiles`).
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import type { AssistantBatch } from "../../../api/assistantBatches";
import { listProfiles } from "../../../api/promptLibrary";

export interface BatchPromptDefaultsProps {
  batch: AssistantBatch;
  onSave: (
    profileId: string | null,
    overrides: Record<string, string>,
  ) => void;
  saving?: boolean;
  saveError?: string | null;
}

function serializeOverrides(rec: Record<string, string>): string {
  return Object.entries(rec)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function parseOverrides(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const slot = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    if (slot) out[slot] = value;
  }
  return out;
}

export function BatchPromptDefaults({
  batch,
  onSave,
  saving = false,
  saveError = null,
}: BatchPromptDefaultsProps) {
  const [profileId, setProfileId] = useState(batch.prompt_profile_id ?? "");
  const [overridesRaw, setOverridesRaw] = useState(
    serializeOverrides(batch.prompt_overrides),
  );
  // Re-sync when the persisted values change on the same batch (same
  // pattern as BatchWorkflowDefaults) — not just on a batch-id swap — so a fresh save round-trip
  // reflects in the form without the user having to navigate away and
  // back. Stringifying the overrides dict gives useEffect a stable
  // dependency it can compare by value.
  const overridesKey = serializeOverrides(batch.prompt_overrides);
  useEffect(() => {
    setProfileId(batch.prompt_profile_id ?? "");
    setOverridesRaw(overridesKey);
  }, [batch.id, batch.prompt_profile_id, overridesKey]);

  const dirty =
    (profileId || null) !== (batch.prompt_profile_id ?? null) ||
    overridesRaw !== overridesKey;
  const savedClean =
    !dirty && !saving && (batch.prompt_profile_id || overridesKey);

  const profilesQ = useQuery({ queryKey: ["prompts", "profiles"], queryFn: listProfiles });
  const profiles = profilesQ.data?.profiles ?? [];

  return (
    <div data-testid="batch-prompt-defaults" className="flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Prompt profile
        </span>
        <select
          data-testid="prompt-profile-id"
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          className="rounded border px-2 py-1 text-sm"
        >
          <option value="">(none — use global active)</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.id})
            </option>
          ))}
        </select>
        {profilesQ.isLoading && (
          <span className="text-[10px] text-muted-foreground">Loading profiles…</span>
        )}
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase text-muted-foreground">
          Overrides (slot=value, one per line)
        </span>
        <textarea
          data-testid="prompt-overrides"
          value={overridesRaw}
          onChange={(e) => setOverridesRaw(e.target.value)}
          rows={4}
          className="rounded border px-2 py-1 font-mono text-xs"
        />
      </label>
      {saveError && (
        <div
          data-testid="prompt-save-error"
          className="rounded border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive"
        >
          Save failed: {saveError}
        </div>
      )}
      <div className="flex items-center justify-end gap-3">
        {savedClean && (
          <span
            data-testid="prompt-saved-indicator"
            className="text-xs text-emerald-700"
          >
            ✓ Saved.
          </span>
        )}
        <button
          type="button"
          data-testid="prompt-save"
          disabled={!dirty || saving}
          onClick={() =>
            onSave(profileId.trim() || null, parseOverrides(overridesRaw))
          }
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save prompts"}
        </button>
      </div>
    </div>
  );
}
