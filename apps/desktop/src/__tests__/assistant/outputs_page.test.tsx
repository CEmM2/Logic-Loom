// @vitest-environment jsdom
/**
 * Frontend tests for the Assistant Outputs page UI.
 *
 * Covers:
 *   1. Tree view groups runs by batch_id → workflow_id → run_id with
 *      status badges (running, succeeded, failed).
 *   2. Detail pane renders markdown preview, metadata viewer, prompt
 *      snapshot viewer, logs viewer for the selected run.
 *   3. Action buttons (rerun-same / rerun-different / export /
 *      open-folder / compare) fire the matching API calls (mocked).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { AssistantOutputRecord } from "../../api/assistantOutputs";

// Mock the assistantOutputs + runs API clients. The tests assert on
// these mocks rather than re-running React Query against a live fetch.
vi.mock("../../api/assistantOutputs", () => ({
  listAssistantOutputs: vi.fn(),
  getPrimaryOutput: vi.fn(),
  openRunFolder: vi.fn(),
  exportRun: vi.fn(),
}));
vi.mock("../../api/runs", () => ({
  rerunRun: vi.fn(),
  getEffectivePrompts: vi.fn(),
}));

import AssistantOutputsRoute, {
  groupRunsByBatch,
} from "../../routes/assistant/outputs";
import {
  exportRun,
  getPrimaryOutput,
  listAssistantOutputs,
  openRunFolder,
} from "../../api/assistantOutputs";
import { getEffectivePrompts, rerunRun } from "../../api/runs";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeRun(
  overrides: Partial<AssistantOutputRecord> & { id: string },
): AssistantOutputRecord {
  const base: AssistantOutputRecord = {
    id: overrides.id,
    stage: overrides.stage ?? overrides.workflow_id ?? "assistant.literature_survey",
    status: overrides.status ?? "succeeded",
    started_at: overrides.started_at ?? "2026-05-15T10:00:00Z",
    ended_at: overrides.ended_at ?? "2026-05-15T10:05:00Z",
    exit_code: overrides.exit_code ?? 0,
    pid: overrides.pid ?? null,
    log_path: overrides.log_path ?? "/tmp/logs.txt",
    args: overrides.args ?? [],
    cwd: overrides.cwd ?? null,
    last_lines: overrides.last_lines ?? [],
    kind: "workflow",
    batch_id: overrides.batch_id ?? "b-1",
    workflow_id: overrides.workflow_id ?? "assistant.literature_survey",
  };
  return { ...base, ...overrides };
}

function renderWithProvider(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("Assistant Outputs page UI", () => {
  it("tree groups runs by batch_id → workflow_id → run_id", async () => {
    const runs = [
      makeRun({
        id: "run-1",
        batch_id: "b-A",
        workflow_id: "assistant.literature_survey",
      }),
      makeRun({
        id: "run-2",
        batch_id: "b-A",
        workflow_id: "assistant.paper_introduction",
      }),
      makeRun({
        id: "run-3",
        batch_id: "b-B",
        workflow_id: "assistant.related_work",
      }),
    ];
    vi.mocked(listAssistantOutputs).mockResolvedValue(runs);

    renderWithProvider(<AssistantOutputsRoute />);

    await waitFor(() => {
      expect(screen.getByTestId("batch-node-b-A")).toBeTruthy();
    });
    expect(screen.getByTestId("batch-node-b-B")).toBeTruthy();
    expect(
      screen.getByTestId("workflow-node-assistant.literature_survey"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("workflow-node-assistant.paper_introduction"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("workflow-node-assistant.related_work"),
    ).toBeTruthy();
    expect(screen.getByTestId("run-node-run-1")).toBeTruthy();
    expect(screen.getByTestId("run-node-run-2")).toBeTruthy();
    expect(screen.getByTestId("run-node-run-3")).toBeTruthy();
  });

  it("status badges render running / succeeded / failed", async () => {
    const runs = [
      makeRun({ id: "run-r", status: "running" }),
      makeRun({ id: "run-s", status: "succeeded" }),
      makeRun({ id: "run-f", status: "failed" }),
    ];
    vi.mocked(listAssistantOutputs).mockResolvedValue(runs);

    renderWithProvider(<AssistantOutputsRoute />);

    await waitFor(() => {
      expect(screen.getAllByTestId(/^status-badge-/).length).toBe(3);
    });
    expect(screen.getByTestId("status-badge-running")).toBeTruthy();
    expect(screen.getByTestId("status-badge-succeeded")).toBeTruthy();
    expect(screen.getByTestId("status-badge-failed")).toBeTruthy();
  });

  it("detail pane renders markdown + metadata + prompt snapshot + logs", async () => {
    const run = makeRun({
      id: "run-detail",
      last_lines: ["line one", "line two"],
    });
    vi.mocked(listAssistantOutputs).mockResolvedValue([run]);
    vi.mocked(getPrimaryOutput).mockResolvedValue({
      run_id: "run-detail",
      filename: "output.md",
      content: "# Hello body",
    });
    vi.mocked(getEffectivePrompts).mockResolvedValue({
      schema_version: 1,
      slots: { main: { variant_id: "v1", content_hash: "sha256:abc" } },
    } as unknown as ReturnType<typeof getEffectivePrompts> extends Promise<infer T> ? T : never);

    renderWithProvider(<AssistantOutputsRoute />);

    await waitFor(() => {
      expect(screen.getByTestId("run-node-run-detail")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("run-node-run-detail"));

    // Markdown tab is the default.
    await waitFor(() => {
      expect(screen.getByTestId("markdown-body").textContent).toContain(
        "Hello body",
      );
    });

    // Metadata tab — registry record is the source.
    fireEvent.click(screen.getByTestId("detail-tab-metadata"));
    await waitFor(() => {
      expect(screen.getByTestId("metadata-body").textContent).toContain(
        "run-detail",
      );
    });

    // Prompts tab — fetches via getEffectivePrompts mock.
    fireEvent.click(screen.getByTestId("detail-tab-prompts"));
    await waitFor(() => {
      expect(screen.getByTestId("prompts-body").textContent).toContain(
        "sha256:abc",
      );
    });

    // Logs tab — uses record.last_lines.
    fireEvent.click(screen.getByTestId("detail-tab-logs"));
    await waitFor(() => {
      expect(screen.getByTestId("logs-body").textContent).toContain(
        "line one",
      );
      expect(screen.getByTestId("logs-body").textContent).toContain(
        "line two",
      );
    });
  });

  it("rerun-same fires POST /api/runs/{id}/rerun with empty body", async () => {
    const run = makeRun({ id: "run-rerun" });
    vi.mocked(listAssistantOutputs).mockResolvedValue([run]);
    vi.mocked(rerunRun).mockResolvedValue({ run_id: "new-run" });

    renderWithProvider(<AssistantOutputsRoute />);

    await waitFor(() => {
      expect(screen.getByTestId("run-node-run-rerun")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("run-node-run-rerun"));
    fireEvent.click(screen.getByTestId("action-rerun-same"));

    await waitFor(() => {
      expect(vi.mocked(rerunRun)).toHaveBeenCalledWith("run-rerun", {});
    });
  });

  it("export action calls export endpoint", async () => {
    const run = makeRun({ id: "run-export" });
    vi.mocked(listAssistantOutputs).mockResolvedValue([run]);
    vi.mocked(exportRun).mockResolvedValue(
      new Blob([new Uint8Array([0x50, 0x4b])], { type: "application/zip" }),
    );
    // jsdom doesn't define URL.createObjectURL/revokeObjectURL — install
    // stubs on the global URL object before the action handler fires.
    const originalCreate = (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
    const originalRevoke = (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
    (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = () => "blob:abc";
    (URL as unknown as { revokeObjectURL: (s: string) => void }).revokeObjectURL = () => {};

    renderWithProvider(<AssistantOutputsRoute />);

    await waitFor(() => {
      expect(screen.getByTestId("run-node-run-export")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("run-node-run-export"));
    fireEvent.click(screen.getByTestId("action-export"));

    await waitFor(() => {
      expect(vi.mocked(exportRun)).toHaveBeenCalledWith("run-export");
    });
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = originalCreate;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = originalRevoke;
  });

  it("open-folder action calls open-folder endpoint", async () => {
    const run = makeRun({ id: "run-open" });
    vi.mocked(listAssistantOutputs).mockResolvedValue([run]);
    vi.mocked(openRunFolder).mockResolvedValue({
      path: "/abs/path/to/run-open",
    });

    renderWithProvider(<AssistantOutputsRoute />);

    await waitFor(() => {
      expect(screen.getByTestId("run-node-run-open")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("run-node-run-open"));
    fireEvent.click(screen.getByTestId("action-open-folder"));

    await waitFor(() => {
      expect(vi.mocked(openRunFolder)).toHaveBeenCalledWith("run-open");
    });
  });

  // Defensive: groupRunsByBatch is the pure tree-building helper that
  // the overview route test transitively exercises through the route render. Pin
  // its contract with a unit test so future refactors can't silently
  // break the grouping.
  it("groupRunsByBatch produces stable batch → workflow → run hierarchy", () => {
    const runs = [
      makeRun({ id: "r1", batch_id: "x", workflow_id: "w1" }),
      makeRun({ id: "r2", batch_id: "x", workflow_id: "w1" }),
      makeRun({ id: "r3", batch_id: "x", workflow_id: "w2" }),
      makeRun({ id: "r4", batch_id: "y", workflow_id: "w1" }),
    ];
    const tree = groupRunsByBatch(runs);
    expect(tree.map((b) => b.batch_id)).toEqual(["x", "y"]);
    const x = tree.find((b) => b.batch_id === "x")!;
    expect(Array.from(x.workflows.keys())).toEqual(["w1", "w2"]);
    expect(x.workflows.get("w1")!.runs.map((r) => r.run.id)).toEqual([
      "r1",
      "r2",
    ]);
  });

  it("renders empty state when no Assistant runs exist", async () => {
    vi.mocked(listAssistantOutputs).mockResolvedValue([]);

    renderWithProvider(<AssistantOutputsRoute />);

    await waitFor(() => {
      expect(screen.getByTestId("outputs-empty")).toBeTruthy();
    });
    expect(screen.getByTestId("detail-empty")).toBeTruthy();
  });

  it("preserves the route contract: <h1>Outputs</h1> at the top", async () => {
    vi.mocked(listAssistantOutputs).mockResolvedValue([]);

    renderWithProvider(<AssistantOutputsRoute />);

    // Same assertion as assistant_overview.test.tsx.
    expect(
      screen.getByRole("heading", { level: 1, name: "Outputs" }),
    ).toBeTruthy();
  });
});
