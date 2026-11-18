// @vitest-environment jsdom
/**
 * QA batch 4: screens that used to leave the user stuck.
 *   - zsum Collections with no index (or a failed request) offers next steps.
 *   - Batch "Run history" lists the batch's runs from the run registry.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const getZsumIndex = vi.fn();
vi.mock("../api/zsum", () => ({ getZsumIndex: () => getZsumIndex() }));
const listAssistantOutputs = vi.fn();
vi.mock("../api/assistantOutputs", () => ({
  listAssistantOutputs: () => listAssistantOutputs(),
}));

import CollectionsRoute from "../routes/zsum/collections";
import { BatchRunHistory, runsForBatch } from "../components/assistant/batches/BatchRunHistory";
import type { AssistantBatch } from "../api/assistantBatches";
import type { RunRecord } from "../api/runs";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: 0 } } })}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function run(id: string, batch_id: string | null, started_at: string): RunRecord {
  return {
    id,
    stage: "workflow",
    status: "succeeded",
    started_at,
    ended_at: null,
    exit_code: 0,
    pid: null,
    log_path: "",
    args: [],
    cwd: null,
    last_lines: [],
    kind: "workflow",
    batch_id,
    workflow_id: "lit-survey",
  };
}

describe("zsum Collections empty state", () => {
  it("explains a missing index and links to Pipeline and settings", async () => {
    getZsumIndex.mockResolvedValue({ kind: "missing", cache_dir: "/c", message: "index.json not found at /c/index.json" });
    wrap(<CollectionsRoute />);
    expect(await screen.findByText(/No zsum library yet/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Pipeline" }).getAttribute("href")).toBe("/zsum/pipeline");
    expect(screen.getByRole("link", { name: "Check zsum settings" })).toBeTruthy();
    // Friendlier than the raw "index.json not found" message (v0.3.3 retest).
    expect(screen.queryByText(/index.json not found/)).toBeNull();
    expect(screen.getByText("/c")).toBeTruthy();
  });

  it("does not render a blank page when the request fails", async () => {
    getZsumIndex.mockRejectedValue(new Error("zsum config unreadable"));
    wrap(<CollectionsRoute />);
    expect(await screen.findByText(/could not be read/)).toBeTruthy();
    expect(screen.getByText("zsum config unreadable")).toBeTruthy();
  });
});

describe("Batch run history", () => {
  const batch = { id: "b1", title: "Batch One" } as AssistantBatch;

  it("keeps only this batch's runs, newest first", () => {
    const runs = [
      run("old", "b1", "2026-11-01T10:00:00Z"),
      run("other", "b2", "2026-11-03T10:00:00Z"),
      run("new", "b1", "2026-11-02T10:00:00Z"),
    ];
    expect(runsForBatch(runs, "b1").map((r) => r.id)).toEqual(["new", "old"]);
  });

  it("lists runs from the registry", async () => {
    listAssistantOutputs.mockResolvedValue([run("r1", "b1", "2026-11-01T10:00:00Z"), run("r2", "b2", "2026-11-01T10:00:00Z")]);
    wrap(<BatchRunHistory batch={batch} />);
    expect(await screen.findByTestId("batch-run-r1")).toBeTruthy();
    expect(screen.queryByTestId("batch-run-r2")).toBeNull();
    expect(screen.queryByText(/not listed on this tab/)).toBeNull();
  });

  it("says when there are no runs yet", async () => {
    listAssistantOutputs.mockResolvedValue([]);
    wrap(<BatchRunHistory batch={batch} />);
    expect(await screen.findByText(/No runs yet/)).toBeTruthy();
  });
});
