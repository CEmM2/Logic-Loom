import { Group, Panel, Separator } from "react-resizable-panels";
import { useMemo, useState } from "react";

import type { FilterSpec } from "../../api/picker";
import { BatchDetailPane } from "../../components/picker/BatchDetailPane";
import { BatchList } from "../../components/picker/BatchList";
import { FilterBar } from "../../components/picker/FilterBar";
import { PaperGrid } from "../../components/picker/PaperGrid";
import { useAddPapers, useBatches, usePapers } from "../../hooks/picker";

export default function PickerRoute() {
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterSpec>({ limit: 200 });
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());

  const { data: batches = [], isLoading: batchesLoading } = useBatches();
  const { data: papers, isFetching: papersLoading } = usePapers(filter);
  const addPapers = useAddPapers();

  const toggleKey = (citekey: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(citekey)) next.delete(citekey);
      else next.add(citekey);
      return next;
    });
  };

  const selectBatch = (id: string) => {
    setSelectedBatchId(id);
    setSelectedKeys(new Set());
  };

  const onAdd = () => {
    if (!selectedBatchId || selectedKeys.size === 0) return;
    addPapers.mutate(
      { batchId: selectedBatchId, citekeys: [...selectedKeys] },
      {
        onSuccess: () => setSelectedKeys(new Set()),
      },
    );
  };

  const filterWithSuggest = useMemo<FilterSpec>(() => {
    if (!selectedBatchId) return filter;
    return { ...filter, suggest_for: selectedBatchId };
  }, [filter, selectedBatchId]);

  const { data: suggestedPapers, isFetching: suggestLoading } = usePapers(filterWithSuggest);
  const list = selectedBatchId ? suggestedPapers : papers;
  const loading = selectedBatchId ? suggestLoading : papersLoading;

  return (
    <Group orientation="horizontal" className="h-full">
      <Panel defaultSize={20} minSize={14} className="flex flex-col border-r border-border overflow-hidden">
        <div className="border-b border-border p-3 text-xs uppercase tracking-wide text-muted-foreground shrink-0">
          Batches
        </div>
        <div className="flex-1 overflow-y-auto">
          {batchesLoading ? (
            <div className="p-3 text-sm text-muted-foreground">Loading…</div>
          ) : (
            <BatchList
              batches={batches}
              selectedId={selectedBatchId}
              onSelect={selectBatch}
            />
          )}
        </div>
      </Panel>

      <Separator className="w-1 bg-border hover:bg-primary/30 transition-colors cursor-col-resize" />

      <Panel defaultSize={55} minSize={30} className="flex flex-col overflow-hidden">
        <FilterBar value={filter} onChange={setFilter} />
        <div className="flex-1 overflow-hidden">
          <PaperGrid
            papers={list?.results ?? []}
            total={list?.total ?? 0}
            selectedKeys={selectedKeys}
            onToggle={toggleKey}
            busy={loading}
          />
        </div>
        <div className="flex items-center gap-2 border-t border-border p-2 text-sm shrink-0">
          <button
            onClick={onAdd}
            disabled={!selectedBatchId || selectedKeys.size === 0 || addPapers.isPending}
            className="rounded bg-foreground px-3 py-1 text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Add {selectedKeys.size || ""} to batch
          </button>
          {addPapers.isError && (
            <span className="text-xs text-red-600">
              {(addPapers.error as Error).message}
            </span>
          )}
        </div>
      </Panel>

      <Separator className="w-1 bg-border hover:bg-primary/30 transition-colors cursor-col-resize" />

      <Panel defaultSize={25} minSize={16} className="overflow-hidden border-l border-border">
        <BatchDetailPane batchId={selectedBatchId} />
      </Panel>
    </Group>
  );
}
