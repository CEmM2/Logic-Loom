/**
 * Workflows Library page.
 *
 * A single shared component rendered by two mode-specific routes
 * (`/akms/workflows` and `/assistant/workflows`). The optional
 * `executor` prop filters which workflows are visible:
 *
 *   - `"claude"` — only Claude/AKMS workflows (akms mode).
 *   - `"zsum"`   — only zsum workflows (zsum mode, future).
 *   - `null`     — all workflows (assistant mode).
 *
 * Layout:
 *   Left  — workflow list grouped by `source` (bundled vs user).
 *   Right — workflow detail showing slot list, defaults, run-command
 *           preview, and (for `source === "user"`) an inline editor.
 *
 * Library-vs-request boundary (easy to blur — keep it strict):
 * this page edits **workflow definitions** (the on-disk library under
 * `<akms_workspace>/workflow-library/user-workflows/`). It does NOT
 * mutate per-run invocation parameters — those belong to the Run dialog
 * (`api/batches.ts::runWorkflow`). A "Run with this workflow"
 * affordance links to `/akms/batches`, the request-level invocation
 * point; it does not duplicate the Run dialog here.
 *
 * Bundled vs user surfacing:
 *   - Bundled workflows are explicitly read-only — the editor is
 *     disabled and a "Duplicate to edit" hint is shown.
 *   - Duplicating a bundled workflow mints a user-source copy that
 *     lands under `<akms_workspace>/workflow-library/user-workflows/`
 *     (the backend route enforces id minting and source forcing).
 *   - Archive is exposed only for user workflows.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveWorkflow,
  deleteWorkflow,
  duplicateWorkflow,
  groupBySource,
  listWorkflows,
  updateWorkflow,
  type WorkflowDefinition,
  type WorkflowExecutor,
  type WorkflowPayload,
} from "../../api/workflows";

interface WorkflowsPageProps {
  /**
   * Executor filter. `null` (assistant mode) shows every workflow.
   * `"claude"` filters to the AKMS catalog; `"zsum"` reserved for the
   * eventual zsum-mode mount.
   */
  executor: WorkflowExecutor | null;
}

export function WorkflowsPage({ executor }: WorkflowsPageProps) {
  const qc = useQueryClient();
  const [includeArchived, setIncludeArchived] = useState(false);

  const listQ = useQuery({
    queryKey: ["workflows", "list", includeArchived],
    queryFn: () => listWorkflows(includeArchived),
    staleTime: 30_000,
  });

  const all = useMemo<WorkflowDefinition[]>(() => {
    const items = listQ.data?.workflows ?? [];
    if (!executor) return items;
    return items.filter((w) => w.executor === executor);
  }, [listQ.data, executor]);

  const { bundled, user } = useMemo(() => groupBySource(all), [all]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const effectiveSelected =
    selectedId && all.some((w) => w.id === selectedId)
      ? selectedId
      : (all[0]?.id ?? null);

  const selected = useMemo<WorkflowDefinition | null>(
    () => all.find((w) => w.id === effectiveSelected) ?? null,
    [all, effectiveSelected],
  );

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["workflows", "list"] });

  const duplicateMut = useMutation({
    mutationFn: (workflowId: string) => duplicateWorkflow(workflowId),
    onSuccess: (dup) => {
      setSelectedId(dup.id);
      invalidate();
    },
  });

  return (
    <main
      className="flex h-full"
      aria-label="Workflows Library"
      data-testid="workflows-page"
    >
      {/* ── Left: grouped list ────────────────────────────────────── */}
      <aside
        className="flex w-72 flex-col border-r"
        aria-label="Workflows"
        data-testid="workflows-list"
      >
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div>
            <h1 className="text-sm font-semibold">Workflows</h1>
            <p className="text-xs text-muted-foreground">
              {listQ.isLoading
                ? "Loading…"
                : `${bundled.length} bundled · ${user.length} user`}
            </p>
          </div>
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <input
              type="checkbox"
              data-testid="workflows-include-archived"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            Archived
          </label>
        </header>

        <ul
          className="flex-1 overflow-auto"
          role="listbox"
          aria-label="Workflows grouped by source"
        >
          <SourceGroupHeader label="Bundled" count={bundled.length} />
          {bundled.map((w) => (
            <WorkflowRow
              key={w.id}
              wf={w}
              selected={effectiveSelected === w.id}
              onSelect={() => setSelectedId(w.id)}
            />
          ))}
          {bundled.length === 0 && !listQ.isLoading && (
            <li className="px-4 py-2 text-xs text-muted-foreground">
              No bundled workflows.
            </li>
          )}

          <SourceGroupHeader label="User" count={user.length} />
          {user.map((w) => (
            <WorkflowRow
              key={w.id}
              wf={w}
              selected={effectiveSelected === w.id}
              onSelect={() => setSelectedId(w.id)}
            />
          ))}
          {user.length === 0 && !listQ.isLoading && (
            <li className="px-4 py-2 text-xs text-muted-foreground">
              No user workflows. Duplicate a bundled workflow to edit.
            </li>
          )}

          {listQ.error && (
            <li className="px-4 py-3 text-xs text-red-600">
              Failed to load workflows: {errMsg(listQ.error)}
            </li>
          )}
        </ul>
      </aside>

      {/* ── Right: detail ─────────────────────────────────────────── */}
      <section
        className="flex flex-1 flex-col overflow-hidden"
        aria-label="Workflow detail"
      >
        {selected ? (
          <WorkflowDetail
            key={`${selected.source}::${selected.id}`}
            workflow={selected}
            onDuplicate={() => duplicateMut.mutate(selected.id)}
            duplicating={duplicateMut.isPending}
            onSaved={invalidate}
            onArchived={() => {
              setSelectedId(null);
              invalidate();
            }}
            onDeleted={() => {
              setSelectedId(null);
              invalidate();
            }}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Select a workflow to inspect.
          </div>
        )}
      </section>
    </main>
  );
}

