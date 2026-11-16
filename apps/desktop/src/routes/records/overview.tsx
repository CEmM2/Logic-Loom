import { Link } from "react-router-dom";

import { useRecordsStatus } from "../../hooks/records";

/** Every configured store at a glance, with links into Browse and Graph. */
export default function RecordsOverviewRoute() {
  const { data } = useRecordsStatus();
  if (!data) return null;

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">Records</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Typed records kept by <code>akms-records</code> {data.version} — decisions,
          findings, lessons or whatever schemas you registered. Logic-Loom reads the
          stores listed in <code>[[records_stores]]</code> and never writes to them.
          akms-records is experimental upstream; its format may still change.
        </p>
      </header>
      <table className="w-full max-w-4xl text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-1">Store</th>
            <th>Scope</th>
            <th>Records</th>
            <th>Schemas</th>
            <th>Root</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.stores.map((store) => {
            const q = `?store=${encodeURIComponent(store.id)}`;
            return (
              <tr key={store.id} className="border-t border-border align-top">
                <td className="py-2 font-medium">{store.id}</td>
                <td>
                  <code className="font-mono text-xs">{store.scope}</code>
                </td>
                <td>{store.exists ? store.record_count : "—"}</td>
                <td>{store.exists ? store.schema_count : "—"}</td>
                <td className="max-w-xs truncate text-xs text-muted-foreground" title={store.root ?? ""}>
                  {store.error ? (
                    <span className="text-red-600 dark:text-red-400">
                      {store.error.code}: {store.error.message}
                    </span>
                  ) : store.exists ? (
                    store.root
                  ) : (
                    `${store.root} (no records yet)`
                  )}
                  {store.replay_errors > 0 && (
                    <span className="block text-amber-700 dark:text-amber-300">
                      {store.replay_errors} record(s) failed to replay
                    </span>
                  )}
                </td>
                <td className="space-x-3 whitespace-nowrap text-xs">
                  <Link className="underline" to={`/records/browse${q}`}>
                    Browse
                  </Link>
                  <Link className="underline" to={`/records/graph${q}`}>
                    Graph
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
