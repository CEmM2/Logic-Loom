import { useState } from "react";

import type { RecordFacets, RecordFilters } from "../../api/records";
import { RecordDetailPanel } from "../../components/records/RecordDetailPanel";
import { StoreFrame } from "../../components/records/StoreFrame";
import { useRecordsList, useSelectedRecord } from "../../hooks/records";

export default function RecordsBrowseRoute() {
  return (
    <StoreFrame
      title="Records — Browse"
      subtitle="Filter on envelope fields every record has; payload fields come from your own schemas."
    >
      {({ store }) => <Browser key={store} store={store} />}
    </StoreFrame>
  );
}

function Browser({ store }: { store: string }) {
  const [filters, setFilters] = useState<RecordFilters>({});
  const [text, setText] = useState("");
  const { selected, select } = useSelectedRecord();
  const { data, isLoading, error } = useRecordsList(store, filters);

  const set = (key: keyof RecordFilters, value: string) =>
    setFilters((current) => ({ ...current, [key]: value || undefined }));

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section className="space-y-3">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            set("q", text.trim());
          }}
        >
          <input
            aria-label="Search records"
            placeholder="Search summary, tags, payload…"
            className="min-w-[14rem] flex-1 rounded border border-border bg-background px-2 py-1 text-sm"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <button type="submit" className="rounded border border-border px-3 py-1 text-sm hover:bg-accent/50">
            Search
          </button>
        </form>
        {data && <FacetFilters facets={data.facets} filters={filters} onChange={set} />}

        {isLoading && <p className="text-sm text-muted-foreground">Loading records…</p>}
        {error && (
          <p className="text-sm text-red-600 dark:text-red-400">
            {error instanceof Error ? error.message : "Could not list records."}
          </p>
        )}
        {data && (
          <>
            <p className="text-xs text-muted-foreground">
              {data.total} record(s){data.total > data.records.length ? `, showing ${data.records.length}` : ""}
            </p>
            {data.replay_errors.length > 0 && (
              <p className="text-xs text-amber-700 dark:text-amber-300">
                {data.replay_errors.length} record(s) failed to replay and are not listed:{" "}
                {data.replay_errors.map((e) => `${e.record_id} (${e.code})`).join(", ")}
              </p>
            )}
            <ul className="divide-y divide-border rounded border border-border bg-background">
              {data.records.map((row) => (
                <li key={row.record_id}>
                  <button
                    onClick={() => select(row.record_id)}
                    className={`block w-full px-3 py-2 text-left hover:bg-accent/40 ${
                      selected === row.record_id ? "bg-accent/60" : ""
                    } ${row.lifecycle !== "active" ? "opacity-70" : ""}`}
                  >
                    <div className="truncate text-sm font-medium">{row.summary}</div>
                    <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                      <span>{row.schema_ref}</span>
                      <span>{row.lifecycle}</span>
                      <span>{row.review_state}</span>
                      <span>{row.recorded_at.slice(0, 10)}</span>
                      {row.tags.slice(0, 4).map((tag) => (
                        <span key={tag}>#{tag}</span>
                      ))}
                    </div>
                  </button>
                </li>
              ))}
              {data.records.length === 0 && (
                <li className="px-3 py-6 text-center text-sm text-muted-foreground">No matching records.</li>
              )}
            </ul>
          </>
        )}
      </section>
      <section className="min-w-0 rounded border border-border bg-background p-4">
        {selected ? (
          <RecordDetailPanel store={store} recordId={selected} onSelectRecord={select} onClose={() => select(null)} />
        ) : (
          <p className="text-sm text-muted-foreground">Select a record to see its payload, links and history.</p>
        )}
      </section>
    </div>
  );
}

const FACETS: { key: keyof RecordFacets; filter: keyof RecordFilters; label: string }[] = [
  { key: "schema_ref", filter: "schema", label: "Schema" },
  { key: "lifecycle", filter: "lifecycle", label: "Lifecycle" },
  { key: "review_state", filter: "review_state", label: "Review" },
  { key: "tags", filter: "tag", label: "Tag" },
];

function FacetFilters({
  facets,
  filters,
  onChange,
}: {
  facets: RecordFacets;
  filters: RecordFilters;
  onChange: (key: keyof RecordFilters, value: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {FACETS.map(({ key, filter, label }) => (
        <label key={key} className="flex items-center gap-1 text-xs">
          {label}
          <select
            className="rounded border border-border bg-background px-1 py-0.5 text-xs"
            value={(filters[filter] as string | undefined) ?? ""}
            onChange={(event) => onChange(filter, event.target.value)}
          >
            <option value="">all</option>
            {Object.entries(facets[key])
              .sort((a, b) => b[1] - a[1])
              .map(([value, count]) => (
                <option key={value} value={value}>
                  {value} ({count})
                </option>
              ))}
          </select>
        </label>
      ))}
    </div>
  );
}
