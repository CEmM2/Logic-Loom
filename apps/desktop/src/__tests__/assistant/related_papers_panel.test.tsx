// @vitest-environment jsdom
/**
 * Tests for the Assistant Related papers UI panel.
 *
 * Covers:
 *   1. All five modes selectable (similar / foundational / recent_citing /
 *      highly_cited_neighbours / review_candidates).
 *   2. Ranking changes re-sort without re-querying when feasible
 *      (client-side sort path).
 *   3. Filter toggles (PDF link, open access, hide-in-Zotero, hide-in-batch)
 *      apply to the rendered table.
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
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the assistantDiscover API BEFORE importing the component.
vi.mock("../../api/assistantDiscover", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/assistantDiscover")>(
      "../../api/assistantDiscover",
    );
  return {
    ...actual,
    discoverRelated: vi.fn(),
  };
});

import * as discoverApi from "../../api/assistantDiscover";
import type {
  AssistantBatch,
  BatchSourceItem,
} from "../../api/assistantBatches";
import {
  RelatedPaperPanel,
  RELATED_MODES as RELATED_MODE_OPTIONS,
  sortByRanking,
  applyClientFilters,
} from "../../components/assistant/RelatedPaperPanel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeQC() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 0, gcTime: 0 } },
  });
}

function makeSource(
  overrides: Partial<BatchSourceItem> = {},
): BatchSourceItem {
  return {
    id: "openalex:W1",
    kind: "zotero",
    title: "Seed paper",
    authors: ["Alice"],
    year: 2022,
    openalex_id: "W1",
    ...overrides,
  };
}

function makeBatch(overrides: Partial<AssistantBatch> = {}): AssistantBatch {
  return {
    schema_version: 1,
    id: "batch-1",
    title: "Test batch",
    description: "",
    status: "active",
    tags: [],
    purpose: null,
    sources: [makeSource()],
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

function makeRelatedPaper(
  overrides: Partial<discoverApi.DiscoveredPaperWithMatch> = {},
): discoverApi.DiscoveredPaperWithMatch {
  return {
    id: "openalex:W2",
    source: "openalex",
    doi: "10.2/x",
    openalex_id: "W2",
    semantic_scholar_id: null,
    title: "Related paper",
    year: 2024,
    authors: ["Bob"],
    citation_count: 10,
    is_open_access: true,
    pdf_url: "https://example.org/p.pdf",
    zotero_match: {
      citekey: "",
      library_id: null,
      item_key: null,
      confidence: 0,
      match_reason: "no_match",
      in_zotero: false,
    },
    ...overrides,
  };
}

function renderPanel(batch: AssistantBatch = makeBatch()) {
  return render(
    <QueryClientProvider client={makeQC()}>
      <RelatedPaperPanel batch={batch} />
    </QueryClientProvider>,
  );
}

describe("Related papers UI panel", () => {
  beforeEach(() => {
    vi.mocked(discoverApi.discoverRelated).mockResolvedValue({
      results: [],
      mode: "similar",
      ranking: "most_relevant",
      cached: false,
    });
  });

  it("renders all five modes in the mode dropdown", () => {
    renderPanel();
    const select = screen.getByTestId(
      "related-mode-select",
    ) as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll("option")).map(
      (o) => o.value,
    );
    expect(options).toEqual([
      "similar",
      "foundational",
      "recent_citing",
      "highly_cited_neighbours",
      "review_candidates",
    ]);
    expect(options.length).toBe(RELATED_MODE_OPTIONS.length);
    expect(options.length).toBe(5);
  });

  it("submits the request body with mode + ranking + filters combined", async () => {
    renderPanel();
    fireEvent.change(screen.getByTestId("related-mode-select"), {
      target: { value: "foundational" },
    });
    fireEvent.change(screen.getByTestId("related-ranking-select"), {
      target: { value: "most_cited" },
    });
    fireEvent.click(screen.getByTestId("related-filter-only-pdf"));
    fireEvent.click(screen.getByTestId("related-submit"));

    await waitFor(() => {
      expect(discoverApi.discoverRelated).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(discoverApi.discoverRelated).mock.calls[0][0];
    expect(payload.mode).toBe("foundational");
    expect(payload.ranking).toBe("most_cited");
    expect(payload.filters?.only_with_pdf).toBe(true);
    expect(payload.seed_paper_ids).toEqual(["openalex:W1"]);
    expect(payload.batch_id).toBe("batch-1");
  });

  it("ranking change re-sorts results without re-fetching", async () => {
    const papers = [
      makeRelatedPaper({ id: "openalex:WA", title: "Low cites", citation_count: 1, year: 2020 }),
      makeRelatedPaper({
        id: "openalex:WB",
        title: "High cites",
        citation_count: 9999,
        year: 2010,
      }),
    ];
    vi.mocked(discoverApi.discoverRelated).mockResolvedValueOnce({
      results: papers,
      mode: "similar",
      ranking: "most_relevant",
      cached: false,
    });
    renderPanel();
    fireEvent.click(screen.getByTestId("related-submit"));

    // Initial order: most_relevant preserves input order (Low cites first).
    await screen.findByText("Low cites");
    let rows = screen.getAllByText(/cites/);
    expect(rows[0].textContent).toBe("Low cites");

    // Change ranking to most_cited — should re-sort client-side without
    // re-fetching.
    fireEvent.change(screen.getByTestId("related-ranking-select"), {
      target: { value: "most_cited" },
    });
    await waitFor(() => {
      rows = screen.getAllByText(/cites/);
      expect(rows[0].textContent).toBe("High cites");
    });

    // No second fetch call (the ranking change re-sorted client-side).
    expect(discoverApi.discoverRelated).toHaveBeenCalledTimes(1);
  });

  it("hide-in-Zotero filter removes papers with in_zotero=true", async () => {
    const papers = [
      makeRelatedPaper({
        id: "openalex:WIN",
        title: "Already in library",
        zotero_match: {
          citekey: "in_lib",
          library_id: null,
          item_key: null,
          confidence: 1.0,
          match_reason: "doi",
          in_zotero: true,
        },
      }),
      makeRelatedPaper({
        id: "openalex:WOUT",
        title: "Fresh discovery",
      }),
    ];
    vi.mocked(discoverApi.discoverRelated).mockResolvedValueOnce({
      results: papers,
      mode: "similar",
      ranking: "most_relevant",
      cached: false,
    });
    renderPanel();
    fireEvent.click(screen.getByTestId("related-submit"));
    await screen.findByText("Already in library");

    // Toggle the filter.
    fireEvent.click(screen.getByTestId("related-filter-hide-in-zotero"));

    await waitFor(() => {
      expect(screen.queryByText("Already in library")).toBeNull();
    });
    expect(screen.getByText("Fresh discovery")).toBeTruthy();
  });

  it("hide-in-batch filter removes papers already present in the batch", async () => {
    const batch = makeBatch({
      sources: [
        makeSource({ id: "openalex:W1", title: "Seed", openalex_id: "W1" }),
        makeSource({ id: "openalex:WDUP", title: "Dup", openalex_id: "WDUP" }),
      ],
    });
    const papers = [
      // Same id as one of the batch sources.
      makeRelatedPaper({ id: "openalex:WDUP", title: "Dup" }),
      makeRelatedPaper({ id: "openalex:WX", title: "Novel" }),
    ];
    vi.mocked(discoverApi.discoverRelated).mockResolvedValueOnce({
      results: papers,
      mode: "similar",
      ranking: "most_relevant",
      cached: false,
    });
    renderPanel(batch);
    fireEvent.click(screen.getByTestId("related-submit"));
    await screen.findByText("Novel");
    expect(screen.getByText("Dup")).toBeTruthy();

    fireEvent.click(screen.getByTestId("related-filter-hide-in-batch"));
    await waitFor(() => {
      expect(screen.queryByText("Dup")).toBeNull();
    });
    expect(screen.getByText("Novel")).toBeTruthy();
  });

  it("seed selection toggles between 'all papers' and 'selected papers only'", async () => {
    const batch = makeBatch({
      sources: [
        makeSource({ id: "openalex:WA", title: "A", openalex_id: "WA" }),
        makeSource({ id: "openalex:WB", title: "B", openalex_id: "WB" }),
      ],
    });
    renderPanel(batch);

    // Default: all papers.
    fireEvent.click(screen.getByTestId("related-submit"));
    await waitFor(() =>
      expect(discoverApi.discoverRelated).toHaveBeenCalledTimes(1),
    );
    let payload = vi.mocked(discoverApi.discoverRelated).mock.calls[0][0];
    expect(payload.seed_paper_ids).toEqual(["openalex:WA", "openalex:WB"]);

    // Switch to selected-only.
    fireEvent.click(screen.getByTestId("related-seed-selected"));
    // Tick only paper B.
    fireEvent.click(screen.getByLabelText("Seed: B"));
    fireEvent.click(screen.getByTestId("related-submit"));
    await waitFor(() =>
      expect(discoverApi.discoverRelated).toHaveBeenCalledTimes(2),
    );
    payload = vi.mocked(discoverApi.discoverRelated).mock.calls[1][0];
    expect(payload.seed_paper_ids).toEqual(["openalex:WB"]);
  });
});

describe("Related papers: sortByRanking utility", () => {
  const papers = [
    makeRelatedPaper({ id: "a", citation_count: 5, year: 2020 }),
    makeRelatedPaper({ id: "b", citation_count: 999, year: 2010 }),
    makeRelatedPaper({ id: "c", citation_count: 0, year: 2024 }),
  ];

  it("most_relevant preserves input order", () => {
    expect(sortByRanking(papers, "most_relevant").map((p) => p.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("most_cited sorts by log(citations + 1) desc", () => {
    expect(sortByRanking(papers, "most_cited").map((p) => p.id)).toEqual([
      "b",
      "a",
      "c",
    ]);
  });

  it("recent_and_relevant sorts by year desc with stable tiebreak", () => {
    expect(
      sortByRanking(papers, "recent_and_relevant").map((p) => p.id),
    ).toEqual(["c", "a", "b"]);
  });

  it("foundational ranks citations first then older year", () => {
    // a:cites=5/yr=2020, b:cites=999/yr=2010, c:cites=0/yr=2024.
    // b wins (high cites). Older year breaks ties; here no tie.
    expect(sortByRanking(papers, "foundational").map((p) => p.id)).toEqual([
      "b",
      "a",
      "c",
    ]);
  });
});

describe("Related papers: applyClientFilters utility", () => {
  const inLib = makeRelatedPaper({
    id: "x",
    zotero_match: {
      citekey: "y",
      library_id: null,
      item_key: null,
      confidence: 1,
      match_reason: "doi",
      in_zotero: true,
    },
  });
  const fresh = makeRelatedPaper({ id: "z" });

  it("hide_in_zotero drops in_zotero=true", () => {
    const out = applyClientFilters([inLib, fresh], { hide_in_zotero: true }, new Set());
    expect(out.map((p) => p.id)).toEqual(["z"]);
  });

  it("hide_in_batch drops papers whose id is in the batch set", () => {
    const out = applyClientFilters(
      [inLib, fresh],
      { hide_in_batch: true },
      new Set(["x"]),
    );
    expect(out.map((p) => p.id)).toEqual(["z"]);
  });
});
