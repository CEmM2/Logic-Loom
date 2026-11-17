// @vitest-environment jsdom
/**
 * Tests for the Assistant Batches page (frontend).
 *
 * Covers:
 *   1. Full create -> edit -> archive flow round-trips through the API
 *      (mocked via vitest).
 *   2. Add sources from a discovered-paper-shaped row updates the table.
 *   3. Send-to-AKMS click surfaces akms_batch_id in a toast/status banner.
 */
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api/assistantBatches", async () => {
  return {
    listAssistantBatches: vi.fn(),
    getAssistantBatch: vi.fn(),
    createAssistantBatch: vi.fn(),
    patchAssistantBatch: vi.fn(),
    archiveAssistantBatch: vi.fn(),
    duplicateAssistantBatch: vi.fn(),
    addAssistantBatchSources: vi.fn(),
    removeAssistantBatchSources: vi.fn(),
    sendAssistantBatchToAkms: vi.fn(),
  };
});

import * as api from "../../api/assistantBatches";
import AssistantBatchesRoute from "../../routes/assistant/batches";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeQC() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 0, gcTime: 0 } },
  });
}

function renderRoute(initialPath = "/assistant/batches") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={makeQC()}>
        <AssistantBatchesRoute />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function makeBatch(
  overrides: Partial<api.AssistantBatch> = {},
): api.AssistantBatch {
  return {
    schema_version: 1,
    id: "b1",
    title: "Batch One",
    description: "",
    status: "active",
    tags: [],
    purpose: null,
    sources: [],
    notebook_id: null,
    default_workflow_id: null,
    prompt_profile_id: null,
    prompt_overrides: {},
    akms_node_plan: [],
    created_at: "2026-05-15T00:00:00+00:00",
    updated_at: "2026-05-15T00:00:00+00:00",
    ...overrides,
  };
}

describe("Assistant Batches page (frontend)", () => {
  beforeEach(() => {
    vi.mocked(api.listAssistantBatches).mockResolvedValue({ batches: [] });
    vi.mocked(api.getAssistantBatch).mockResolvedValue(makeBatch());
  });

  it("create -> edit -> archive round-trips through the API", async () => {
    // First load: empty list.
    vi.mocked(api.listAssistantBatches).mockResolvedValueOnce({
      batches: [],
    });
    const created = makeBatch({ id: "fresh", title: "Fresh" });
    vi.mocked(api.createAssistantBatch).mockResolvedValueOnce(created);
    // After create, list returns the new batch and detail returns it.
    vi.mocked(api.listAssistantBatches).mockResolvedValue({
      batches: [created],
    });
    vi.mocked(api.getAssistantBatch).mockResolvedValue(created);

    renderRoute();

    await waitFor(() => {
      expect(screen.getByTestId("batch-list-empty")).toBeTruthy();
    });

    // Create a new batch.
    fireEvent.change(screen.getByTestId("new-batch-title"), {
      target: { value: "Fresh" },
    });
    fireEvent.click(screen.getByTestId("new-batch-create"));

    await waitFor(() => {
      expect(api.createAssistantBatch).toHaveBeenCalledWith({
        title: "Fresh",
      });
    });
    // Once detail loads, the overview editor shows the title.
    await waitFor(() => {
      const titleInput = screen.getByTestId(
        "overview-title",
      ) as HTMLInputElement;
      expect(titleInput.value).toBe("Fresh");
    });

    // Edit description and Save -> PATCH called.
    const patched = makeBatch({
      id: "fresh",
      title: "Fresh",
      description: "edited",
    });
    vi.mocked(api.patchAssistantBatch).mockResolvedValueOnce(patched);
    fireEvent.change(screen.getByTestId("overview-description"), {
      target: { value: "edited" },
    });
    fireEvent.click(screen.getByTestId("overview-save"));

    await waitFor(() => {
      expect(api.patchAssistantBatch).toHaveBeenCalledWith(
        "fresh",
        expect.objectContaining({ description: "edited" }),
      );
    });

    // Click Archive -> archive endpoint called.
    const archived = makeBatch({
      id: "fresh",
      title: "Fresh",
      status: "archived",
    });
    vi.mocked(api.archiveAssistantBatch).mockResolvedValueOnce(archived);
    fireEvent.click(screen.getByTestId("batch-action-archive"));
    await waitFor(() => {
      expect(api.archiveAssistantBatch).toHaveBeenCalledWith("fresh");
    });
  });

  it("adding a source via the form posts to the add endpoint and the table updates", async () => {
    const seed = makeBatch({ id: "b1", title: "Batch One" });
    vi.mocked(api.listAssistantBatches).mockResolvedValue({
      batches: [seed],
    });
    vi.mocked(api.getAssistantBatch).mockResolvedValue(seed);

    renderRoute();

    // Wait for the list item to render and select it.
    await waitFor(() => {
      expect(screen.getByTestId("batch-list-item-b1")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("batch-list-item-b1"));
    // Switch to sources tab.
    await waitFor(() => {
      expect(screen.getByTestId("tab-sources")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("tab-sources"));

    // Initially empty.
    await waitFor(() => {
      expect(screen.getByTestId("sources-empty")).toBeTruthy();
    });

    // After add, the detail query returns a batch with the new source.
    const withSource = makeBatch({
      id: "b1",
      title: "Batch One",
      sources: [
        {
          id: "discovered-paper",
          kind: "discovered",
          title: "Discovered Paper",
        },
      ],
    });
    vi.mocked(api.addAssistantBatchSources).mockResolvedValueOnce(
      withSource,
    );
    vi.mocked(api.getAssistantBatch).mockResolvedValue(withSource);

    fireEvent.change(screen.getByTestId("add-source-title"), {
      target: { value: "Discovered Paper" },
    });
    fireEvent.click(screen.getByTestId("add-source-submit"));

    await waitFor(() => {
      expect(api.addAssistantBatchSources).toHaveBeenCalledWith(
        "b1",
        expect.arrayContaining([
          expect.objectContaining({
            kind: "discovered",
            title: "Discovered Paper",
          }),
        ]),
      );
    });
    // After invalidation the new row appears in the table.
    await waitFor(() => {
      expect(
        screen.getByTestId("source-row-discovered-paper"),
      ).toBeTruthy();
    });
  });

  it("Send-to-AKMS click surfaces akms_batch_id in a toast", async () => {
    const seed = makeBatch({ id: "b1", title: "Batch One" });
    vi.mocked(api.listAssistantBatches).mockResolvedValue({
      batches: [seed],
    });
    vi.mocked(api.getAssistantBatch).mockResolvedValue(seed);
    vi.mocked(api.sendAssistantBatchToAkms).mockResolvedValueOnce({
      akms_batch_id: "akms-fresh",
      akms_workspace_path: "/tmp/AKMS-Workspace",
    });

    renderRoute();

    // Wait for selection to land.
    await waitFor(() => {
      expect(screen.getByTestId("send-to-akms-button")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("send-to-akms-button"));

    await waitFor(() => {
      expect(screen.getByTestId("send-to-akms-toast")).toBeTruthy();
    });
    expect(screen.getByTestId("send-to-akms-id").textContent).toBe(
      "akms-fresh",
    );
    expect(api.sendAssistantBatchToAkms).toHaveBeenCalledWith("b1");
  });
});
