// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi, type Mock } from "vitest";

import type { Job } from "../api/failureMemory";
import * as api from "../api/failureMemory";
import { useTrackedWriteJob } from "./failureMemory";

/**
 * A compile or refresh submission returns 202 with a queued job — not an
 * outcome. This pins the wiring that surfaces the outcome: tracking the job
 * refreshes the jobs list immediately, polls the job to its terminal state,
 * and then invalidates the queries a write can move. Without it, accepted
 * work could finish or fail without ever appearing in the operator UI.
 */

vi.mock("../api/failureMemory", () => ({
  checkFailureMemory: vi.fn(),
  compileProject: vi.fn(),
  getFailureMemoryJob: vi.fn(),
  getWriteDiagnostics: vi.fn(),
  listFailureMemoryJobs: vi.fn(),
  recordLesson: vi.fn(),
  refreshProject: vi.fn(),
  getFailureMemoryLesson: vi.fn(),
  getFailureMemoryLessons: vi.fn(),
  getFailureMemoryProjects: vi.fn(),
  getFailureMemoryRoutes: vi.fn(),
  getFailureMemoryStatus: vi.fn(),
  resolveFailureMemory: vi.fn(),
}));

const QUEUED: Job = {
  id: "abcdef0123456789",
  project_id: "pack",
  action: "compile",
  status: "queued",
  detail: "write",
  created_at: "2026-08-16T00:00:00+00:00",
  started_at: null,
  finished_at: null,
  summary: "",
  result: null,
  error: null,
};

describe("useTrackedWriteJob", () => {
  it("surfaces a tracked job's terminal outcome and refreshes the write queries", async () => {
    const settled: Job = {
      ...QUEUED,
      status: "succeeded",
      finished_at: "2026-08-16T00:00:01+00:00",
      summary: "Compiler ok",
      result: { status: "ok" },
    };
    (api.getFailureMemoryJob as Mock).mockResolvedValue(settled);

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidated = vi.spyOn(client, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useTrackedWriteJob("pack"), { wrapper });
    act(() => result.current.track(QUEUED));

    // Tracking refreshes the jobs list at once, so the queued entry appears.
    expect(invalidated).toHaveBeenCalledWith({
      queryKey: ["failure-memory", "jobs", "pack"],
    });

    // The job is polled; once terminal, the write-movable queries refresh.
    await waitFor(() => expect(api.getFailureMemoryJob).toHaveBeenCalledWith(QUEUED.id));
    await waitFor(
      () => {
        expect(invalidated).toHaveBeenCalledWith({
          queryKey: ["failure-memory", "status", "pack"],
        });
      },
      { timeout: 5000 },
    );
    await waitFor(() => expect(result.current.active).toBeNull(), { timeout: 5000 });
  });
});
