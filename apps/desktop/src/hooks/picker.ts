/**
 * TanStack Query hooks wrapping the picker API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import * as nlm from "../api/nlm";
import type { GenerateRequest, NlmBatchRequest, NotebookRequest, ValidateRequest } from "../api/nlm";
import * as picker from "../api/picker";
import type { FilterSpec } from "../api/picker";

export function useBatches() {
  return useQuery({ queryKey: ["picker", "batches"], queryFn: picker.listBatches });
}

export function useBatch(id: string | null) {
  return useQuery({
    queryKey: ["picker", "batches", id],
    queryFn: () => picker.getBatch(id!),
    enabled: !!id,
  });
}

export function usePapers(spec: FilterSpec) {
  return useQuery({
    queryKey: ["picker", "papers", spec],
    queryFn: () => picker.listPapers(spec),
  });
}

export function useCollections() {
  return useQuery({
    queryKey: ["picker", "collections"],
    queryFn: picker.listCollections,
  });
}

export function useSavedQueries() {
  return useQuery({
    queryKey: ["picker", "saved_queries"],
    queryFn: picker.listSavedQueries,
  });
}

export function useAddPapers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { batchId: string; citekeys: string[] }) =>
      picker.addPapers(vars.batchId, vars.citekeys),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["picker", "batches"] });
      qc.invalidateQueries({ queryKey: ["picker", "batches", vars.batchId] });
    },
  });
}

export function useRemovePapers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { batchId: string; citekeys: string[] }) =>
      picker.removePapers(vars.batchId, vars.citekeys),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["picker", "batches"] });
      qc.invalidateQueries({ queryKey: ["picker", "batches", vars.batchId] });
    },
  });
}

export function useSaveQuery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { name: string; spec: FilterSpec }) =>
      picker.saveQuery(vars.name, vars.spec),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["picker", "saved_queries"] }),
  });
}

export function useDeleteSavedQuery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => picker.deleteSavedQuery(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["picker", "saved_queries"] }),
  });
}

// ── NLM integration ─────────────────────────────────────────────────────────

export function useNlmRuns() {
  return useQuery({ queryKey: ["nlm", "runs"], queryFn: nlm.listNlmRuns });
}

export function useSubmitNotebook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: NotebookRequest) => nlm.submitNotebook(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nlm", "runs"] });
    },
  });
}

export function useSubmitGenerate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: GenerateRequest) => nlm.submitGenerate(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nlm", "runs"] });
    },
  });
}

export function useSubmitNlmBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: NlmBatchRequest) => nlm.submitNlmBatch(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nlm", "runs"] });
    },
  });
}

export function useSubmitValidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: ValidateRequest) => nlm.submitValidate(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nlm", "runs"] });
    },
  });
}
