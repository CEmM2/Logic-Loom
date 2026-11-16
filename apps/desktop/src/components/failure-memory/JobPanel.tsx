import type { Job } from "../../api/failureMemory";

/**
 * Write-job status.
 *
 * The hazard this component exists to prevent is a partially accepted
 * UI state: a recording that LOOKS accepted while its compile later failed. So
 * `queued` and `running` are rendered as explicitly non-final — "not finished
 * yet", never a tick — and only a job the backend reports as `succeeded`, which
 * it does only after the package returned, is shown as done.
 *
 * `failed` shows the package's own error code rather than a paraphrase, because
 * the codes are the actionable part: `repo2md_missing` means fix the toolchain,
 * `lock_parent_missing` means this project has never been written to,
 * `record_request` means the proposal was malformed.
 */

const TONE: Record<Job["status"], string> = {
  queued: "border-border bg-muted/40 text-muted-foreground",
  running: "border-blue-500/60 bg-blue-500/10 text-blue-900 dark:text-blue-200",
  succeeded:
    "border-emerald-500/60 bg-emerald-500/10 text-emerald-900 dark:text-emerald-200",
  failed: "border-red-500/60 bg-red-500/10 text-red-900 dark:text-red-200",
};

const LABEL: Record<Job["status"], string> = {
  queued: "Queued — not finished",
  running: "Running — not finished",
  succeeded: "Succeeded",
  failed: "Failed",
};

export function JobPanel({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No write jobs yet for this project.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {jobs.map((job) => (
        <JobRow key={job.id} job={job} />
      ))}
    </ul>
  );
}

export function JobRow({ job }: { job: Job }) {
  const pending = job.status === "queued" || job.status === "running";
  return (
    <li
      data-job-status={job.status}
      data-job-action={job.action}
      className={`space-y-1 rounded border p-3 text-sm ${TONE[job.status]}`}
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-semibold">{LABEL[job.status]}</span>
        <span className="text-xs opacity-80">
          {job.action}
          {job.detail ? ` · ${job.detail}` : ""}
        </span>
        <span className="ml-auto font-mono text-[10px] opacity-60">{job.id.slice(0, 8)}</span>
      </div>

      {pending && (
        <p className="text-xs">
          The package has not reported an outcome yet. Nothing is confirmed until
          it does.
        </p>
      )}

      {job.status === "succeeded" && job.summary && <p className="text-xs">{job.summary}</p>}

      {job.status === "failed" && job.error && (
        <div className="space-y-1 text-xs">
          <p>
            <code className="font-mono font-semibold">{job.error.code}</code>
          </p>
          <p>{job.error.message}</p>
          {job.error.code.startsWith("repo2md_") && (
            <p>
              This is a TOOLCHAIN shortfall, not stale knowledge: the mutating
              refresh actions need a working mirror exporter — the builtin one
              shipped in akms, or, for projects with{" "}
              <code>mirror_provider = "repo2md"</code>, a pinned, clean, editable
              repo2md checkout. Nothing about your recorded lessons has changed.
            </p>
          )}
          {job.error.code === "lock_parent_missing" && (
            <p>
              This project has never been written to. Recording a lesson or
              compiling will create the directory the project lock needs.
            </p>
          )}
        </div>
      )}
    </li>
  );
}
