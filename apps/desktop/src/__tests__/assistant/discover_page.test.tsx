// @vitest-environment jsdom
/**
 * Tests for the Assistant Discovery UI (frontend half of discovery).
 *
 * Covers:
 *   1. A search returns merged + cross-referenced results — the Discover
 *      page renders rows with ZoteroStatusBadge and PdfAvailabilityBadge.
 *   2. The user can add a single result, multiple results, or all results
 *      to a batch via AddToBatchDialog / CreateBatchFromResultsDialog
 *      (the dialogs call createAssistantBatch + addAssistantBatchSources
 *      on the typed assistant batches client).
 *
 * Plus integration with ASSISTANT_ROUTE_BATCHES — no hardcoded URLs.
 */
import {
  afterEach,
  beforeEach,
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
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock both API clients BEFORE importing the page.
vi.mock("../../api/assistantBatches", async () => ({
  listAssistantBatches: vi.fn(),
  createAssistantBatch: vi.fn(),
  addAssistantBatchSources: vi.fn(),
}));
vi.mock("../../api/assistantDiscover", async () => {
  const actual = await vi.importActual<
    typeof import("../../api/assistantDiscover")
  >("../../api/assistantDiscover");
  return {
    ...actual,
    discoverSearch: vi.fn(),
  };
});

import * as batchesApi from "../../api/assistantBatches";
import * as discoverApi from "../../api/assistantDiscover";
import AssistantDiscoverRoute from "../../routes/assistant/discover";
import { ASSISTANT_ROUTE_BATCHES } from "../../routes/assistant/routes";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeQC() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 0, gcTime: 0 } },
  });
}

function renderPage() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter>
        <AssistantDiscoverRoute />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function makePaper(
  overrides: Partial<discoverApi.DiscoveredPaperWithMatch> = {},
): discoverApi.DiscoveredPaperWithMatch {
  return {
    id: "openalex:W1",
    source: "merged",
    doi: "10.1234/x",
    openalex_id: "W1",
    semantic_scholar_id: "S1",
    title: "A graphene paper",
    year: 2022,
    authors: ["Alice", "Bob"],
    pdf_url: "https://example.org/p.pdf",
    zotero_match: {
      citekey: "alice2022",
      library_id: null,
      item_key: null,
      confidence: 1.0,
      match_reason: "doi",
      in_zotero: true,
    },
    ...overrides,
  };
}

function makeBatch(
  overrides: Partial<batchesApi.AssistantBatch> = {},
): batchesApi.AssistantBatch {
  return {
    schema_version: 1,
    id: "batch-1",
    title: "Existing Batch",
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
    created_at: "2026-05-15T00:00:00Z",
    updated_at: "2026-05-15T00:00:00Z",
    ...overrides,
  };
}

