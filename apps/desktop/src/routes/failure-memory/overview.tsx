import { WorkspaceFrame } from "../../components/failure-memory/WorkspaceFrame";
import {
  useFailureMemoryCheck,
  useFailureMemoryStatus,
} from "../../hooks/failureMemory";
import type { FailureMemoryStatus, StalenessState } from "../../api/failureMemory";

/**
 * Registry status, staleness, and read-only compiler diagnostics.
 *
 * The staleness shown here is the LOCAL signal: the compiled route index records
 * the registry digest it was built from, so comparing it to the registry's
 * current digest answers "have the lessons moved since the last compile?". It is
 * not the provider fingerprint staleness that gates dispatch elsewhere — that
 * one additionally depends on the published graph and on a caller-supplied
 * `generated_at`, and a consumer that does not control both axes must report
 * rather than block. Logic-Loom controls neither, so it reports.
 */
export default function FailureMemoryOverviewRoute() {
  return (
    <WorkspaceFrame
      title="Failure Memory"
      subtitle={
        <>
          Inspect the lesson registries of explicitly configured projects. Logic-Loom
          reads through the <code>akms-failure-memory</code> package and never edits
          a registry, a generated node, or a route index.
        </>
      }
    >
      {({ project }) => <Overview project={project} />}
    </WorkspaceFrame>
  );
}

