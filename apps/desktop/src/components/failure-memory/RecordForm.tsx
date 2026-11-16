import { useEffect, useRef, useState } from "react";

import type { Job, RecordRequest, WriteDiagnostics } from "../../api/failureMemory";
import {
  useFailureMemoryJob,
  useInvalidateAfterWrite,
  useRecordLesson,
} from "../../hooks/failureMemory";
import { JobRow } from "./JobPanel";

/**
 * The operator recording form.
 *
 * Two rules govern everything here:
 *
 * 1. **Logic-Loom composes a proposal; it does not write a lesson.** The fields
 *    below are exactly the package recorder's closed key set. There is no `id`
 *    field and there cannot be one — ids are allocated by the package under the
 *    project lock, and both the backend model (`extra="forbid"`) and the
 *    recorder itself reject a request that carries one.
 * 2. **The form never declares success.** Submitting returns a queued job. The
 *    form stays filled and disabled until the backend job reaches a terminal
 *    state, and only clears on `succeeded`. If the compile inside `add_lesson`
 *    fails, the package restores the previous registry and the job reports
 *    `failed` — at which point clearing the form would have thrown away the
 *    operator's work for a recording that did not happen.
 */

const TODAY = new Date().toISOString().slice(0, 10);

const EMPTY: RecordRequest = {
  date_found: TODAY,
  date_fixed: TODAY,
  location: { area: "", file: "", line_hint: "", module: "", package: "", symbol: "" },
  found_by: { type: "", trigger: "" },
  references: { commit: "", issue: "", plan: "", pr: "" },
  symptom: "",
  root_cause: "",
  fix: "",
  prevention: "",
  related: [],
};

interface Props {
  project: string;
  diagnostics: WriteDiagnostics;
}

export function RecordForm({ project, diagnostics }: Props) {
  const [draft, setDraft] = useState<RecordRequest>(EMPTY);
  const [jobId, setJobId] = useState<string | null>(null);
  const [lastJob, setLastJob] = useState<Job | null>(null);
  const record = useRecordLesson(project);
  const invalidate = useInvalidateAfterWrite(project);
  const { data: job } = useFailureMemoryJob(jobId);
  const settled = useRef<string | null>(null);

  const pending =
    record.isPending || job?.status === "queued" || job?.status === "running";

  // Terminal handling — the ONLY place an outcome is acted on, and it runs in an
  // effect so a poll cannot re-enter it mid-render. `settled` makes it run once
  // per job. Success clears the form; failure deliberately does NOT, because a
  // failed recording did not happen (the package restores the previous registry
  // bytes if its compile raises) and throwing the operator's typing away would
  // be the worst possible response to that.
  useEffect(() => {
    if (!job || settled.current === job.id) return;
    if (job.status !== "succeeded" && job.status !== "failed") return;
    settled.current = job.id;
    setLastJob(job);
    setJobId(null);
    if (job.status === "succeeded") setDraft(EMPTY);
    invalidate();
  }, [job, invalidate]);

  const shown = job ?? lastJob;

  function set<K extends keyof RecordRequest>(key: K, value: RecordRequest[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  function setNested(group: "location" | "found_by" | "references", key: string, value: string) {
    setDraft((prev) => ({ ...prev, [group]: { ...(prev[group] ?? {}), [key]: value } }));
  }

  async function submit() {
    const created = await record.mutateAsync(draft);
    setJobId(created.id);
  }

  if (!diagnostics.can_record) {
    return (
      <div className="space-y-2 rounded border border-amber-500/60 bg-amber-500/10 p-4 text-sm">
        <p className="font-semibold">Recording is not available.</p>
        <ul className="list-disc pl-5 text-xs">
          {diagnostics.blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {diagnostics.never_written && (
        <p className="rounded border border-border bg-muted/40 p-3 text-xs">
          This project has never been written to. Recording the first lesson also
          creates the directory its project lock needs — nothing is wrong here.
        </p>
      )}

      <section className="grid gap-3 sm:grid-cols-2">
        <Text label="Date found" value={draft.date_found} onChange={(v) => set("date_found", v)} />
        <Text label="Date fixed" value={draft.date_fixed} onChange={(v) => set("date_fixed", v)} />
        <Text
          label="File (repository-relative, required)"
          value={draft.location.file}
          onChange={(v) => setNested("location", "file", v)}
        />
        <Text label="Symbol" value={draft.location.symbol ?? ""} onChange={(v) => setNested("location", "symbol", v)} />
        <Text label="Area" value={draft.location.area ?? ""} onChange={(v) => setNested("location", "area", v)} />
        <Text label="Package" value={draft.location.package ?? ""} onChange={(v) => setNested("location", "package", v)} />
        <Text label="Module" value={draft.location.module ?? ""} onChange={(v) => setNested("location", "module", v)} />
        <Text label="Line hint" value={draft.location.line_hint ?? ""} onChange={(v) => setNested("location", "line_hint", v)} />
        <Text label="Found by (type)" value={draft.found_by?.type ?? ""} onChange={(v) => setNested("found_by", "type", v)} />
        <Text label="Found by (trigger)" value={draft.found_by?.trigger ?? ""} onChange={(v) => setNested("found_by", "trigger", v)} />
      </section>

      <section className="grid gap-3">
        <Area label="Symptom" value={draft.symptom ?? ""} onChange={(v) => set("symptom", v)} />
        <Area label="Root cause" value={draft.root_cause ?? ""} onChange={(v) => set("root_cause", v)} />
        <Area label="Fix" value={draft.fix ?? ""} onChange={(v) => set("fix", v)} />
        <Area label="Prevention" value={draft.prevention ?? ""} onChange={(v) => set("prevention", v)} />
        <Area label="Notes (optional)" value={draft.notes ?? ""} onChange={(v) => set("notes", v || null)} />
      </section>

      <section className="grid gap-3 sm:grid-cols-4">
        <Text label="Commit" value={draft.references?.commit ?? ""} onChange={(v) => setNested("references", "commit", v)} />
        <Text label="Issue" value={draft.references?.issue ?? ""} onChange={(v) => setNested("references", "issue", v)} />
        <Text label="PR" value={draft.references?.pr ?? ""} onChange={(v) => setNested("references", "pr", v)} />
        <Text label="Plan" value={draft.references?.plan ?? ""} onChange={(v) => setNested("references", "plan", v)} />
      </section>

      <Text
        label="Related lesson ids (comma separated)"
        value={(draft.related ?? []).join(", ")}
        onChange={(v) =>
          set(
            "related",
            v.split(",").map((part) => part.trim()).filter(Boolean),
          )
        }
      />

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={pending || !draft.location.file.trim()}
          onClick={() => void submit()}
          className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground disabled:opacity-50"
        >
          {pending ? "Recording…" : "Record lesson"}
        </button>
        <span className="text-xs text-muted-foreground">
          The lesson id is allocated by the package under the project lock —
          Logic-Loom never assigns one, and never edits{" "}
          <code>lessons.json</code> itself.
        </span>
      </div>

      {record.isError && (
        <p className="text-sm text-red-600">{(record.error as Error).message}</p>
      )}
      {shown && <JobRow job={shown} />}
    </div>
  );
}

function Text({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs">
      <span className="text-muted-foreground">{label}</span>
      <input
        className="mt-1 w-full rounded border border-border bg-background px-2 py-1 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function Area({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs">
      <span className="text-muted-foreground">{label}</span>
      <textarea
        rows={2}
        className="mt-1 w-full rounded border border-border bg-background px-2 py-1 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
