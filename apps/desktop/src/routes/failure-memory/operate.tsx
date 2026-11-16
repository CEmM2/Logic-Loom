import { JobPanel } from "../../components/failure-memory/JobPanel";
import { RecordForm } from "../../components/failure-memory/RecordForm";
import { WorkspaceFrame } from "../../components/failure-memory/WorkspaceFrame";
import {
  useCompileProject,
  useFailureMemoryJobs,
  useRefreshProject,
  useTrackedWriteJob,
  useWriteDiagnostics,
} from "../../hooks/failureMemory";
import type { RefreshAction, WriteDiagnostics } from "../../api/failureMemory";

/**
 * Operator mode: recording form, compile/refresh jobs, write diagnostics.
 *
 * Reachable only when the backend reports `operator: true`, which comes from
 * `failure_memory_mode` in `config.toml` (or `LOGIC_LOOM_FAILURE_MEMORY_MODE`)
 * and is deliberately not settable over HTTP. The tab is hidden otherwise, and
 * the backend refuses independently — the hidden tab is convenience, the 403 is
 * the control.
 */
export default function FailureMemoryOperateRoute() {
  return (
    <WorkspaceFrame
      title="Failure Memory — Record"
      subtitle="Record, compile and refresh through the package. Logic-Loom never edits a registry itself."
    >
      {({ project, projects }) =>
        projects.operator ? (
          <Operate project={project} />
        ) : (
          <p className="rounded border border-border p-4 text-sm text-muted-foreground">
            Logic-Loom is running in <code>{projects.mode}</code> mode and cannot
            write. Set <code>failure_memory_mode = "operator"</code> in your
            Logic-Loom <code>config.toml</code> and reload the sidecar — the mode
            is intentionally not changeable from this window.
          </p>
        )
      }
    </WorkspaceFrame>
  );
}

function Operate({ project }: { project: string }) {
  const { data: diagnostics } = useWriteDiagnostics(project);
  const { data: jobs } = useFailureMemoryJobs(project);
  const compile = useCompileProject(project);
  const refresh = useRefreshProject(project);
  // A compile/refresh 202 is not an outcome. Tracking the returned job polls
  // it to its terminal state and refreshes status/lessons/routes/jobs, so the
  // panel below always shows how the operation ended.
  const tracked = useTrackedWriteJob(project);

  if (!diagnostics) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-6">
      <Diagnostics diagnostics={diagnostics} />

      <section className="space-y-3 rounded border border-border p-4">
        <h2 className="text-sm font-semibold">Record a lesson</h2>
        <RecordForm project={project} diagnostics={diagnostics} />
      </section>

      <section className="space-y-3 rounded border border-border p-4">
        <h2 className="text-sm font-semibold">Jobs</h2>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={!diagnostics.can_compile || compile.isPending}
            onClick={() => void compile.mutateAsync().then(tracked.track)}
            className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground disabled:opacity-50"
          >
            Compile (write)
          </button>
          {(["preflight", "lessons", "mirror", "graph", "all"] as RefreshAction[]).map(
            (action) => (
              <button
                key={action}
                type="button"
                disabled={
                  refresh.isPending ||
                  (action !== "preflight" && !diagnostics.can_refresh)
                }
                onClick={() => void refresh.mutateAsync(action).then(tracked.track)}
                className="rounded border border-border px-3 py-1.5 text-sm disabled:opacity-50"
                title={
                  action !== "preflight" && !diagnostics.can_refresh
                    ? diagnostics.refresh_reason
                    : undefined
                }
              >
                refresh {action}
              </button>
            ),
          )}
        </div>
        {compile.isError && (
          <p className="text-sm text-red-600">{(compile.error as Error).message}</p>
        )}
        {refresh.isError && (
          <p className="text-sm text-red-600">{(refresh.error as Error).message}</p>
        )}
        <p className="text-xs text-muted-foreground">
          A compile or a recording rewrites <em>every</em> generated node — each
          embeds the whole-registry digest — so a full-tree diff is the correct
          result of adding one lesson, not damage. <code>clean</code> is not
          offered here: it deletes every disposable artifact and belongs on the
          CLI, not one click from a lesson inventory.
        </p>
        <JobPanel jobs={jobs ?? []} />
      </section>
    </div>
  );
}

function Diagnostics({ diagnostics }: { diagnostics: WriteDiagnostics }) {
  const tone =
    diagnostics.blockers.length === 0
      ? "border-emerald-500/60 bg-emerald-500/10"
      : "border-amber-500/60 bg-amber-500/10";
  return (
    <section className={`space-y-2 rounded border p-4 text-sm ${tone}`}>
      <h2 className="text-sm font-semibold">Write permissions</h2>
      <ul className="grid gap-1 sm:grid-cols-3">
        <Capability label="record" allowed={diagnostics.can_record} />
        <Capability label="compile" allowed={diagnostics.can_compile} />
        <Capability label="refresh" allowed={diagnostics.can_refresh} />
      </ul>

      {diagnostics.never_written && (
        <p className="text-xs">
          This project has never been written to: it owns no registry, route
          index, generated nodes or graph. Not a fault — record or compile to
          bootstrap it.
        </p>
      )}

      {diagnostics.refresh_classification === "unavailable" && (
        <div className="space-y-1 text-xs" data-refresh-classification="unavailable">
          <p className="font-semibold">Refresh unavailable — fix the toolchain</p>
          <p>{diagnostics.refresh_reason}</p>
          <p>
            This is explicitly <strong>not</strong> staleness. Nothing about the
            recorded knowledge has changed; the pinned repo2md distribution this
            install would need is simply not present.
          </p>
          <ul className="list-disc pl-5 font-mono">
            {diagnostics.toolchain
              .filter((requirement) => !requirement.satisfied)
              .map((requirement) => (
                <li key={requirement.name}>
                  {requirement.name}: {requirement.detail}
                </li>
              ))}
          </ul>
          <p className="font-semibold">
            Required: repo2md {diagnostics.repo2md_required_version} at commit{" "}
            <code className="font-mono">{diagnostics.repo2md_required_commit}</code>
          </p>
          <ol className="list-decimal space-y-1 pl-5">
            {diagnostics.refresh_remediation.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          {diagnostics.detection_still_works && (
            <p>
              Staleness <strong>detection</strong> is unaffected — it reads the
              registry digest and the route-index hash. Only republishing the
              canonical graph is blocked.
            </p>
          )}
        </div>
      )}

      <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
        <dt className="opacity-70">global vault</dt>
        <dd className="truncate font-mono">
          {diagnostics.global_vault} {diagnostics.global_vault_exists ? "" : "(missing)"}
        </dd>
        <dt className="opacity-70">lock</dt>
        <dd className="truncate font-mono">{diagnostics.lock_path}</dd>
        <dt className="opacity-70">generated_at (derived from registry content)</dt>
        <dd className="truncate font-mono">{diagnostics.derived_generated_at ?? "—"}</dd>
        <dt className="opacity-70">derived from digest</dt>
        <dd className="truncate font-mono">{diagnostics.generated_at_source || "—"}</dd>
      </dl>
      <p className="text-xs opacity-80">
        <code>generated_at</code> is derived from the registry digest, never the
        wall clock: identical content must derive an identical instant, or every
        later freshness check reads as stale.
      </p>
    </section>
  );
}

function Capability({ label, allowed }: { label: string; allowed: boolean }) {
  return (
    <li data-capability={label} data-allowed={String(allowed)}>
      {allowed ? "✓" : "✗"} {label}
    </li>
  );
}
