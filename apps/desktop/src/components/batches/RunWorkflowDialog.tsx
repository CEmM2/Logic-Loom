/**
 * Extracted `RunWorkflowDialog` (originally inline in `BatchesPage.tsx`).
 *
 * Extracted so the Assistant
 * wrapper (`components/assistant/RunWorkflowFromAssistant.tsx`) can
 * reuse the same UI surface with a category-restricted workflow filter.
 *
 * The dialog accepts an optional `workflowFilter` prop:
 *
 *   - Default: `() => true` — all workflows surface (the AKMS batches
 *     page behaviour, preserved verbatim).
 *   - Assistant wrapper: `cat => assistantCategories.includes(cat)`.
 *
 * The filter callback receives a workflow's `category` string. The
 * dialog renders any UI that lists workflows by piping the listing
 * through the filter; today the dialog has a free-text workflow id
 * input so the filter is consumed by future workflow-listing UI
 * (when this component is extended to list workflows from the
 * workflows API). The default-permissive contract is unit-tested.
 *
 * `outDirHint` is an optional pre-filled path for downstream
 * Assistant outputs (`<assistant_workspace>/assistant/outputs/...`).
 * When supplied it surfaces in a read-only field beneath the run
 * controls.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  previewEffectivePrompts,
  runWorkflow,
  type EditableBatch,
  type EffectivePromptEntry,
  type EffectivePromptsManifest,
} from "../../api/batches";
import { listProfiles } from "../../api/promptLibrary";
import { listWorkflows } from "../../api/workflows";

export interface RunWorkflowDialogProps {
  batch: EditableBatch;
  onClose: () => void;
  /**
   * Optional category-filter for workflow listings (Assistant wrapper).
   * Default: permissive `() => true`. The filter is invoked with the
   * workflow's `category` string.
   */
  workflowFilter?: (category: string) => boolean;
  /**
   * Optional pre-filled out-dir surfaced as a read-only display.
   * Assistant wrapper supplies
   * ``<assistant_workspace>/assistant/outputs/<batch_id>/<workflow_id>/<run_id>``.
   * Cosmetic only — the run dispatcher recomputes its own out_dir.
   */
  outDirHint?: string;
  /**
   * Optional title override. Assistant wrapper supplies "Run Assistant
   * workflow against ...".
   */
  titleOverride?: string;
  /**
   * Optional workflow_id to pre-select. Wins over ``batch.default_workflow_id``
   * when both are set — used when the dialog opens from a deep-link like
   * Workflows → "Run with this workflow".
   */
  defaultWorkflowId?: string;
  /**
   * Optional surface hook: lets callers know the filter was applied
   * (used by tests to assert behaviour without exposing the predicate).
   */
  onFilterApplied?: (allowed: boolean) => void;
}

