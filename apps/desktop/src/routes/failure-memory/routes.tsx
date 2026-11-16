import { WorkspaceFrame } from "../../components/failure-memory/WorkspaceFrame";
import { useFailureMemoryRoutes } from "../../hooks/failureMemory";

/**
 * Route coverage — the compiled index the provider actually reads.
 *
 * Deliberately the COMMITTED route index rather than a fresh in-memory compile,
 * so what is shown here is what a resolution will use. Two properties routinely
 * surprise people and are called out inline rather than left to be rediscovered:
 * an empty `by_symbol` map is normal (a symbol route is emitted only when the
 * referenced file exists in the checkout), and a route whose source path is
 * missing from the checkout is still a live route.
 */
export default function FailureMemoryRoutesRoute() {
  return (
    <WorkspaceFrame
      title="Failure Memory — Routes"
      subtitle="Compiled path and symbol routes, and how much of the registry they cover."
    >
      {({ project }) => <Routes project={project} />}
    </WorkspaceFrame>
  );
}

function Routes({ project }: { project: string }) {
  const { data, isLoading } = useFailureMemoryRoutes(project);

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading routes…</p>;
  if (!data) return null;

  if (!data.compiled) {
    return (
      <p className="rounded border border-amber-500/60 bg-amber-500/10 p-4 text-sm">
        No compiled route index exists for this project yet, so no task can match any
        lesson by path. Run <code>failure-memory compile</code>.
      </p>
    );
  }

  const coverage = data.coverage;
  return (
    <div className="space-y-4">
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label="Path routes" value={coverage.path_route_count} />
        <Tile label="Symbol routes" value={coverage.symbol_route_count} />
        <Tile
          label="Routed lessons"
          value={`${coverage.routed_lesson_count} / ${coverage.lesson_count}`}
        />
        <Tile label="Documentation paths" value={coverage.documentation_path_count} />
      </section>

      {coverage.symbol_route_count === 0 && coverage.path_route_count > 0 && (
        <p className="text-xs text-muted-foreground">
          No symbol routes. That is normal, not a fault: the compiler emits a symbol
          route only when the referenced file exists in this checkout.
        </p>
      )}

      {coverage.unrouted_lesson_ids.length > 0 && (
        <p className="rounded border border-red-500/60 bg-red-500/10 p-3 text-sm">
          {coverage.unrouted_lesson_ids.length} lesson(s) have no compiled route at all
          and can never be matched by path:{" "}
          <span className="font-mono">{coverage.unrouted_lesson_ids.join(", ")}</span>
        </p>
      )}

      {coverage.missing_source_paths.length > 0 && (
        <details className="rounded border border-border p-3 text-xs">
          <summary className="cursor-pointer">
            {coverage.missing_source_paths.length} routed path(s) do not exist in this
            checkout
          </summary>
          <p className="mt-2 text-muted-foreground">
            The routes stay live — a lesson about a file that has since moved is still a
            lesson — but a task declaring today's path will not match them.
          </p>
          <ul className="mt-2 font-mono">
            {coverage.missing_source_paths.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
        </details>
      )}

      <section className="rounded border border-border">
        <table className="w-full text-left text-xs">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border">
              <th className="p-2">kind</th>
              <th>key</th>
              <th>lesson</th>
              <th>matched by</th>
            </tr>
          </thead>
          <tbody>
            {data.routes.map((route) => (
              <tr key={route.overlay_edge_id} className="border-b border-border/50">
                <td className="p-2">{route.kind}</td>
                <td className="font-mono">
                  {route.key}
                  {route.documentation && (
                    <span className="ml-2 rounded bg-amber-500/15 px-1 py-0.5 text-[10px] text-amber-700 dark:text-amber-300">
                      docs
                    </span>
                  )}
                  {route.source_exists === false && (
                    <span className="ml-2 text-muted-foreground">(missing)</span>
                  )}
                </td>
                <td className="font-mono">
                  {route.lesson_id ?? "—"} / {route.node_id}
                </td>
                <td>{route.reasons.map((reason) => reason.source_field).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p className="font-mono text-[11px] text-muted-foreground">
        {data.schema_version} · {data.source_hash} · {data.staleness.state}
      </p>
    </div>
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
