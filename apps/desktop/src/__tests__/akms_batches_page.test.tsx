// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * AKMS Batches page (list + detail + Run-workflow dialog).
 *
 * Covers:
 *   1. Editing a batch posts a PATCH and the next GET reflects the change.
 *   2. The Run dialog's "Preview effective prompts" renders the resolved
 *      variants (POST /api/runs/workflow with dry_run=true).
 */

vi.mock("../api/batches", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/batches")>();
  return {
    ...actual,
    listBatches: vi.fn(),
    getBatch: vi.fn(),
    patchBatch: vi.fn(),
    runWorkflow: vi.fn(),
    previewEffectivePrompts: vi.fn(),
  };
});

import * as batchesApi from "../api/batches";
import type { EditableBatch } from "../api/batches";
import { BatchesPage } from "../components/batches/BatchesPage";

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
}

function renderPage() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter>
        <BatchesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function makeBatch(overrides: Partial<EditableBatch> = {}): EditableBatch {
  return {
    id: "alpha",
    title: "Alpha Batch",
    description: "Original description",
    round: 1,
    round_title: "",
    round_theme: "",
    status: "active",
    source: "user",
    created_at: "2026-05-14T00:00:00Z",
    updated_at: "2026-05-14T00:00:00Z",
    papers: ["paperA", "paperB"],
    nodes: [],
    notebook_id: null,
    zotsums_text: "",
    sources_text: "",
    notes: "",
    default_workflow_id: "wf-default",
    prompt_profile_id: "profile-default",
    prompt_overrides: {},
    ...overrides,
  };
}

describe("AKMS Batches page", () => {
  beforeEach(() => {
    (batchesApi.listBatches as unknown as ReturnType<typeof vi.fn>).mockReset();
    (batchesApi.getBatch as unknown as ReturnType<typeof vi.fn>).mockReset();
    (batchesApi.patchBatch as unknown as ReturnType<typeof vi.fn>).mockReset();
    (batchesApi.runWorkflow as unknown as ReturnType<typeof vi.fn>).mockReset();
    (batchesApi.previewEffectivePrompts as unknown as ReturnType<typeof vi.fn>).mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("editing a batch posts a PATCH and the next GET reflects the change", async () => {
    const initial = makeBatch();
    const updated = makeBatch({ title: "Renamed Batch" });

    (batchesApi.listBatches as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      batches: [initial],
    });

    // First getBatch returns initial; after patch, subsequent getBatch returns updated.
    const getBatchMock = batchesApi.getBatch as unknown as ReturnType<typeof vi.fn>;
    getBatchMock
      .mockResolvedValueOnce(initial)
      .mockResolvedValue(updated);

    (batchesApi.patchBatch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      updated,
    );

    renderPage();

    // Detail tab loads on the first batch.
    await waitFor(() => {
      expect(screen.getByTestId("batch-overview-form")).toBeTruthy();
    });
    const titleInput = screen.getByTestId("batch-title-input") as HTMLInputElement;
    expect(titleInput.value).toBe("Alpha Batch");

    fireEvent.change(titleInput, { target: { value: "Renamed Batch" } });
    fireEvent.click(screen.getByTestId("batch-overview-save"));

    // PATCH was called with the new title.
    await waitFor(() => {
      expect(batchesApi.patchBatch).toHaveBeenCalledWith(
        "alpha",
        expect.objectContaining({ title: "Renamed Batch" }),
      );
    });

    // After invalidation the next GET returns the updated batch — the form
    // re-mounts with the new title from the refetched batch.
    await waitFor(() => {
      const t = screen.getByTestId("batch-title-input") as HTMLInputElement;
      expect(t.value).toBe("Renamed Batch");
    });
  });

  it("dialog 'Preview effective prompts' renders the resolved variants", async () => {
    const initial = makeBatch();
    (batchesApi.listBatches as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      batches: [initial],
    });
    (batchesApi.getBatch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      initial,
    );
    (batchesApi.previewEffectivePrompts as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      effective_prompts: {
        entries: [
          {
            slot: "summarize",
            variant_id: "summarize.v2",
            resolved_text: "Summarize the following sources…",
            family: "akms",
          },
          {
            slot: "extract",
            variant_id: "extract.default",
            resolved_text: "Extract key claims…",
            family: "akms",
          },
        ],
      },
    });

    renderPage();

    // Open the run dialog from the detail header.
    const runBtn = await screen.findByTestId("run-workflow-button");
    fireEvent.click(runBtn);

    const dialog = await screen.findByTestId("run-workflow-dialog");
    expect(dialog).toBeTruthy();

    // The workflow_id input pre-fills from the batch default — so Preview
    // is enabled immediately.
    const previewBtn = screen.getByTestId("run-preview-prompts");
    fireEvent.click(previewBtn);

    // Resolved variants render in the preview list.
    await waitFor(() => {
      expect(screen.getByTestId("run-preview-results")).toBeTruthy();
    });
    expect(screen.getByTestId("preview-entry-summarize").textContent).toMatch(/summarize\.v2/);
    expect(screen.getByTestId("preview-entry-extract").textContent).toMatch(/extract\.default/);

    // The preview call carried dry_run=true via previewEffectivePrompts.
    expect(batchesApi.previewEffectivePrompts).toHaveBeenCalledWith(
      expect.objectContaining({
        batch_id: "alpha",
        workflow_id: "wf-default",
      }),
    );
  });
});