function SourceGroupHeader({ label, count }: { label: string; count: number }) {
  return (
    <li
      data-testid={`workflows-group-${label.toLowerCase()}`}
      className="sticky top-0 z-10 border-b bg-muted/40 px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
    >
      {label} · {count}
    </li>
  );
}

function WorkflowRow({
  wf,
  selected,
  onSelect,
}: {
  wf: WorkflowDefinition;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        role="option"
        aria-selected={selected}
        onClick={onSelect}
        data-testid={`workflow-row-${wf.id}`}
        className={
          "w-full border-b px-3 py-2 text-left text-xs hover:bg-accent " +
          (selected ? "bg-accent" : "")
        }
      >
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-medium">{wf.name || wf.id}</span>
          <span
            data-testid={`workflow-badge-${wf.id}`}
            className={
              "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase " +
              (wf.source === "bundled"
                ? "bg-blue-100 text-blue-700"
                : "bg-emerald-100 text-emerald-700")
            }
          >
            {wf.source}
          </span>
        </div>
        <div className="truncate font-mono text-[10px] text-muted-foreground">
          {wf.id} · {wf.executor}
          {wf.status === "archived" ? " · archived" : ""}
        </div>
      </button>
    </li>
  );
}

// ── Detail / editor ────────────────────────────────────────────────────────

interface WorkflowDetailProps {
  workflow: WorkflowDefinition;
  onDuplicate: () => void;
  duplicating: boolean;
  onSaved: () => void;
  onArchived: () => void;
  onDeleted: () => void;
}

