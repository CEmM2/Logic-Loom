import type { ReactNode } from "react";

import type { RecordsStatus } from "../../api/records";
import { useRecordsStatus, useSelectedStore } from "../../hooks/records";

interface Props {
  title: string;
  subtitle?: ReactNode;
  children: (context: { store: string; status: RecordsStatus }) => ReactNode;
}

/** Shared chrome for every Records sub-tab: title, badges and store selector. */
export function StoreFrame({ title, subtitle, children }: Props) {
  const { data } = useRecordsStatus();
  const { selected, select } = useSelectedStore(data?.stores ?? []);
  if (!data) return null;
  const current = data.stores.find((s) => s.id === selected) ?? null;

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">{title}</h1>
          <span
            className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs font-semibold text-emerald-700 dark:text-emerald-300"
            title="Logic-Loom only reads record stores; it never writes to them."
          >
            read-only
          </span>
          <span
            className="rounded bg-amber-500/15 px-2 py-0.5 text-xs font-semibold text-amber-700 dark:text-amber-300"
            title="akms-records is experimental upstream: its storage format and API may change."
          >
            experimental
          </span>
          {data.version && (
            <span className="text-xs text-muted-foreground">akms-records {data.version}</span>
          )}
        </div>
        {subtitle && <p className="max-w-3xl text-sm text-muted-foreground">{subtitle}</p>}
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm font-medium" htmlFor="records-store">
            Store
          </label>
          <select
            id="records-store"
            className="rounded border border-border bg-background px-2 py-1 text-sm"
            value={selected ?? ""}
            onChange={(event) => select(event.target.value)}
          >
            {data.stores.map((store) => (
              <option key={store.id} value={store.id}>
                {store.id} — {store.scope}
                {store.error ? " (error)" : ""}
              </option>
            ))}
          </select>
          {current && !current.error && (
            <span className="text-xs text-muted-foreground" title={current.root ?? ""}>
              {current.exists
                ? `${current.record_count ?? 0} records · ${current.schema_count ?? 0} schemas`
                : "no records yet"}{" "}
              · <code className="font-mono">{current.root}</code>
            </span>
          )}
          {current?.error && (
            <span className="text-xs text-red-600 dark:text-red-400">
              {current.error.code}: {current.error.message}
            </span>
          )}
        </div>
      </header>
      {selected ? (
        children({ store: selected, status: data })
      ) : (
        <p className="text-sm text-muted-foreground">Select a store to continue.</p>
      )}
    </div>
  );
}
