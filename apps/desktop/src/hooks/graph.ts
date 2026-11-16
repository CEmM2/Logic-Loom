import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getClusterGraph,
  getEgoGraph,
  getGraphEdges,
  getGraphIssueLog,
  getGraphNodes,
  type GraphRefreshProgress,
  type GraphIssueLog,
  type GraphRefreshOptions,
  GraphReviewRequiredError,
  type GraphStatus,
  getGraphStatus,
  refreshGraph,
  scanGraphIssueLog,
  setGraphIssueIgnored,
  streamGraphRefresh,
  type GraphDirection,
  type GraphEdgeFilters,
  type GraphNodeFilters,
} from "../api/graph";

export function useGraphStatus() {
  return useQuery({
    queryKey: ["graph", "status"],
    queryFn: getGraphStatus,
    staleTime: 30_000,
  });
}

export function useRefreshGraph() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: refreshGraph,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["graph"] }),
        qc.invalidateQueries({ queryKey: ["graph", "issues"] }),
      ]);
    },
  });
}

export function useGraphIssueLog() {
  return useQuery({
    queryKey: ["graph", "issues"],
    queryFn: getGraphIssueLog,
    staleTime: 0,
  });
}

export function useScanGraphIssueLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: scanGraphIssueLog,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["graph", "issues"] });
    },
  });
}

export function useSetGraphIssueIgnored() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ path, ignored }: { path: string; ignored: boolean }) => setGraphIssueIgnored(path, ignored),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["graph", "issues"] });
    },
  });
}

export function useGraphRefreshStream() {
  const qc = useQueryClient();
  const closeRef = useRef<(() => void) | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [progress, setProgress] = useState<GraphRefreshProgress | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [reviewRequired, setReviewRequired] = useState<GraphIssueLog | null>(null);

  useEffect(() => () => {
    closeRef.current?.();
    closeRef.current = null;
  }, []);

  const start = useCallback(async (options: GraphRefreshOptions = {}) => {
    closeRef.current?.();
    closeRef.current = null;
    setIsPending(true);
    setProgress(null);
    setError(null);
    setReviewRequired(null);

    return new Promise<GraphStatus>((resolve, reject) => {
      const fail = (rawError: unknown) => {
        const err = rawError instanceof Error ? rawError : new Error(String(rawError));
        setProgress(null);
        setError(err);
        setIsPending(false);
        reject(err);
      };

      streamGraphRefresh(
        options,
        (next) => setProgress(next),
        async (status) => {
          closeRef.current = null;
          await Promise.all([
            qc.invalidateQueries({ queryKey: ["graph"] }),
            qc.invalidateQueries({ queryKey: ["graph", "issues"] }),
          ]);
          setProgress(null);
          setIsPending(false);
          resolve(status);
        },
        (message) => fail(new Error(message)),
        async (report) => {
          closeRef.current = null;
          await qc.invalidateQueries({ queryKey: ["graph", "issues"] });
          setProgress(null);
          setReviewRequired(report);
          setIsPending(false);
          reject(new GraphReviewRequiredError(report));
        },
      )
        .then((closeFn) => {
          closeRef.current = closeFn;
        })
        .catch(fail);
    });
  }, [qc]);

  return { start, isPending, progress, error, reviewRequired, clearReviewRequired: () => setReviewRequired(null) };
}

export function useGraphNodes(filters: GraphNodeFilters, enabled = true) {
  return useQuery({
    queryKey: ["graph", "nodes", filters],
    queryFn: () => getGraphNodes(filters),
    enabled,
    staleTime: 30_000,
  });
}

export function useGraphEdges(filters: GraphEdgeFilters, enabled = true) {
  return useQuery({
    queryKey: ["graph", "edges", filters],
    queryFn: () => getGraphEdges(filters),
    enabled,
    staleTime: 30_000,
  });
}

export function useGraphEgo(
  params: { node: string; depth: number; direction: GraphDirection },
  enabled = true,
) {
  return useQuery({
    queryKey: ["graph", "ego", params],
    queryFn: () => getEgoGraph(params),
    enabled,
    staleTime: 30_000,
  });
}

export function useGraphCluster(params: { tag?: string; domain?: string }, enabled = true) {
  return useQuery({
    queryKey: ["graph", "cluster", params],
    queryFn: () => getClusterGraph(params),
    enabled,
    staleTime: 30_000,
  });
}