export function RunWorkflowDialog({
  batch,
  onClose,
  workflowFilter,
  outDirHint,
  titleOverride,
  defaultWorkflowId,
  onFilterApplied,
}: RunWorkflowDialogProps) {
  const filter = workflowFilter ?? (() => true);
  const [workflowId, setWorkflowId] = useState(
    defaultWorkflowId ?? batch.default_workflow_id ?? "",
  );
  const [profileId, setProfileId] = useState(batch.prompt_profile_id ?? "");
  const [runLabel, setRunLabel] = useState("");
  const [saveAsDefault, setSaveAsDefault] = useState(false);
  const [preview, setPreview] = useState<EffectivePromptsManifest | null>(null);

  const profilesQ = useQuery({ queryKey: ["prompts", "profiles"], queryFn: listProfiles });
  const workflowsQ = useQuery({
    queryKey: ["workflows", "list"],
    queryFn: () => listWorkflows(false),
  });
  const profiles = profilesQ.data?.profiles ?? [];
  const workflows = useMemo(
    () => (workflowsQ.data?.workflows ?? []).filter((w) => filter(w.category ?? "")),
    [workflowsQ.data, filter],
  );

  const previewMut = useMutation({
    mutationFn: () =>
      previewEffectivePrompts({
        batch_id: batch.id,
        workflow_id: workflowId,
        prompt_profile_id: profileId || null,
        prompt_overrides: batch.prompt_overrides ?? {},
        run_label: runLabel || null,
        save_as_batch_default: false,
      }),
    onSuccess: (res) => setPreview(res.effective_prompts),
  });

  const runMut = useMutation({
    mutationFn: () =>
      runWorkflow({
        batch_id: batch.id,
        workflow_id: workflowId,
        // Prefer the batch's notebook when set; empty → backend creates a new
        // NotebookLM notebook for akms.node_extraction (nlm_batch_fix).
        notebook_id: batch.notebook_id ?? null,
        prompt_profile_id: profileId || null,
        prompt_overrides: batch.prompt_overrides ?? {},
        run_label: runLabel || null,
        save_as_batch_default: saveAsDefault,
      }),
    onSuccess: () => onClose(),
  });

  const canRun = workflowId.trim().length > 0 && !runMut.isPending;

  // Trigger the filter callback once on mount with the default-permissive
  // value so tests can pin the contract without a workflow listing UI.
  if (onFilterApplied) {
    // Defer outside render to avoid setState-during-render.
    queueMicrotask(() => onFilterApplied(filter("Literature Review")));
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Run workflow"
      data-testid="run-workflow-dialog"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-2xl rounded bg-background p-5 shadow-lg">
        <h2 className="mb-3 text-base font-semibold">
          {titleOverride ?? `Run workflow against ${batch.title}`}
        </h2>

        <div className="space-y-3 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase text-muted-foreground">Workflow</span>
            <select
              data-testid="run-workflow-id-input"
              className="rounded border px-2 py-1"
              value={workflowId}
              onChange={(e) => setWorkflowId(e.target.value)}
            >
              <option value="">— select workflow —</option>
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.id})
                </option>
              ))}
              {/* Surface the pre-filled value even if it isn't in the
                  workflow list (deep-linked from an old run / external
                  source) so the dialog never silently drops the id. */}
              {workflowId && !workflows.some((w) => w.id === workflowId) && (
                <option value={workflowId}>{workflowId}</option>
              )}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase text-muted-foreground">Prompt profile</span>
            <select
              data-testid="run-profile-input"
              className="rounded border px-2 py-1"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
            >
              <option value="">(none — use global active)</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.id})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase text-muted-foreground">Run label</span>
            <input
              data-testid="run-label-input"
              className="rounded border px-2 py-1"
              value={runLabel}
              onChange={(e) => setRunLabel(e.target.value)}
            />
          </label>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              data-testid="run-save-default"
              checked={saveAsDefault}
              onChange={(e) => setSaveAsDefault(e.target.checked)}
            />
            <span>Save these settings as the batch default (otherwise this run is request-level only)</span>
          </label>
          {outDirHint ? (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium uppercase text-muted-foreground">
                Output directory (preview)
              </span>
              <input
                readOnly
                data-testid="run-out-dir-hint"
                className="rounded border bg-muted/20 px-2 py-1 font-mono text-xs"
                value={outDirHint}
              />
            </label>
          ) : null}
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="run-preview-prompts"
            disabled={!workflowId.trim() || previewMut.isPending}
            className="rounded border px-3 py-1 text-sm"
            onClick={() => previewMut.mutate()}
          >
            {previewMut.isPending ? "Previewing…" : "Preview effective prompts"}
          </button>
          <button
            type="button"
            data-testid="run-start"
            disabled={!canRun}
            className="rounded bg-foreground px-3 py-1 text-sm text-background disabled:opacity-40"
            onClick={() => runMut.mutate()}
          >
            {runMut.isPending ? "Starting…" : "Run"}
          </button>
          <button
            type="button"
            className="ml-auto rounded border px-3 py-1 text-sm"
            onClick={onClose}
          >
            Cancel
          </button>
        </div>

        {previewMut.isError && (
          <p className="mt-3 text-xs text-red-600">
            Preview failed: {errMsg(previewMut.error)}
          </p>
        )}

        {preview && (
          <section
            className="mt-4 rounded border bg-muted/20 p-3"
            aria-label="Resolved prompt variants"
            data-testid="run-preview-results"
          >
            <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
              Resolved variants
            </h3>
            <PreviewList preview={preview} />
          </section>
        )}
      </div>
    </div>
  );
}

