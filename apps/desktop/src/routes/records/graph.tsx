import { useState } from "react";

import { RecordDetailPanel } from "../../components/records/RecordDetailPanel";
import { GraphLegend, RecordsGraphCanvas } from "../../components/records/RecordsGraphCanvas";
import { StoreFrame } from "../../components/records/StoreFrame";
import { useRecordsGraph, useSelectedRecord } from "../../hooks/records";

/**
 * The record graph. Its own canvas: records are not AKMS knowledge nodes, so
 * this never mixes into `/akms/graph` (a promoted record shows the AKMS node it
 * became as a separate green node instead).
 */
export default function RecordsGraphRoute() {
  return (
    <StoreFrame
      title="Records — Graph"
      subtitle={
        <>
          Edges come from the envelope only: <code>superseded_by</code>, record
          citations (<code>cites</code> / <code>references</code>),{" "}
          <code>promoted_to</code> AKMS nodes, and external <code>source_ref</code>s.
          Click a record to inspect it and focus on its neighbourhood.
        </>
      }
    >
      {({ store }) => <Graph key={store} store={store} />}
    </StoreFrame>
  );
}

function Graph({ store }: { store: string }) {
  const { selected, select } = useSelectedRecord();
  const [focusMode, setFocusMode] = useState(true);
  const [depth, setDepth] = useState(2);
  const [includeExternal, setIncludeExternal] = useState(true);
  const focus = focusMode && selected ? selected : undefined;
  const { data, isLoading, error } = useRecordsGraph(store, {
    focus,
    depth: focus ? depth : undefined,
    include_external: includeExternal,
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4 text-xs">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={includeExternal} onChange={(e) => setIncludeExternal(e.target.checked)} />
          external sources
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={focusMode}
            onChange={(e) => setFocusMode(e.target.checked)}
            disabled={!selected}
          />
          focus on selected record
        </label>
        <label className="flex items-center gap-1">
          depth
          <select
            className="rounded border border-border bg-background px-1 py-0.5"
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            disabled={!focus}
          >
            {[1, 2, 3, 4].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        {selected && (
          <button className="underline" onClick={() => select(null)}>
            show whole store
          </button>
        )}
        {data && (
          <span className="text-muted-foreground">
            {data.nodes.length} nodes · {data.edges.length} edges
            {data.truncated ? ` · truncated from ${data.total_nodes}` : ""}
          </span>
        )}
      </div>
      <GraphLegend />
      {error && (
        <p className="text-sm text-red-600 dark:text-red-400">
          {error instanceof Error ? error.message : "Could not load the graph."}
        </p>
      )}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="h-[640px] rounded border border-border bg-background">
          {isLoading ? (
            <p className="p-4 text-sm text-muted-foreground">Loading graph…</p>
          ) : data ? (
            <RecordsGraphCanvas
              graph={data}
              onSelectRecord={select}
              emptyMessage="This store has no records yet."
            />
          ) : null}
        </div>
        {selected && (
          <div className="max-h-[640px] overflow-y-auto rounded border border-border bg-background p-4">
            <RecordDetailPanel store={store} recordId={selected} onSelectRecord={select} onClose={() => select(null)} />
          </div>
        )}
      </div>
    </div>
  );
}
