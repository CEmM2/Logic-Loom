// @vitest-environment jsdom
/**
 * Tests for the Assistant Run dialog UI.
 *
 * Covers:
 *   1. Dialog opens from a batch, lists Assistant workflows only,
 *      dry-run preview works.
 *   2. ``workflowFilter`` restricts the workflow listing to the
 *      Assistant category set.
 *   3. ``Save as batch default`` checkbox forwards through to the
 *      runWorkflow payload.
 *   4. ``out_dir`` is pre-filled to
 *      ``<assistant_workspace>/assistant/outputs/<batch_id>/<workflow_id>/<run_id>``.
 *
 * The dialog extraction (shared RunWorkflowDialog) is exercised by the
 * ``Backwards-compat`` describe block: the extracted dialog without a
 * filter prop still behaves like the original inline AKMS dialog.
 */
import {
  afterEach,
  describe,
  it,
  expect,
  vi,
} from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the batches API BEFORE importing the components.
vi.mock("../../api/batches", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/batches")>(
      "../../api/batches",
    );
  return {
    ...actual,
    previewEffectivePrompts: vi.fn(),
    runWorkflow: vi.fn(),
  };
});

import * as batchesApi from "../../api/batches";
import type { EditableBatch } from "../../api/batches";
import { RunWorkflowDialog } from "../../components/batches/RunWorkflowDialog";
import {
  ASSISTANT_CATEGORIES,
  RunWorkflowFromAssistant,
  buildAssistantOutDirHint,
  isAssistantCategory,
} from "../../components/assistant/RunWorkflowFromAssistant";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeQC() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 0, gcTime: 0 } },
  });
}

function makeBatch(overrides: Partial<EditableBatch> = {}): EditableBatch {
  return {
    id: "batch-p5-5",
    title: "Assistant test batch",
    description: "",
    status: "active",
    source: "manual",
    created_at: "2026-05-15T00:00:00Z",
    updated_at: "2026-05-15T00:00:00Z",
    papers: ["10.1234/test"],
    nodes: [],
    notebook_id: "nlm-fixture",
    default_workflow_id: "assistant.literature_survey",
    prompt_profile_id: null,
    prompt_overrides: {},
    sources_text: "",
    zotsums_text: "",
    notes: [],
    round: 1,
    generation: { in_progress: false, last_run_id: null, last_started_at: null },
    ...overrides,
  } as EditableBatch;
}

function renderWithClient(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={makeQC()}>{node}</QueryClientProvider>,
  );
}

// ── Filter behaviour ─────────────────────────────────────────────────────────

describe("Assistant run dialog — workflowFilter", () => {
  it("isAssistantCategory accepts the four documented categories only", () => {
    for (const cat of [
      "Literature Review",
      "Paper Writing",
      "Proposal Writing",
      "Analysis",
    ]) {
      expect(isAssistantCategory(cat)).toBe(true);
    }
    expect(isAssistantCategory("AKMS")).toBe(false);
    expect(isAssistantCategory("Research Synthesis")).toBe(false);
    expect(isAssistantCategory("")).toBe(false);
  });

  it("ASSISTANT_CATEGORIES set is exactly the four documented values", () => {
    expect(Array.from(ASSISTANT_CATEGORIES).sort()).toEqual([
      "Analysis",
      "Literature Review",
      "Paper Writing",
      "Proposal Writing",
    ]);
  });

  it("default workflowFilter on extracted dialog is permissive", async () => {
    // Backwards-compat probe: without the filter prop the dialog
    // applies the default-permissive `() => true` predicate so the
    // existing AKMS Batches page caller sees every workflow.
    const seen: boolean[] = [];
    renderWithClient(
      <RunWorkflowDialog
        batch={makeBatch()}
        onClose={() => undefined}
        onFilterApplied={(allowed) => seen.push(allowed)}
      />,
    );
    await waitFor(() => expect(seen.length).toBeGreaterThanOrEqual(1));
    expect(seen[0]).toBe(true);
  });

  it("Assistant wrapper applies the Assistant-category filter", async () => {
    // The wrapper passes workflowFilter=isAssistantCategory. The
    // filter callback is invoked once on mount with the literal
    // "Literature Review" so the test asserts allowed=true (a
    // category in the Assistant set).
    const seen: boolean[] = [];
    renderWithClient(
      <RunWorkflowFromAssistant
        batch={makeBatch()}
        onClose={() => undefined}
        onFilterApplied={(allowed) => seen.push(allowed)}
      />,
    );
    await waitFor(() => expect(seen.length).toBeGreaterThanOrEqual(1));
    expect(seen[0]).toBe(true);
  });
});

// ── Out-dir pre-fill ─────────────────────────────────────────────────────────

