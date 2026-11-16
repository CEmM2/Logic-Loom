import { useState } from "react";

import { DeliveryBanner } from "../../components/failure-memory/DeliveryBanner";
import { OverlayCanvas } from "../../components/failure-memory/OverlayCanvas";
import { WorkspaceFrame } from "../../components/failure-memory/WorkspaceFrame";
import { useFailureMemoryResolve } from "../../hooks/failureMemory";
import type { RecordView, ResolveResponse } from "../../api/failureMemory";

/**
 * Required / coactivated / advisory preview for a task or a diff.
 *
 * Runs the real provider through the package with `write_artifacts=false`, so
 * the result is byte-identical to what the standalone CLI would produce (minus
 * the artifact paths that only exist when the provider writes) and nothing is
 * created in the target repository.
 *
 * The result is always shown alongside a DeliveryBanner, never on its own: three
 * of the four possible outcomes produce an empty or thin required lane, and two
 * of those mean opposite things.
 */
export default function FailureMemoryPreviewRoute() {
  return (
    <WorkspaceFrame
      title="Failure Memory — Preview"
      subtitle="Resolve a task or diff exactly as the standalone provider would, without writing anything."
    >
      {({ project }) => <Preview project={project} />}
    </WorkspaceFrame>
  );
}

function Preview({ project }: { project: string }) {
  const [mode, setMode] = useState<"pre-task" | "post-diff">("pre-task");
  const [declared, setDeclared] = useState("");
  const [changed, setChanged] = useState("");
  const [tags, setTags] = useState("");
  const [symbols, setSymbols] = useState("");
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const resolve = useFailureMemoryResolve(project);

  function submit() {
    resolve.mutate({
      mode,
      role: mode === "pre-task" ? "implementer" : "code_reviewer",
      title,
      objective,
      declared_paths: split(declared),
      changed_paths: mode === "post-diff" ? split(changed) : [],
      akms_tags: split(tags),
      symbols: split(symbols),
    });
  }

  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded border border-border p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm">
            Mode{" "}
            <select
              className="rounded border border-border bg-background px-2 py-1 text-sm"
              value={mode}
              onChange={(event) => setMode(event.target.value as typeof mode)}
            >
              <option value="pre-task">pre-task (implementer)</option>
              <option value="post-diff">post-diff (code_reviewer)</option>
            </select>
          </label>
        </div>

        <Field label="Title" value={title} onChange={setTitle} />
        <Field label="Objective" value={objective} onChange={setObjective} />
        <Field
          label="Declared paths (one per line)"
          value={declared}
          onChange={setDeclared}
          multiline
        />
        {mode === "post-diff" && (
          <Field
            label="Changed paths (one per line)"
            value={changed}
            onChange={setChanged}
            multiline
          />
        )}
        <Field label="AKMS tags (one per line)" value={tags} onChange={setTags} multiline />
        <Field
          label="Symbols (one per line) — seeds symbol routes"
          value={symbols}
          onChange={setSymbols}
          multiline
        />

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={submit}
            disabled={resolve.isPending || split(declared).length === 0}
            className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground disabled:opacity-50"
          >
            {resolve.isPending ? "Resolving…" : "Preview resolution"}
          </button>
          <span className="text-xs text-muted-foreground">
            Read-only: the provider runs with <code>write_artifacts=false</code> and
            leaves no invocation directory behind.
          </span>
        </div>

        {resolve.isError && (
          <p className="text-sm text-red-600">{(resolve.error as Error).message}</p>
        )}
      </section>

      {resolve.data && <Result data={resolve.data} />}
    </div>
  );
}

function Result({ data }: { data: ResolveResponse }) {
  return (
    <div className="space-y-4">
      <DeliveryBanner delivery={data.delivery} />

      <section className="grid gap-3 lg:grid-cols-3">
        <Lane title="Required" records={data.required} tone="text-red-700 dark:text-red-300" />
        <Lane
          title="Co-activated"
          records={data.coactivated}
          tone="text-amber-700 dark:text-amber-300"
        />
        <Lane title="Advisory" records={data.advisory} tone="text-blue-700 dark:text-blue-300" />
      </section>

      <section className="h-[420px] rounded border border-border">
        <OverlayCanvas
          overlay={data.overlay}
          emptyMessage="No lesson was selected for this task, so the preview overlay is empty."
        />
      </section>

      <details className="rounded border border-border p-3 text-xs">
        <summary className="cursor-pointer">Provider request and result (verbatim)</summary>
        <p className="mt-2 text-muted-foreground">
          The result is passed through unchanged so it can be diffed against the
          standalone CLI or the shared conformance fixture.
        </p>
        <pre className="mt-2 max-h-96 overflow-auto rounded bg-muted p-3 text-[11px]">
          {JSON.stringify({ request: data.request, result: data.result }, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function Lane({
  title,
  records,
  tone,
}: {
  title: string;
  records: RecordView[];
  tone: string;
}) {
  return (
    <div className="rounded border border-border p-3">
      <h3 className={`text-sm font-semibold ${tone}`}>
        {title} ({records.length})
      </h3>
      {records.length === 0 ? (
        <p className="mt-1 text-xs text-muted-foreground">Nothing in this lane.</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {records.map((record) => (
            <li key={record.node_id} className="text-xs">
              <div className="font-mono font-semibold">{record.node_id}</div>
              <div className="text-muted-foreground">{record.content_ref}</div>
              <details className="mt-1">
                <summary className="cursor-pointer">
                  {record.reasons.length} reason(s)
                </summary>
                <ul className="mt-1 space-y-1 font-mono text-[10px]">
                  {record.reasons.map((reason, index) => (
                    <li key={index}>{reason}</li>
                  ))}
                </ul>
              </details>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  multiline,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
}) {
  return (
    <label className="block text-xs">
      <span className="text-muted-foreground">{label}</span>
      {multiline ? (
        <textarea
          rows={3}
          className="mt-1 w-full rounded border border-border bg-background px-2 py-1 font-mono text-sm"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          className="mt-1 w-full rounded border border-border bg-background px-2 py-1 text-sm"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  );
}

function split(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((part) => part.trim())
    .filter(Boolean);
}