function WorkflowDetail({
  workflow,
  onDuplicate,
  duplicating,
  onSaved,
  onArchived,
  onDeleted,
}: WorkflowDetailProps) {
  const isReadOnly = workflow.source === "bundled";
  const location = useLocation();
  // Open Batches in the current mode (assistant or akms). When the user
  // clicks "Run with this workflow" from /assistant/workflows we want to
  // land on /assistant/batches?workflow_id=... — not /akms/batches.
  const batchesPath = location.pathname.startsWith("/assistant/")
    ? "/assistant/batches"
    : "/akms/batches";
  const runLinkTo = `${batchesPath}?workflow_id=${encodeURIComponent(workflow.id)}`;

  const [name, setName] = useState(workflow.name);
  const [category, setCategory] = useState(workflow.category);
  const [description, setDescription] = useState(workflow.description);
  const [defaultSubdir, setDefaultSubdir] = useState(
    workflow.default_output_subdir,
  );
  const [inputSlotsText, setInputSlotsText] = useState(
    workflow.input_slots.join("\n"),
  );
  const [promptSlotsText, setPromptSlotsText] = useState(() =>
    JSON.stringify(workflow.prompt_slots, null, 2),
  );
  const [requiredToolsText, setRequiredToolsText] = useState(
    workflow.required_external_tools.join("\n"),
  );
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saveBanner, setSaveBanner] = useState<string | null>(null);

  // Re-sync local state when the selected workflow changes (key forces
  // remount, but keep the explicit reset for clarity).
  useEffect(() => {
    setName(workflow.name);
    setCategory(workflow.category);
    setDescription(workflow.description);
    setDefaultSubdir(workflow.default_output_subdir);
    setInputSlotsText(workflow.input_slots.join("\n"));
    setPromptSlotsText(JSON.stringify(workflow.prompt_slots, null, 2));
    setRequiredToolsText(workflow.required_external_tools.join("\n"));
    setValidationError(null);
    setSaveBanner(null);
  }, [workflow.id, workflow.source]);

  const updateMut = useMutation({
    mutationFn: (payload: WorkflowPayload) =>
      updateWorkflow(workflow.id, payload),
    onSuccess: () => {
      setSaveBanner("Saved.");
      setValidationError(null);
      onSaved();
    },
    onError: (e: unknown) => {
      setSaveBanner(null);
      setValidationError(errMsg(e));
    },
  });

  const archiveMut = useMutation({
    mutationFn: () => archiveWorkflow(workflow.id),
    onSuccess: () => onArchived(),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteWorkflow(workflow.id),
    onSuccess: () => onDeleted(),
  });

  const handleSave = () => {
    let promptSlots: Record<string, string>;
    try {
      const parsed = JSON.parse(promptSlotsText) as unknown;
      if (
        !parsed ||
        typeof parsed !== "object" ||
        Array.isArray(parsed) ||
        Object.entries(parsed as Record<string, unknown>).some(
          ([k, v]) => typeof k !== "string" || typeof v !== "string",
        )
      ) {
        throw new Error("prompt_slots must be a {string: string} object");
      }
      promptSlots = parsed as Record<string, string>;
    } catch (e) {
      setValidationError(`Invalid prompt_slots JSON: ${errMsg(e)}`);
      return;
    }
    const payload: WorkflowPayload = {
      id: workflow.id,
      name,
      category,
      description,
      executor: workflow.executor,
      source_mode: workflow.source_mode,
      input_slots: splitLines(inputSlotsText),
      output_format: workflow.output_format,
      default_output_subdir: defaultSubdir,
      prompt_slots: promptSlots,
      required_external_tools: splitLines(requiredToolsText),
      validation: workflow.validation ?? { required_sections: [] },
    };
    updateMut.mutate(payload);
  };

  const runCommandPreview = useMemo(
    () => buildRunCommandPreview(workflow),
    [workflow],
  );

  const dirty =
    name !== workflow.name ||
    category !== workflow.category ||
    description !== workflow.description ||
    defaultSubdir !== workflow.default_output_subdir ||
    inputSlotsText !== workflow.input_slots.join("\n") ||
    promptSlotsText !== JSON.stringify(workflow.prompt_slots, null, 2) ||
    requiredToolsText !== workflow.required_external_tools.join("\n");

  return (
    <div
      className="flex flex-1 flex-col overflow-auto p-5"
      data-testid="workflow-detail"
    >
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">{workflow.name || workflow.id}</h2>
          <p className="text-xs text-muted-foreground">
            <code className="font-mono">{workflow.id}</code> · executor:{" "}
            <code className="font-mono">{workflow.executor}</code> · source:{" "}
            <code
              className="font-mono"
              data-testid="workflow-source-label"
            >
              {workflow.source}
            </code>
            {workflow.status === "archived" && (
              <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-700">
                archived
              </span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Link
            to={runLinkTo}
            data-testid="workflow-run-link"
            className="rounded border px-2 py-1 text-xs hover:bg-accent"
            title="Open Batches to run this workflow"
          >
            Run with this workflow
          </Link>
          <button
            type="button"
            data-testid="workflow-duplicate-button"
            onClick={onDuplicate}
            disabled={duplicating}
            className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-40"
            title="Duplicate to a user-editable workflow"
          >
            {duplicating ? "Duplicating…" : "Duplicate"}
          </button>
          {!isReadOnly && workflow.status !== "archived" && (
            <button
              type="button"
              data-testid="workflow-archive-button"
              onClick={() => archiveMut.mutate()}
              disabled={archiveMut.isPending}
              className="rounded border px-2 py-1 text-xs text-amber-700 hover:bg-amber-50 disabled:opacity-40"
              title="Archive this user workflow"
            >
              {archiveMut.isPending ? "Archiving…" : "Archive"}
            </button>
          )}
          {!isReadOnly && (
            <button
              type="button"
              data-testid="workflow-delete-button"
              onClick={() => {
                const ok = window.confirm(
                  `Delete the user workflow "${workflow.name || workflow.id}" permanently? Run history that referenced this workflow will remain but the workflow itself will be gone. Use Archive instead if you only want to hide it.`,
                );
                if (ok) deleteMut.mutate();
              }}
              disabled={deleteMut.isPending}
              className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-40"
              title="Delete this user workflow"
            >
              {deleteMut.isPending ? "Deleting…" : "Delete"}
            </button>
          )}
        </div>
      </header>

      {isReadOnly && (
        <p
          data-testid="workflow-readonly-banner"
          className="mb-3 rounded border border-blue-300 bg-blue-50 px-3 py-2 text-xs text-blue-800"
        >
          Bundled workflow — read-only. Duplicate to edit and the copy will be
          saved under <code className="font-mono">
            &lt;akms_workspace&gt;/workflow-library/user-workflows/
          </code>.
        </p>
      )}

      <div className="grid gap-3 text-sm">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Name
          </span>
          <input
            data-testid="workflow-name-input"
            className="rounded border px-2 py-1 text-sm"
            value={name}
            disabled={isReadOnly}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Category
          </span>
          <input
            data-testid="workflow-category-input"
            className="rounded border px-2 py-1 text-sm"
            value={category}
            disabled={isReadOnly}
            onChange={(e) => setCategory(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Description
          </span>
          <input
            data-testid="workflow-description-input"
            className="rounded border px-2 py-1 text-sm"
            value={description}
            disabled={isReadOnly}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Default output subdir
          </span>
          <input
            data-testid="workflow-default-subdir-input"
            className="rounded border px-2 py-1 text-sm font-mono"
            value={defaultSubdir}
            disabled={isReadOnly}
            onChange={(e) => setDefaultSubdir(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Input slots (one per line)
          </span>
          <textarea
            data-testid="workflow-input-slots-input"
            rows={3}
            className="rounded border px-2 py-1 font-mono text-xs"
            value={inputSlotsText}
            disabled={isReadOnly}
            onChange={(e) => setInputSlotsText(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Prompt slot defaults (JSON: slot → variant_ref)
          </span>
          <textarea
            data-testid="workflow-prompt-slots-input"
            rows={6}
            className="rounded border px-2 py-1 font-mono text-xs"
            value={promptSlotsText}
            disabled={isReadOnly}
            onChange={(e) => setPromptSlotsText(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Required external tools (one per line)
          </span>
          <textarea
            data-testid="workflow-required-tools-input"
            rows={2}
            className="rounded border px-2 py-1 font-mono text-xs"
            value={requiredToolsText}
            disabled={isReadOnly}
            onChange={(e) => setRequiredToolsText(e.target.value)}
          />
        </label>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="workflow-save-button"
          disabled={isReadOnly || !dirty || updateMut.isPending}
          onClick={handleSave}
          className="rounded bg-foreground px-3 py-1 text-sm text-background disabled:opacity-40"
        >
          {updateMut.isPending ? "Saving…" : "Save"}
        </button>
        {isReadOnly && (
          <span className="text-xs text-muted-foreground">
            Bundled workflows are read-only. Duplicate to edit.
          </span>
        )}
      </div>

      {validationError && (
        <p
          data-testid="workflow-validation-banner"
          className="mt-3 rounded border border-red-500 px-3 py-2 text-xs text-red-700"
        >
          {validationError}
        </p>
      )}
      {saveBanner && (
        <p
          data-testid="workflow-save-banner"
          className="mt-3 rounded border border-green-500 px-3 py-2 text-xs text-green-700"
        >
          {saveBanner}
        </p>
      )}

      <section
        className="mt-4 rounded border bg-muted/20 p-3"
        aria-label="Run command preview"
        data-testid="workflow-run-preview"
      >
        <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
          Run-command preview
        </h3>
        <pre className="overflow-auto whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
          {runCommandPreview}
        </pre>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Library-level preview only — per-run parameters (batch, prompt
          profile, overrides) are configured in the{" "}
          <Link to="/akms/batches" className="underline">
            Batches Run dialog
          </Link>
          .
        </p>
      </section>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

function splitLines(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function buildRunCommandPreview(wf: WorkflowDefinition): string {
  const slots = wf.input_slots.length
    ? wf.input_slots.join(", ")
    : "(none)";
  const tools = wf.required_external_tools.length
    ? wf.required_external_tools.join(", ")
    : "(none)";
  return [
    `# Executor: ${wf.executor}`,
    `# Source mode: ${wf.source_mode}`,
    `# Output format: ${wf.output_format}`,
    `# Default output subdir: ${wf.default_output_subdir || "(workflow id)"}`,
    `# Input slots: ${slots}`,
    `# Required external tools: ${tools}`,
    "",
    `POST /api/runs/workflow`,
    JSON.stringify(
      {
        workflow_id: wf.id,
        // batch_id, prompt_profile_id and overrides come from the
        // request-level Run dialog, not from the library.
      },
      null,
      2,
    ),
  ].join("\n");
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default WorkflowsPage;