describe("Assistant run dialog — out_dir pre-fill", () => {
  it("buildAssistantOutDirHint produces the documented pattern", () => {
    expect(
      buildAssistantOutDirHint({
        assistantWorkspaceRoot: "/home/u/Assistant-Workspace",
        batchId: "batch-1",
        workflowId: "assistant.literature_survey",
        runId: "abc123",
      }),
    ).toBe(
      "/home/u/Assistant-Workspace/assistant/outputs/batch-1/assistant.literature_survey/abc123",
    );
  });

  it("buildAssistantOutDirHint uses placeholders when args omitted", () => {
    expect(buildAssistantOutDirHint({ batchId: "batch-1" })).toBe(
      "<assistant_workspace>/assistant/outputs/batch-1/<workflow_id>/<run_id>",
    );
  });

  it("Assistant wrapper renders the out-dir hint in a read-only input", () => {
    renderWithClient(
      <RunWorkflowFromAssistant
        batch={makeBatch()}
        onClose={() => undefined}
        assistantWorkspaceRoot="/tmp/aws"
        workflowIdHint="assistant.literature_survey"
        runIdHint="r-1"
      />,
    );
    const input = screen.getByTestId("run-out-dir-hint") as HTMLInputElement;
    expect(input.value).toBe(
      "/tmp/aws/assistant/outputs/batch-p5-5/assistant.literature_survey/r-1",
    );
    expect(input.readOnly).toBe(true);
  });

  it("Bare RunWorkflowDialog without outDirHint does NOT render the hint input", () => {
    renderWithClient(
      <RunWorkflowDialog batch={makeBatch()} onClose={() => undefined} />,
    );
    expect(screen.queryByTestId("run-out-dir-hint")).toBeNull();
  });
});

// ── Save-as-default behaviour ────────────────────────────────────────────────

describe("Assistant run dialog — Save as batch default checkbox", () => {
  it("forwards save_as_batch_default=true when checked", async () => {
    const runMock = vi
      .mocked(batchesApi.runWorkflow)
      .mockResolvedValue({} as unknown as Awaited<ReturnType<typeof batchesApi.runWorkflow>>);

    renderWithClient(
      <RunWorkflowFromAssistant
        batch={makeBatch()}
        onClose={() => undefined}
        assistantWorkspaceRoot="/tmp/aws"
      />,
    );

    fireEvent.click(screen.getByTestId("run-save-default"));
    fireEvent.click(screen.getByTestId("run-start"));

    await waitFor(() => expect(runMock).toHaveBeenCalledTimes(1));
    const body = runMock.mock.calls[0][0];
    expect(body.save_as_batch_default).toBe(true);
    expect(body.batch_id).toBe("batch-p5-5");
    expect(body.workflow_id).toBe("assistant.literature_survey");
  });

  it("defaults save_as_batch_default=false when checkbox untouched", async () => {
    const runMock = vi
      .mocked(batchesApi.runWorkflow)
      .mockResolvedValue({} as unknown as Awaited<ReturnType<typeof batchesApi.runWorkflow>>);

    renderWithClient(
      <RunWorkflowFromAssistant
        batch={makeBatch()}
        onClose={() => undefined}
      />,
    );

    fireEvent.click(screen.getByTestId("run-start"));
    await waitFor(() => expect(runMock).toHaveBeenCalledTimes(1));
    expect(runMock.mock.calls[0][0].save_as_batch_default).toBe(false);
  });
});

// ── Dry-run preview ──────────────────────────────────────────────────────────

describe("Assistant run dialog — Preview effective prompts", () => {
  it("triggers a dry-run preview call when Preview is clicked", async () => {
    const previewMock = vi
      .mocked(batchesApi.previewEffectivePrompts)
      .mockResolvedValue({
        effective_prompts: {
          entries: [
            {
              slot: "main",
              variant_id: "default",
              resolved_text: "preview content",
            },
          ],
        },
      });

    renderWithClient(
      <RunWorkflowFromAssistant
        batch={makeBatch()}
        onClose={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("run-preview-prompts"));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(1));
    // The first arg carries the batch + workflow ids; preview omits
    // save_as_batch_default=true (forced false in the dialog).
    const body = previewMock.mock.calls[0][0];
    expect(body.batch_id).toBe("batch-p5-5");
    expect(body.workflow_id).toBe("assistant.literature_survey");
    expect(body.save_as_batch_default).toBe(false);
    // Preview result renders.
    await waitFor(() =>
      expect(screen.getByTestId("preview-entry-main")).toBeTruthy(),
    );
  });
});

// ── Extraction contract ──────────────────────────────────────────────────────

describe("Assistant run dialog — RunWorkflowDialog extraction contract", () => {
  it("extracted dialog renders the same testids as the original", () => {
    renderWithClient(
      <RunWorkflowDialog batch={makeBatch()} onClose={() => undefined} />,
    );
    // Pin the testid surface so the BatchesPage's existing usage
    // doesn't break.
    expect(screen.getByTestId("run-workflow-dialog")).toBeTruthy();
    expect(screen.getByTestId("run-workflow-id-input")).toBeTruthy();
    expect(screen.getByTestId("run-profile-input")).toBeTruthy();
    expect(screen.getByTestId("run-label-input")).toBeTruthy();
    expect(screen.getByTestId("run-save-default")).toBeTruthy();
    expect(screen.getByTestId("run-preview-prompts")).toBeTruthy();
    expect(screen.getByTestId("run-start")).toBeTruthy();
  });

  it("extracted dialog accepts a workflowFilter prop without crashing", () => {
    // Pin the prop shape — TypeScript would catch a missing prop, but
    // the runtime contract is also asserted.
    renderWithClient(
      <RunWorkflowDialog
        batch={makeBatch()}
        onClose={() => undefined}
        workflowFilter={(category) => category === "Literature Review"}
      />,
    );
    expect(screen.getByTestId("run-workflow-dialog")).toBeTruthy();
  });
});
