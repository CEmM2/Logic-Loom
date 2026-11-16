import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  checkFailureMemory,
  compileProject,
  getFailureMemoryJob,
  getWriteDiagnostics,
  listFailureMemoryJobs,
  recordLesson,
  refreshProject,
  getFailureMemoryLesson,
  getFailureMemoryLessons,
  getFailureMemoryProjects,
  getFailureMemoryRoutes,
  getFailureMemoryStatus,
  resolveFailureMemory,
  type Job,
  type LessonFilters,
  type RecordRequest,
  type RefreshAction,
  type ResolveRequest,
} from "../api/failureMemory";

/**
 * `GET /api/failure-memory/projects` — always-200 discovery. The workspace gate
 * and the project selector both read this, so it is cached briefly and shared.
 */
export function useFailureMemoryProjects() {
  return useQuery({
    queryKey: ["failure-memory", "projects"],
    queryFn: getFailureMemoryProjects,
    staleTime: 30_000,
  });
}

/**
 * The selected project lives in the URL (`?project=<id>`), not in a store.
 *
 * Two reasons: every sub-tab of the workspace needs it and would otherwise have
 * to thread it through, and a failure-memory view is exactly the kind of thing
 * someone pastes to a colleague — a URL that carries the project is shareable,
 * a component-local selection is not. Falls back to the first AVAILABLE project
 * so a broken entry never becomes the default view.
 */
export function useSelectedProject(candidates: { id: string; available: boolean }[]) {
  const [params, setParams] = useSearchParams();
  const requested = params.get("project");

  const selected = useMemo(() => {
    if (requested && candidates.some((c) => c.id === requested)) return requested;
    return candidates.find((c) => c.available)?.id ?? candidates[0]?.id ?? null;
  }, [requested, candidates]);

  const select = useCallback(
    (id: string) => {
      const next = new URLSearchParams(params);
      next.set("project", id);
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  return { selected, select, requestedIsUnknown: Boolean(requested) && requested !== selected };
}

export function useFailureMemoryStatus(project: string | null) {
  return useQuery({
    queryKey: ["failure-memory", "status", project],
    queryFn: () => getFailureMemoryStatus(project as string),
    enabled: Boolean(project),
  });
}

export function useFailureMemoryLessons(project: string | null, filters: LessonFilters) {
  return useQuery({
    queryKey: ["failure-memory", "lessons", project, filters],
    queryFn: () => getFailureMemoryLessons(project as string, filters),
    enabled: Boolean(project),
  });
}

export function useFailureMemoryLesson(project: string | null, lessonId: string | null) {
  return useQuery({
    queryKey: ["failure-memory", "lesson", project, lessonId],
    queryFn: () => getFailureMemoryLesson(project as string, lessonId as string),
    enabled: Boolean(project) && Boolean(lessonId),
  });
}

export function useFailureMemoryRoutes(project: string | null) {
  return useQuery({
    queryKey: ["failure-memory", "routes", project],
    queryFn: () => getFailureMemoryRoutes(project as string),
    enabled: Boolean(project),
  });
}

/** Resolution preview. A mutation only in the react-query sense — the backend
 * runs with `write_artifacts=false` and creates nothing. */
export function useFailureMemoryResolve(project: string | null) {
  return useMutation({
    mutationFn: (request: ResolveRequest) =>
      resolveFailureMemory(project as string, request),
  });
}

/** Compiler diagnostics (`validate` + `check`). Publishes nothing. */
export function useFailureMemoryCheck(project: string | null) {
  return useMutation({
    mutationFn: () => checkFailureMemory(project as string),
  });
}


// ── operator mode ───────────────────────────────────────────────────────────

export function useWriteDiagnostics(project: string | null) {
  return useQuery({
    queryKey: ["failure-memory", "write-diagnostics", project],
    queryFn: () => getWriteDiagnostics(project as string),
    enabled: Boolean(project),
  });
}

/**
 * Job list, polled while anything is still in flight.
 *
 * The poll is what makes the "no partially accepted state" rule visible: the
 * form does not decide the outcome, this query does, and it only reports one
 * once the backend job reaches a terminal status.
 */
export function useFailureMemoryJobs(project: string | null) {
  return useQuery({
    queryKey: ["failure-memory", "jobs", project],
    queryFn: () => listFailureMemoryJobs(project ?? undefined),
    enabled: Boolean(project),
    refetchInterval: (query) =>
      (query.state.data ?? []).some(
        (job: Job) => job.status === "queued" || job.status === "running",
      )
        ? 750
        : false,
  });
}

export function useFailureMemoryJob(jobId: string | null) {
  return useQuery({
    queryKey: ["failure-memory", "job", jobId],
    queryFn: () => getFailureMemoryJob(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) =>
      query.state.data && (query.state.data.status === "queued" || query.state.data.status === "running")
        ? 500
        : false,
  });
}

/**
 * Invalidates everything a write can move: status, lessons, routes and the job
 * list. Called when a job reaches a TERMINAL state, never on dispatch.
 */
export function useInvalidateAfterWrite(project: string | null) {
  const qc = useQueryClient();
  return () => {
    for (const key of ["status", "lessons", "lesson", "routes", "jobs", "write-diagnostics"]) {
      void qc.invalidateQueries({ queryKey: ["failure-memory", key, project] });
    }
  };
}

export function useRecordLesson(project: string | null) {
  return useMutation({
    mutationFn: (request: RecordRequest) => recordLesson(project as string, request),
  });
}

/**
 * Track one submitted write job to its terminal state.
 *
 * A submission returns 202 with a `queued` job — not an outcome. Callers pass
 * the created job to `track`; this hook refreshes the jobs list immediately
 * (so the queued entry appears), polls the job, and invalidates everything a
 * write can move once it settles. Without this, a compile or refresh 202
 * would never surface its success or failure: the jobs list only self-polls
 * while it already contains an in-flight job.
 */
export function useTrackedWriteJob(project: string | null) {
  const [jobId, setJobId] = useState<string | null>(null);
  const invalidate = useInvalidateAfterWrite(project);
  const { data: job } = useFailureMemoryJob(jobId);
  const settled = useRef<string | null>(null);

  useEffect(() => {
    if (!job || settled.current === job.id) return;
    if (job.status !== "succeeded" && job.status !== "failed") return;
    settled.current = job.id;
    setJobId(null);
    invalidate();
  }, [job, invalidate]);

  const track = useCallback(
    (created: Job) => {
      settled.current = null;
      setJobId(created.id);
      invalidate();
    },
    [invalidate],
  );

  return { track, active: job ?? null };
}

export function useCompileProject(project: string | null) {
  return useMutation({ mutationFn: () => compileProject(project as string) });
}

export function useRefreshProject(project: string | null) {
  return useMutation({
    mutationFn: (action: RefreshAction) =>
      refreshProject(project as string, { action }),
  });
}