/**
 * Decode a backend effective-prompts envelope into UI rows.
 *
 * The dry-run endpoint returns an :class:`EffectivePromptSet` serialised
 * as ``{schema_version, slot_to_variant, contents}``. Do not iterate the
 * envelope's top-level keys: that renders ``slot_to_variant`` and
 * ``contents`` as if they were slot names, yielding literal
 * ``variant: ?`` placeholders (a past regression).
 *
 * Exported so the decoder can be unit-tested without rendering the
 * surrounding dialog.
 */
export function decodeEffectivePrompts(
  preview: EffectivePromptsManifest,
): EffectivePromptEntry[] {
  const slotToVariant = isStringMap(preview.slot_to_variant)
    ? (preview.slot_to_variant as Record<string, string>)
    : null;
  const contents = isStringMap(preview.contents)
    ? (preview.contents as Record<string, string>)
    : null;
  if (slotToVariant) {
    const out: EffectivePromptEntry[] = [];
    for (const [slot, ref] of Object.entries(slotToVariant)) {
      out.push({
        slot,
        variant_id: parseVariantId(ref),
        resolved_text: contents?.[slot] ?? "",
        source: parseVariantSource(ref),
        family: slot.split(".")[0],
      });
    }
    return out;
  }

  // Legacy envelopes (kept for forward compatibility):
  //   * ``entries: [...]`` already in the canonical shape.
  //   * Flat ``{<slot>: {variant_id, resolved_text, ...}}`` map.
  if (Array.isArray(preview.entries)) return preview.entries;
  const out: EffectivePromptEntry[] = [];
  for (const [slot, val] of Object.entries(preview)) {
    if (slot === "entries" || slot === "schema_version") continue;
    if (val && typeof val === "object" && !Array.isArray(val)) {
      const v = val as Record<string, unknown>;
      out.push({
        slot,
        variant_id: String(v.variant_id ?? v.id ?? "?"),
        resolved_text: String(v.resolved_text ?? v.text ?? ""),
        source: typeof v.source === "string" ? v.source : undefined,
        family: typeof v.family === "string" ? v.family : undefined,
      });
    }
  }
  return out;
}

function PreviewList({ preview }: { preview: EffectivePromptsManifest }) {
  const entries = useMemo<EffectivePromptEntry[]>(
    () => decodeEffectivePrompts(preview),
    [preview],
  );

  if (entries.length === 0) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="preview-empty">
        No prompt variants are selected for this run: neither the active prompt
        profile, the batch nor this dialog picks one. Each step will use its
        built-in default prompt. To override it, choose a profile or variant in
        the Prompts section.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {entries.map((e) => (
        <li key={e.slot} data-testid={`preview-entry-${e.slot}`} className="rounded border bg-background p-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-mono">{e.slot}</span>
            <span className="text-muted-foreground">
              variant: <code className="font-mono">{e.variant_id}</code>
              {e.family ? <> · family: <code className="font-mono">{e.family}</code></> : null}
            </span>
          </div>
          {e.resolved_text ? (
            <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">
              {e.resolved_text}
            </pre>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function isStringMap(v: unknown): boolean {
  if (!v || typeof v !== "object" || Array.isArray(v)) return false;
  for (const val of Object.values(v as Record<string, unknown>)) {
    if (typeof val !== "string") return false;
  }
  return true;
}

/**
 * Variant refs are formatted ``"<source>:<slot>.<variant_id>"`` (e.g.
 * ``"bundled:zsum.paper_card.default"``). Strip the prefix to get the
 * variant id alone for display. Falls back to the full string when the
 * shape doesn't match so debugging stays possible.
 */
function parseVariantId(ref: string): string {
  const lastDot = ref.lastIndexOf(".");
  const afterDot = lastDot >= 0 ? ref.slice(lastDot + 1) : ref;
  return afterDot || ref;
}

function parseVariantSource(ref: string): string | undefined {
  const firstColon = ref.indexOf(":");
  return firstColon > 0 ? ref.slice(0, firstColon) : undefined;
}

export default RunWorkflowDialog;
