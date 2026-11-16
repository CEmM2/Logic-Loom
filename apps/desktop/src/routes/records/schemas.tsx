import { Link } from "react-router-dom";

import { StoreFrame } from "../../components/records/StoreFrame";
import { useRecordSchemas } from "../../hooks/records";

/** The JSON Schemas registered in the store — the user-defined record shapes. */
export default function RecordsSchemasRoute() {
  return (
    <StoreFrame
      title="Records — Schemas"
      subtitle="Each record's payload follows one of these registered JSON Schemas (Draft 2020-12)."
    >
      {({ store }) => <Schemas store={store} />}
    </StoreFrame>
  );
}

function Schemas({ store }: { store: string }) {
  const { data, isLoading } = useRecordSchemas(store);
  if (isLoading) return <p className="text-sm text-muted-foreground">Loading schemas…</p>;
  if (!data || data.schemas.length === 0) {
    return <p className="text-sm text-muted-foreground">No schemas are registered in this store yet.</p>;
  }
  return (
    <div className="space-y-3">
      {data.schemas.map((entry) => {
        const properties = Object.entries(
          ((entry.schema as { properties?: Record<string, { description?: string; type?: unknown }> } | undefined)
            ?.properties) ?? {},
        );
        const required = new Set(((entry.schema as { required?: string[] } | undefined)?.required) ?? []);
        return (
          <details key={entry.schema_ref} className="rounded border border-border p-3">
            <summary className="cursor-pointer">
              <span className="font-mono text-sm font-semibold">{entry.schema_ref}</span>{" "}
              {entry.title && <span className="text-sm">— {entry.title}</span>}{" "}
              <span className="text-xs text-muted-foreground">
                {entry.record_count} record(s)
                {entry.registered_at ? ` · registered ${entry.registered_at.slice(0, 10)}` : ""}
              </span>
              {entry.record_count > 0 && (
                <Link
                  className="ml-3 text-xs underline"
                  to={`/records/browse?store=${encodeURIComponent(store)}`}
                >
                  browse
                </Link>
              )}
            </summary>
            {entry.error ? (
              <p className="mt-2 text-xs text-red-600 dark:text-red-400">
                {entry.error.code}: {entry.error.message}
              </p>
            ) : (
              <div className="mt-3 space-y-3">
                {properties.length > 0 && (
                  <table className="w-full text-xs">
                    <tbody>
                      {properties.map(([name, prop]) => (
                        <tr key={name} className="border-t border-border align-top">
                          <td className="py-1 pr-3 font-mono">
                            {name}
                            {required.has(name) && <span className="text-red-600">*</span>}
                          </td>
                          <td className="pr-3 text-muted-foreground">{JSON.stringify(prop.type ?? "")}</td>
                          <td>{prop.description}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <details>
                  <summary className="cursor-pointer text-xs text-muted-foreground">Raw JSON Schema</summary>
                  <pre className="mt-1 max-h-96 overflow-auto rounded bg-muted p-2 text-[11px]">
                    {JSON.stringify(entry.schema, null, 2)}
                  </pre>
                </details>
              </div>
            )}
          </details>
        );
      })}
    </div>
  );
}