describe("Assistant Discovery UI — frontend half", () => {
  beforeEach(() => {
    vi.mocked(batchesApi.listAssistantBatches).mockResolvedValue({
      batches: [makeBatch()],
    });
  });

  it("renders the Discover page with a search bar, filters, and an empty results view on mount", () => {
    vi.mocked(discoverApi.discoverSearch).mockResolvedValue({
      results: [],
      cached: false,
    });
    renderPage();
    expect(screen.getByText("Discover")).toBeTruthy();
    expect(screen.getByTestId("discovery-search-bar")).toBeTruthy();
    expect(screen.getByTestId("discovery-filters")).toBeTruthy();
    expect(screen.getByTestId("discovery-results-empty")).toBeTruthy();
  });

  it("running a search calls /api/assistant/discover/search and renders rows with Zotero + PDF badges", async () => {
    vi.mocked(discoverApi.discoverSearch).mockResolvedValue({
      results: [makePaper()],
      cached: false,
    });

    renderPage();
    fireEvent.change(screen.getByLabelText("Discovery query"), {
      target: { value: "graphene" },
    });
    fireEvent.click(screen.getByText("Search"));

    await waitFor(() => {
      expect(discoverApi.discoverSearch).toHaveBeenCalledWith({
        query: "graphene",
        filters: { only_with_pdf: false },
      });
    });
    expect(await screen.findByText("A graphene paper")).toBeTruthy();
    // Zotero + PDF badges render.
    expect(screen.getByTestId("zotero-status-badge").textContent).toMatch(
      /In Zotero \(certain\)/,
    );
    expect(screen.getByTestId("pdf-availability-badge").textContent).toMatch(
      /PDF available/,
    );
  });

  it("AddToBatchDialog adds the selected single row to an existing batch via addAssistantBatchSources (single)", async () => {
    vi.mocked(discoverApi.discoverSearch).mockResolvedValue({
      results: [makePaper()],
      cached: false,
    });
    vi.mocked(batchesApi.addAssistantBatchSources).mockResolvedValue(
      makeBatch({ sources: [{ id: "openalex:W1", kind: "discovered", title: "A graphene paper" }] }),
    );

    renderPage();
    fireEvent.change(screen.getByLabelText("Discovery query"), {
      target: { value: "graphene" },
    });
    fireEvent.click(screen.getByText("Search"));

    // Wait for the row to mount then click its per-row "Add to batch" button.
    await screen.findByText("A graphene paper");
    fireEvent.click(screen.getByText("Add to batch"));

    expect(await screen.findByTestId("add-to-batch-dialog")).toBeTruthy();
    fireEvent.click(screen.getByTestId("add-to-batch-confirm"));

    await waitFor(() => {
      expect(batchesApi.addAssistantBatchSources).toHaveBeenCalledTimes(1);
    });
    const [batchId, items] = vi.mocked(batchesApi.addAssistantBatchSources)
      .mock.calls[0];
    expect(batchId).toBe("batch-1");
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe("discovered");
    expect(items[0].id).toBe("openalex:W1");
  });

  it("selecting multiple rows and clicking 'Add selected to batch' adds all selected rows via addAssistantBatchSources (multiple)", async () => {
    const papers = [makePaper(), makePaper({ id: "openalex:W2", title: "Second" })];
    vi.mocked(discoverApi.discoverSearch).mockResolvedValue({
      results: papers,
      cached: false,
    });
    vi.mocked(batchesApi.addAssistantBatchSources).mockResolvedValue(
      makeBatch(),
    );

    renderPage();
    fireEvent.change(screen.getByLabelText("Discovery query"), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByText("Search"));

    await screen.findByText("A graphene paper");
    // Tick both row checkboxes.
    fireEvent.click(screen.getByLabelText("Select A graphene paper"));
    fireEvent.click(screen.getByLabelText("Select Second"));

    // Bulk button reflects the selected count.
    const bulkBtn = screen.getByTestId("discover-add-selected");
    expect(bulkBtn.textContent).toMatch(/\(2\)/);
    fireEvent.click(bulkBtn);

    expect(await screen.findByTestId("add-to-batch-dialog")).toBeTruthy();
    fireEvent.click(screen.getByTestId("add-to-batch-confirm"));

    await waitFor(() => {
      expect(batchesApi.addAssistantBatchSources).toHaveBeenCalledTimes(1);
    });
    const [, items] = vi.mocked(batchesApi.addAssistantBatchSources).mock
      .calls[0];
    expect(items).toHaveLength(2);
    expect(items.map((i) => i.id).sort()).toEqual([
      "openalex:W1",
      "openalex:W2",
    ]);
  });

  it("CreateBatchFromResultsDialog creates a new batch from all results via createAssistantBatch + addAssistantBatchSources (all)", async () => {
    const papers = [makePaper(), makePaper({ id: "openalex:W2", title: "Second" })];
    vi.mocked(discoverApi.discoverSearch).mockResolvedValue({
      results: papers,
      cached: false,
    });
    vi.mocked(batchesApi.createAssistantBatch).mockResolvedValue(
      makeBatch({ id: "batch-new", title: "New" }),
    );
    vi.mocked(batchesApi.addAssistantBatchSources).mockResolvedValue(
      makeBatch({ id: "batch-new" }),
    );

    renderPage();
    fireEvent.change(screen.getByLabelText("Discovery query"), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByText("Search"));

    await screen.findByText("A graphene paper");
    fireEvent.click(screen.getByTestId("discover-create-from-all"));

    expect(
      await screen.findByTestId("create-batch-from-results-dialog"),
    ).toBeTruthy();
    fireEvent.change(screen.getByTestId("create-batch-title"), {
      target: { value: "Fresh Batch" },
    });
    fireEvent.click(screen.getByTestId("create-batch-confirm"));

    await waitFor(() => {
      expect(batchesApi.createAssistantBatch).toHaveBeenCalledWith({
        title: "Fresh Batch",
      });
    });
    await waitFor(() => {
      expect(batchesApi.addAssistantBatchSources).toHaveBeenCalledTimes(1);
    });
    const [batchId, items] = vi.mocked(batchesApi.addAssistantBatchSources)
      .mock.calls[0];
    expect(batchId).toBe("batch-new");
    expect(items).toHaveLength(2);
  });

  it("the Discover page consumes ASSISTANT_ROUTE_BATCHES rather than hardcoded /assistant/batches", () => {
    vi.mocked(discoverApi.discoverSearch).mockResolvedValue({
      results: [],
      cached: false,
    });
    renderPage();
    const link = screen.getByTestId(
      "discover-to-batches-link",
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(ASSISTANT_ROUTE_BATCHES);
  });
});