function Overview({ project }: { project: string }) {
  const { data, isLoading, error } = useFailureMemoryStatus(project);
  const check = useFailureMemoryCheck(project);

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading status…</p>;
  if (error || !data)
    return <p className="text-sm text-red-600">Could not read this project's status.</p>;

  return (
    <div className="space-y-4">
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label="Lessons" value={data.lesson_count} />
        <Tile label="Path routes" value={data.routed_path_count} />
        <Tile label="Symbol routes" value={data.routed_symbol_count} />
        <Tile label="Graph nodes" value={data.graph.node_count ?? "—"} />
      </section>

      <CurrencyCard status={data} />
      <StalenessCard staleness={data.staleness} />

      <section className="space-y-2 rounded border border-border p-4">
        <h2 className="text-sm font-semibold">Published graph</h2>
        {data.graph.published ? (
          <dl className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-2">
            <Row label="generated_at" value={data.graph.generated_at} />
            <Row label="repo_id" value={data.graph.repo_id} />
            <Row
              label="nodes (document / header)"
              value={`${data.graph.content_node_count} / ${data.graph.node_count ?? "—"}`}
            />
            <Row
              label="routed nodes present"
              value={`${data.graph.routed_node_count - data.graph.missing_routed_node_ids.length} of ${data.graph.routed_node_count}`}
            />
            <Row label="global_vault" value={data.graph.global_vault} />
            <Row
              label="identity"
              value={
                data.graph.identity_matches_config
                  ? `matches repository_id "${data.repository_id}"`
                  : `MISMATCH — configured repository_id is "${data.repository_id}"`
              }
            />
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">
            No graph has been published for this project. Resolution previews need
            one; run <code>failure-memory refresh graph</code> with an explicit{" "}
            <code>--generated-at</code>.
          </p>
        )}
        {data.graph.missing_routed_node_ids.length > 0 && (
          <p className="text-sm text-amber-700 dark:text-amber-300">
            {data.graph.missing_routed_node_ids.length} lesson node(s) named by the
            compiled route index are absent from the published graph, so resolution
            fails for exactly those routes. Counted from the document's own nodes,
            not from its header — a graph can claim nodes it does not contain.
          </p>
        )}
        {data.graph.published && data.graph.identity_matches_config === false && (
          <p className="text-sm text-amber-700 dark:text-amber-300">
            The graph was published under a different repository identity. The project
            configuration's <code>repository_id</code> is the single authority, so this
            graph reads as stale until an explicit graph refresh republishes it.
          </p>
        )}
      </section>

      <section className="space-y-2 rounded border border-border p-4">
        <h2 className="text-sm font-semibold">Artifacts</h2>
        <table className="w-full text-left text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1">key</th>
              <th>path</th>
              <th>policy</th>
              <th>sha256</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {data.artifacts.map((artifact) => (
              <tr key={artifact.key} className="border-t border-border/60">
                <td className="py-1">{artifact.key}</td>
                <td className="truncate">{artifact.path}</td>
                <td>{artifact.policy ?? (artifact.key === "registry" ? "canonical" : "—")}</td>
                <td className="truncate">
                  {artifact.exists ? (artifact.sha256 ?? "directory") : "absent"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="space-y-2 rounded border border-border p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Diagnostics</h2>
          <button
            type="button"
            onClick={() => check.mutate()}
            disabled={check.isPending}
            className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground disabled:opacity-50"
          >
            {check.isPending ? "Checking…" : "Run validate + check"}
          </button>
        </div>
        <p className="text-xs text-muted-foreground">
          Runs the compiler in its two non-writing modes: <code>validate</code> (is the
          registry well-formed?) and <code>check</code> (do the committed generated
          bytes still match?). Nothing is published. Expect a WHOLE-tree diff after
          any recording — every generated node embeds the whole-registry digest, so
          one new lesson rewrites every node file.
        </p>
        {check.isError && (
          <p className="text-sm text-red-600">
            {(check.error as Error).message}
          </p>
        )}
        {check.data && (
          <div className="space-y-2 text-sm">
            <p>
              <strong>{check.data.status}</strong> ·{" "}
              {String((check.data.check?.counts as Record<string, number>)?.changed ?? 0)}{" "}
              changed ·{" "}
              {String((check.data.check?.counts as Record<string, number>)?.added ?? 0)} added ·{" "}
              {String((check.data.check?.counts as Record<string, number>)?.stale ?? 0)} stale
            </p>
            {!check.data.global_vault_exists && (
              <p className="text-xs text-amber-700 dark:text-amber-300">
                The configured read-only global vault ({check.data.global_vault}) does not
                exist, so the node-id collision check had nothing to compare against.
              </p>
            )}
            {check.data.warnings.length > 0 && (
              <details className="text-xs">
                <summary>{check.data.warnings.length} compiler warning(s)</summary>
                <ul className="mt-1 space-y-1 font-mono">
                  {check.data.warnings.map((warning, index) => (
                    <li key={index}>
                      [{warning.code}] {warning.lesson_id} {warning.path} — {warning.message}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </section>

      <section className="space-y-1 rounded border border-border p-4 text-xs text-muted-foreground">
        <h2 className="text-sm font-semibold text-foreground">Identity</h2>
        <p>
          config <code className="font-mono">{data.config_path}</code>
        </p>
        <p>
          root <code className="font-mono">{data.root}</code>
        </p>
        <p>
          config_fingerprint <code className="font-mono">{data.config_fingerprint}</code>
        </p>
      </section>
    </div>
  );
}

/**
 * The five-state currency answer.
 *
 * `unverifiable` gets its own tone and its own words rather than being folded
 * into either neighbour: "we could not establish currency" is a different fact
 * from "it is current" and from "it has moved", and the operator's next action
 * differs in each case. The card also says explicitly that detection does not
 * depend on the repo2md toolchain, so an unavailable refresh is never misread as
 * an inability to notice a change.
 */
function CurrencyCard({ status }: { status: FailureMemoryStatus }) {
  const tone: Record<FailureMemoryStatus["currency"], string> = {
    current: "border-emerald-500/60 bg-emerald-500/10",
    stale: "border-amber-500/60 bg-amber-500/10",
    unverifiable: "border-blue-500/60 bg-blue-500/10",
    uncompiled: "border-border bg-muted/40",
    never_written: "border-border bg-muted/40",
  };
  const heading: Record<FailureMemoryStatus["currency"], string> = {
    current: "Current — verified",
    stale: "Stale — the recorded knowledge has moved",
    unverifiable: "Unverifiable — currency could not be established",
    uncompiled: "Not compiled yet",
    never_written: "Never written to",
  };
  return (
    <section
      data-currency={status.currency}
      className={`space-y-1 rounded border p-4 ${tone[status.currency]}`}
    >
      <h2 className="text-sm font-semibold">{heading[status.currency]}</h2>
      <p className="text-sm">{status.currency_reason}</p>
      {status.currency === "unverifiable" && (
        <p className="text-xs">
          This is deliberately not reported as current. Logic-Loom could not
          establish that what this project would resolve today matches the
          registry as it stands. On this inspection surface that is
          informational — for any consumer that <em>enforces</em> on freshness it
          is a blocking, fail-closed state and must never be treated as current.
        </p>
      )}
      {status.currency === "stale" && !status.lock_parent_exists && (
        <p className="text-xs">
          Reported stale even though this checkout has no materialised{" "}
          <code>runtime/</code> directory: content evidence outranks the absence
          of a disposable scratch path.
        </p>
      )}
      <p className="text-xs opacity-80">
        Detecting that the registry moved needs only its digest and the compiled
        route-index hash — never the repo2md toolchain. An unavailable refresh
        blocks fixing this, never noticing it.
      </p>
    </section>
  );
}

function StalenessCard({ staleness }: { staleness: StalenessState }) {
  const tone =
    staleness.state === "current"
      ? "border-emerald-500/60 bg-emerald-500/10"
      : staleness.state === "stale"
        ? "border-amber-500/60 bg-amber-500/10"
        : "border-border bg-muted/40";
  return (
    <section className={`space-y-1 rounded border p-4 ${tone}`} data-staleness={staleness.state}>
      <h2 className="text-sm font-semibold">Route index: {staleness.state}</h2>
      <p className="text-sm">{staleness.reason}</p>
      <p className="font-mono text-[11px] opacity-70">
        registry {staleness.registry_sha256 ?? "—"} · routes{" "}
        {staleness.routes_source_hash ?? "—"}
      </p>
    </section>
  );
}

function Tile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono text-xs">{value ?? "—"}</dd>
    </>
  );
}
