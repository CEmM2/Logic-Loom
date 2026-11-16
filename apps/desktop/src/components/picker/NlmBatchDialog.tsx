/**
 * Configuration dialog for the NLM direct batch generator.
 * Collects the required --plan and --prompt-file paths, then submits.
 */

import { useEffect, useState } from "react";

import type { RunRecord } from "../../api/nlm";
import { useSubmitNlmBatch } from "../../hooks/picker";

interface Props {
  open: boolean;
  batchId: string;
  batchTitle: string;
  notebookId: string | null;
  onLaunch: (rec: RunRecord) => void;
  onClose: () => void;
}

export function NlmBatchDialog({
  open,
  batchId,
  batchTitle,
  notebookId,
  onLaunch,
  onClose,
}: Props) {
  const submit = useSubmitNlmBatch();
  const [planPath, setPlanPath] = useState("");
  const [promptFile, setPromptFile] = useState("");
  const [templateFile, setTemplateFile] = useState("");
  const [outDir, setOutDir] = useState("");
  const [sourceIds, setSourceIds] = useState("");
  const [timeout, setTimeout_] = useState("540");
  const [outputFormat, setOutputFormat] = useState<"yaml" | "json">("yaml");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setError(null);
      submit.reset();
    }
  }, [open]);

  const canSubmit = planPath.trim() !== "" && promptFile.trim() !== "" && !submit.isPending;

  const onRun = async () => {
    setError(null);
    try {
      const rec = await submit.mutateAsync({
        batch_id: batchId,
        plan_path: planPath.trim(),
        prompt_file: promptFile.trim(),
        out_dir: outDir.trim() || undefined,
        template_file: templateFile.trim() || undefined,
        notebook_id: notebookId ?? undefined,
        source_ids: sourceIds.trim() ? sourceIds.split(",").map((s) => s.trim()) : undefined,
        timeout: Number(timeout) || 540,
        output_format: outputFormat,
      });
      onLaunch(rec);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="w-[32rem] rounded border border-border bg-background p-4 shadow-lg">
        <h3 className="text-base font-semibold">Generate Nodes (NLM direct)</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {batchTitle} — calls NotebookLM directly, no Claude tokens used
        </p>

        <div className="mt-3 flex flex-col gap-2 text-sm">
          <label className="flex flex-col gap-0.5">
            <span className="text-xs text-muted-foreground">Plan JSON *</span>
            <input
              type="text"
              value={planPath}
              onChange={(e) => setPlanPath(e.target.value)}
              placeholder="Sources_Evals/NLM/Inputs/R7_B2_plan.json"
              className="rounded border border-border bg-background px-2 py-1 text-xs font-mono"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-xs text-muted-foreground">Prompt file *</span>
            <input
              type="text"
              value={promptFile}
              onChange={(e) => setPromptFile(e.target.value)}
              placeholder="path/to/notebooklm_prompt.md"
              className="rounded border border-border bg-background px-2 py-1 text-xs font-mono"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-xs text-muted-foreground">Template file</span>
            <input
              type="text"
              value={templateFile}
              onChange={(e) => setTemplateFile(e.target.value)}
              placeholder="path/to/output_template.yaml"
              className="rounded border border-border bg-background px-2 py-1 text-xs font-mono"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-xs text-muted-foreground">Output directory</span>
            <input
              type="text"
              value={outDir}
              onChange={(e) => setOutDir(e.target.value)}
              placeholder={`Sources_Evals/NLM/Outputs/${batchId}`}
              className="rounded border border-border bg-background px-2 py-1 text-xs font-mono"
            />
          </label>
          <div className="flex gap-2">
            <label className="flex flex-1 flex-col gap-0.5">
              <span className="text-xs text-muted-foreground">Source IDs (comma-sep)</span>
              <input
                type="text"
                value={sourceIds}
                onChange={(e) => setSourceIds(e.target.value)}
                className="rounded border border-border bg-background px-2 py-1 text-xs font-mono"
              />
            </label>
            <label className="flex w-20 flex-col gap-0.5">
              <span className="text-xs text-muted-foreground">Timeout</span>
              <input
                type="number"
                value={timeout}
                onChange={(e) => setTimeout_(e.target.value)}
                className="rounded border border-border bg-background px-2 py-1 text-xs font-mono"
              />
            </label>
            <label className="flex w-20 flex-col gap-0.5">
              <span className="text-xs text-muted-foreground">Format</span>
              <select
                value={outputFormat}
                onChange={(e) => setOutputFormat(e.target.value as "yaml" | "json")}
                className="rounded border border-border bg-background px-1 py-1 text-xs"
              >
                <option value="yaml">YAML</option>
                <option value="json">JSON</option>
              </select>
            </label>
          </div>

          {notebookId && (
            <div className="text-xs text-muted-foreground">
              notebook: <span className="font-mono">{notebookId}</span>
            </div>
          )}
        </div>

        {error && (
          <pre className="mt-2 max-h-24 overflow-y-auto rounded bg-red-50 p-2 text-xs text-red-900">
            {error}
          </pre>
        )}

        <div className="mt-4 flex justify-end gap-2 text-sm">
          <button onClick={onClose} className="rounded border border-border px-3 py-1 hover:bg-muted">
            Cancel
          </button>
          <button
            onClick={onRun}
            disabled={!canSubmit}
            className="rounded bg-foreground px-3 py-1 text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submit.isPending ? "Submitting…" : "Run"}
          </button>
        </div>
      </div>
    </div>
  );
}
