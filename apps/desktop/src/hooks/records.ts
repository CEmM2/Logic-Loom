import { useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import {
  getRecord,
  getRecordsGraph,
  getRecordsStatus,
  listRecordSchemas,
  listRecords,
  type GraphOptions,
  type RecordFilters,
} from "../api/records";

export function useRecordsStatus() {
  return useQuery({
    queryKey: ["records", "status"],
    queryFn: getRecordsStatus,
    staleTime: 30_000,
  });
}

/** The selected store, kept in `?store=` so a deep link survives a reload. */
export function useSelectedStore(candidates: { id: string; error: unknown }[]) {
  const [params, setParams] = useSearchParams();
  const requested = params.get("store");

  const selected = useMemo(() => {
    if (requested && candidates.some((c) => c.id === requested)) return requested;
    return candidates.find((c) => !c.error)?.id ?? candidates[0]?.id ?? null;
  }, [requested, candidates]);

  const select = useCallback(
    (id: string) => {
      const next = new URLSearchParams(params);
      next.set("store", id);
      next.delete("record");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  return { selected, select };
}

/** The record opened in the detail panel, kept in `?record=`. */
export function useSelectedRecord() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("record");
  const select = useCallback(
    (id: string | null) => {
      const next = new URLSearchParams(params);
      if (id) next.set("record", id);
      else next.delete("record");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );
  return { selected, select };
}

export function useRecordsList(store: string | null, filters: RecordFilters) {
  return useQuery({
    queryKey: ["records", "list", store, filters],
    queryFn: () => listRecords(store as string, filters),
    enabled: Boolean(store),
  });
}

export function useRecord(store: string | null, recordId: string | null) {
  return useQuery({
    queryKey: ["records", "record", store, recordId],
    queryFn: () => getRecord(store as string, recordId as string),
    enabled: Boolean(store && recordId),
  });
}

export function useRecordSchemas(store: string | null) {
  return useQuery({
    queryKey: ["records", "schemas", store],
    queryFn: () => listRecordSchemas(store as string),
    enabled: Boolean(store),
  });
}

export function useRecordsGraph(store: string | null, options: GraphOptions) {
  return useQuery({
    queryKey: ["records", "graph", store, options],
    queryFn: () => getRecordsGraph(store as string, options),
    enabled: Boolean(store),
  });
}
