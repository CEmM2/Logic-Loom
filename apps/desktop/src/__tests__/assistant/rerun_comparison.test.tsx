// @vitest-environment jsdom
/**
 * Frontend tests for the Assistant rerun + comparison flows.
 *
 * Covers:
 *   1. Rerun-same fires ``POST /api/runs/{id}/rerun`` with an empty
 *      body and refreshes the tree.
 *   2. Rerun-different opens the extracted ``RunWorkflowDialog``
 *      pre-filled with previous workflow_inputs + prompt_overrides.
 *   3. Compare opens ``RunComparisonView`` and highlights only the
 *      changed prompt slot when two runs differ only in
 *      ``assistant.literature_survey``.
 *   4. Selection guard: comparing the same run_id twice raises a
 *      friendly error.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import {
  RunComparisonView,
  diffPromptSnapshots,
  type ComparisonRunPayload,
} from "../../components/assistant/RunComparisonView";
import type { AssistantOutputRecord } from "../../api/assistantOutputs";

// Mocks (same shape as outputs_page.test.tsx + the batches API for rerun-different).
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
vi.mock("../../api/batches", () => ({
  getBatch: vi.fn(),
}));

import AssistantOutputsRoute from "../../routes/assistant/outputs";
import {
  listAssistantOutputs,
  getPrimaryOutput,
} from "../../api/assistantOutputs";
import { rerunRun, getEffectivePrompts } from "../../api/runs";
import { getBatch } from "../../api/batches";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeRun(
  overrides: Partial<AssistantOutputRecord> & { id: string },
): AssistantOutputRecord {
  const base: AssistantOutputRecord = {
    id: overrides.id,
    stage:
      overrides.stage ?? overrides.workflow_id ?? "assistant.literature_survey",
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
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Rerun-same
// ---------------------------------------------------------------------------

describe("Assistant rerun + comparison flows", () => {
  it("rerun-same posts to /api/runs/{id}/rerun with empty body", async () => {
    const run = makeRun({ id: "run-rs" });
    vi.mocked(listAssistantOutputs).mockResolvedValue([run]);
    vi.mocked(rerunRun).mockResolvedValue({ run_id: "new" });

    renderWithProvider(<AssistantOutputsRoute />);
    await waitFor(() =>
      expect(screen.getByTestId("run-node-run-rs")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("run-node-run-rs"));
    fireEvent.click(screen.getByTestId("action-rerun-same"));
    await waitFor(() => {
      expect(vi.mocked(rerunRun)).toHaveBeenCalledWith("run-rs", {});
    });
  });

  // ---------------------------------------------------------------------
  // Rerun-different
  // ---------------------------------------------------------------------

  it("rerun-different opens RunWorkflowDialog pre-filled with previous inputs", async () => {
    const run = makeRun({
      id: "run-rd",
      workflow_id: "assistant.paper_introduction",
      batch_id: "b-rd",
    });
    // The route fetches the batch via getBatch + getEffectivePrompts
    // before mounting the dialog.
    vi.mocked(listAssistantOutputs).mockResolvedValue([run]);
    vi.mocked(getBatch).mockResolvedValue({
      batch: {
        id: "b-rd",
        title: "Test batch",
        description: "",
        round: 1,
        round_title: "",
        round_theme: "",
        status: "active",
        source: "manual",
        created_at: "2026-05-15T00:00:00Z",
        updated_at: "2026-05-15T00:00:00Z",
        papers: [],
        nodes: [],
        notebook_id: null,
        zotsums_text: "",
        sources_text: "",
        notes: "",
        default_workflow_id: null,
        prompt_profile_id: null,
        prompt_overrides: {},
      },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    vi.mocked(getEffectivePrompts).mockResolvedValue({
      slots: {},
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);

    renderWithProvider(<AssistantOutputsRoute />);
    await waitFor(() =>
      expect(screen.getByTestId("run-node-run-rd")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("run-node-run-rd"));
    fireEvent.click(screen.getByTestId("action-rerun-different"));

    // Dialog mounts with the source run's workflow id pre-filled.
    await waitFor(() => {
      expect(screen.getByTestId("run-workflow-dialog")).toBeTruthy();
    });
    const workflowInput = screen.getByTestId(
      "run-workflow-id-input",
    ) as HTMLInputElement;
    expect(workflowInput.value).toBe("assistant.paper_introduction");
  });

  // ---------------------------------------------------------------------
  // Compare opens with two distinct run_ids
  // ---------------------------------------------------------------------

  it("compare with two distinct run_ids opens RunComparisonView", async () => {
    const runA = makeRun({ id: "run-A" });
    const runB = makeRun({ id: "run-B" });
    vi.mocked(listAssistantOutputs).mockResolvedValue([runA, runB]);
    vi.mocked(getEffectivePrompts).mockResolvedValue({
      slots: {},
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    vi.mocked(getPrimaryOutput).mockResolvedValue({
      run_id: "x",
      filename: "output.md",
      content: "body",
    });

    renderWithProvider(<AssistantOutputsRoute />);

    await waitFor(() =>
      expect(screen.getByTestId("run-node-run-A")).toBeTruthy(),
    );
    // First compare: stake left.
    fireEvent.click(screen.getByTestId("run-node-run-A"));
    fireEvent.click(screen.getByTestId("action-compare"));
    await waitFor(() =>
      expect(screen.getByTestId("compare-pending-right")).toBeTruthy(),
    );
    // Second compare on a different run: open the view.
    fireEvent.click(screen.getByTestId("run-node-run-B"));
    fireEvent.click(screen.getByTestId("action-compare"));

    await waitFor(() => {
      expect(screen.getByTestId("run-comparison-overlay")).toBeTruthy();
    });
    expect(screen.getByTestId("run-comparison-view")).toBeTruthy();
    expect(screen.getByTestId("comparison-left-id").textContent).toBe("run-A");
    expect(screen.getByTestId("comparison-right-id").textContent).toBe("run-B");
  });

  // ---------------------------------------------------------------------
  // Friendly error on same-id comparison
  // ---------------------------------------------------------------------

  it("comparing the same run_id twice surfaces a friendly error", async () => {
    const run = makeRun({ id: "run-only-one" });
    vi.mocked(listAssistantOutputs).mockResolvedValue([run]);

    renderWithProvider(<AssistantOutputsRoute />);
    await waitFor(() =>
      expect(screen.getByTestId("run-node-run-only-one")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("run-node-run-only-one"));
    // First compare: stake left.
    fireEvent.click(screen.getByTestId("action-compare"));
    await waitFor(() =>
      expect(screen.getByTestId("compare-pending-right")).toBeTruthy(),
    );
    // Second compare on the SAME run.
    fireEvent.click(screen.getByTestId("action-compare"));

    await waitFor(() => {
      expect(screen.getByTestId("compare-error")).toBeTruthy();
    });
    expect(screen.getByTestId("compare-error").textContent).toContain(
      "compare a run to itself",
    );
  });

  // ---------------------------------------------------------------------
  // Load-bearing: only changed prompt slots are highlighted
  // ---------------------------------------------------------------------

  it("comparing runs that differ only in assistant.literature_survey highlights only that slot", () => {
    const leftPrompts = {
      slots: {
        main: {
          slot: "assistant.paper_introduction",
          variant_id: "v1",
          content_hash: "sha256:abc",
        },
        // The literature survey slot here represents the workflow's
        // composition of multiple prompt slots. Both runs share
        // ``main``; only the literature-survey hash differs.
        "assistant.literature_survey": {
          slot: "assistant.literature_survey",
          variant_id: "v1",
          content_hash: "sha256:LEFT",
        },
      },
    };
    const rightPrompts = {
      slots: {
        main: {
          slot: "assistant.paper_introduction",
          variant_id: "v1",
          content_hash: "sha256:abc",
        },
        "assistant.literature_survey": {
          slot: "assistant.literature_survey",
          variant_id: "v2",
          content_hash: "sha256:RIGHT",
        },
      },
    };

    const diff = diffPromptSnapshots(leftPrompts, rightPrompts);
    const changed = diff.filter((d) => d.status === "changed");
    expect(changed.map((d) => d.slot_name)).toEqual([
      "assistant.literature_survey",
    ]);
    const unchanged = diff.filter((d) => d.status === "unchanged");
    expect(unchanged.map((d) => d.slot_name)).toEqual(["main"]);
  });

  // ---------------------------------------------------------------------
  // Component pin: comparing identical snapshots marks zero diffs.
  // ---------------------------------------------------------------------

  it("comparing identical prompt snapshots marks zero diffs", () => {
    const snap = {
      slots: {
        main: { slot: "assistant.literature_survey", content_hash: "h1" },
        other: { slot: "assistant.method_comparison", content_hash: "h2" },
      },
    };
    const diff = diffPromptSnapshots(snap, snap);
    expect(diff.every((d) => d.status === "unchanged")).toBe(true);
  });

  // ---------------------------------------------------------------------
  // Component pin: RunComparisonView renders the load-bearing contract.
  // ---------------------------------------------------------------------

  it("RunComparisonView marks only the changed slot's row", () => {
    const leftPayload: ComparisonRunPayload = {
      record: makeRun({ id: "L" }),
      prompts: {
        slots: {
          main: { slot: "x", content_hash: "h-main" },
          "assistant.literature_survey": {
            slot: "assistant.literature_survey",
            content_hash: "h-left",
          },
        },
      },
      primary: "left body",
    };
    const rightPayload: ComparisonRunPayload = {
      record: makeRun({ id: "R" }),
      prompts: {
        slots: {
          main: { slot: "x", content_hash: "h-main" },
          "assistant.literature_survey": {
            slot: "assistant.literature_survey",
            content_hash: "h-right",
          },
        },
      },
      primary: "right body",
    };
    render(<RunComparisonView left={leftPayload} right={rightPayload} />);
    const mainRow = screen.getByTestId("slot-diff-main");
    const literatureRow = screen.getByTestId(
      "slot-diff-assistant.literature_survey",
    );
    expect(mainRow.getAttribute("data-status")).toBe("unchanged");
    expect(literatureRow.getAttribute("data-status")).toBe("changed");
  });

  // ---------------------------------------------------------------------
  // Component pin: same-id renders the friendly error directly.
  // ---------------------------------------------------------------------

  it("RunComparisonView renders friendly error on same-id payloads", () => {
    const sameRun = makeRun({ id: "same-id" });
    const payload: ComparisonRunPayload = {
      record: sameRun,
      prompts: null,
      primary: null,
    };
    render(<RunComparisonView left={payload} right={payload} />);
    expect(screen.getByTestId("run-comparison-same-id-error")).toBeTruthy();
  });
});
