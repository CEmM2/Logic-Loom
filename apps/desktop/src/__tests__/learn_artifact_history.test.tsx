// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Learn tab artifact history, open-export-folder, and help panel.
 *
 * Contract: history lists prior artifacts; open-folder works; help panel explains provenance.
 *
 * Three test cases:
 *   (a) history: compile twice → both artifacts appear in session history list
 *   (b) open-export-folder:
 *       - Tauri runtime: mock isTauriRuntime() = true and openPath, assert called
 *         with the cache_dir from the compile response
 *       - Non-Tauri (browser): button is disabled and a hint is rendered, no crash
 *   (c) help panel: renders the provenance-explanation content, collapses/expands
 *
 * Mirrors the mode-registry / mode-forms / preview-panels suites' mock patterns: stub getLearnStatus / getLearnProfiles /
 * getLearnExporters / compileLearningSource; render LearnRoute.
 */

// ── Mocks ─────────────────────────────────────────────────────────────────────
// Partial-mock: stub network fns only; keep types + learnArtifactUrl real.
vi.mock("../api/learn", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/learn")>();
  return {
    ...actual,
    getLearnStatus: vi.fn(),
    getLearnProfiles: vi.fn(async () => ({ profiles: [] })),
    getLearnExporters: vi.fn(async () => ({ exporters: [] })),
    compileLearningSource: vi.fn(),
    learnArtifactUrl: vi.fn(
      async (id: string, seg: string) =>
        `https://test.local/api/learn/artifacts/${encodeURIComponent(id)}/${encodeURIComponent(seg)}`,
    ),
  };
});

// Mock isTauriRuntime from api/client — controls Tauri vs. browser degradation.
// NOTE: vi.mock hoisting means the factory runs at module-parse time. We use a
// shared mutable ref so individual tests can flip the value per scenario.
const tauriRuntimeRef = { value: false };
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    isTauriRuntime: () => tauriRuntimeRef.value,
  };
});

// Mock @tauri-apps/plugin-opener so we can assert openPath is called with
// the right folder path. The dynamic import inside OpenFolderButton resolves to
// this mock regardless of the isTauriRuntime branch.
const openPathMock = vi.fn();
vi.mock("@tauri-apps/plugin-opener", () => ({
  openPath: (...args: unknown[]) => openPathMock(...args),
}));

import { compileLearningSource, getLearnStatus } from "../api/learn";
import type { LearnCompileResponse, LearnStatus } from "../api/learn";
import LearnRoute from "../routes/akms/learn";

const getStatus = getLearnStatus as unknown as ReturnType<typeof vi.fn>;
const compileFn = compileLearningSource as unknown as ReturnType<typeof vi.fn>;

// ── Fixtures ──────────────────────────────────────────────────────────────────

const AVAILABLE_STATUS: LearnStatus = {
  akms_learn_available: true,
  capabilities: ["deterministic_outline", "node_anthology", "markdown_export"],
  generation_modes: ["deterministic_outline", "node_anthology"],
  version: "0.1.0",
  plugin_api: "akms-learn-plugin/v1",
};

/** First compile response — artifact_id "alpha111", topic "Graph Traversal" */
const RESPONSE_A: LearnCompileResponse = {
  artifact_id: "alpha111",
  markdown: "# Graph Traversal\n\nContent A.",
  warnings: [],
  provenance_summary: {
    request_hash: "aabbccdd",
    graph_hash: "11223344",
    topic: "Graph Traversal",
    generation_option: "deterministic_outline",
    node_count: 3,
    edge_count: 2,
  },
  cache_dir: "/cache/alpha111",
  packet_path: "/cache/alpha111/aabbccdd.json",
  exports: [
    { path: "/cache/alpha111/lesson.md", type: "md" },
    { path: "/cache/alpha111/lesson.ipynb", type: "ipynb" },
  ],
};

/** Second compile response — artifact_id "beta222", topic "Sorting Algorithms" */
const RESPONSE_B: LearnCompileResponse = {
  artifact_id: "beta222",
  markdown: "# Sorting Algorithms\n\nContent B.",
  warnings: [],
  provenance_summary: {
    request_hash: "eeff0011",
    graph_hash: "55667788",
    topic: "Sorting Algorithms",
    generation_option: "node_anthology",
    node_count: 5,
    edge_count: 4,
  },
  cache_dir: "/cache/beta222",
  packet_path: "/cache/beta222/eeff0011.json",
  exports: [
    { path: "/cache/beta222/lesson.md", type: "md" },
  ],
};

/** Response with null cache_dir — for open-folder disabled test. */
const RESPONSE_NO_CACHE: LearnCompileResponse = {
  ...RESPONSE_A,
  artifact_id: "nocache99",
  cache_dir: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
}

function renderLearn() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter initialEntries={[{ pathname: "/akms/learn" }]}>
        <LearnRoute />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Fill required fields and click compile. Waits for the button to be enabled. */
async function fillAndCompile(topic = "My Topic", goal = "My Goal", node = "node-1") {
  await waitFor(() => {
    expect(screen.getByTestId("learn-compile")).toBeTruthy();
  });
  fireEvent.change(screen.getByTestId("learn-topic"), { target: { value: topic } });
  fireEvent.change(screen.getByTestId("learn-goal"), { target: { value: goal } });
  fireEvent.change(screen.getByTestId("learn-node"), { target: { value: node } });
  const btn = screen.getByTestId("learn-compile") as HTMLButtonElement;
  await waitFor(() => expect(btn.disabled).toBe(false));
  fireEvent.click(btn);
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

beforeEach(() => {
  getStatus.mockReset();
  compileFn.mockReset();
  openPathMock.mockReset();
  tauriRuntimeRef.value = false; // default: browser (non-Tauri)
  getStatus.mockResolvedValue(AVAILABLE_STATUS);
  window.localStorage.clear(); // history is saved in localStorage
});

afterEach(() => {
  cleanup();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Learn tab — artifact history, open-export-folder, help panel", () => {
  it("history survives a reload (saved in localStorage)", async () => {
    compileFn.mockResolvedValueOnce(RESPONSE_A);
    const first = renderLearn();
    await fillAndCompile("Graph Traversal", "Understand traversal");
    await waitFor(() => {
      expect(screen.getByTestId("learn-history-entry-alpha111")).toBeTruthy();
    });
    first.unmount();

    // A fresh mount (what a reload does) shows the saved entry without compiling.
    renderLearn();
    await waitFor(() => {
      expect(screen.getByTestId("learn-history-entry-alpha111")).toBeTruthy();
    });
    expect(compileFn).toHaveBeenCalledTimes(1);
  });

  it("ignores corrupt saved history", async () => {
    window.localStorage.setItem("logic-loom.learn.history.v1", "{not json");
    renderLearn();
    await waitFor(() => {
      expect(screen.getByTestId("learn-compile")).toBeTruthy();
    });
    expect(screen.queryByTestId("learn-artifact-history")).toBeNull();
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case (a): artifact history lists prior compiled artifacts
  // ──────────────────────────────────────────────────────────────────────────
  it("history lists prior compiled artifacts — compile twice, both appear in the history panel", async () => {
    // First compile returns RESPONSE_A; second returns RESPONSE_B.
    compileFn.mockResolvedValueOnce(RESPONSE_A).mockResolvedValueOnce(RESPONSE_B);
    renderLearn();

    // ── Before any compile — history panel must NOT appear yet ───────────
    // (it only renders once entries.length > 0)
    await waitFor(() => {
      expect(screen.getByTestId("learn-compile")).toBeTruthy();
    });
    expect(screen.queryByTestId("learn-artifact-history")).toBeNull();

    // ── First compile ─────────────────────────────────────────────────────
    await fillAndCompile("Graph Traversal", "Understand traversal");

    // History panel appears with one entry.
    await waitFor(() => {
      expect(screen.getByTestId("learn-artifact-history")).toBeTruthy();
    });

    // The first compile's artifact must be present.
    expect(screen.getByTestId("learn-history-entry-alpha111")).toBeTruthy();
    // Topic from provenance_summary must appear.
    expect(
      screen.getByTestId("learn-history-topic-alpha111").textContent,
    ).toContain("Graph Traversal");
    // Mode badge present.
    expect(
      screen.getByTestId("learn-history-mode-alpha111").textContent,
    ).toContain("deterministic_outline");
    // Export count (2 exports in RESPONSE_A).
    expect(
      screen.getByTestId("learn-history-exports-alpha111").textContent,
    ).toContain("2");

    // ── Second compile ────────────────────────────────────────────────────
    await fillAndCompile("Sorting Algorithms", "Learn sorting");

    // Both entries now appear.
    await waitFor(() => {
      expect(screen.getByTestId("learn-history-entry-beta222")).toBeTruthy();
    });
    expect(screen.getByTestId("learn-history-entry-alpha111")).toBeTruthy();

    // History count shows 2.
    expect(screen.getByTestId("learn-artifact-history").textContent).toContain("2 compiles");

    // Second compile entry.
    expect(
      screen.getByTestId("learn-history-topic-beta222").textContent,
    ).toContain("Sorting Algorithms");
    expect(
      screen.getByTestId("learn-history-exports-beta222").textContent,
    ).toContain("1 export");
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case (b): open-export-folder
  //   b1. Tauri runtime: button enabled, openPath called with cache_dir
  //   b2. Non-Tauri (browser): button disabled + hint rendered, no crash
  //   b3. null cache_dir: button disabled regardless of runtime
  // ──────────────────────────────────────────────────────────────────────────
  it("open-export-folder: invoked via Tauri runtime with correct path; degrades gracefully without Tauri", async () => {
    compileFn.mockResolvedValue(RESPONSE_A);

    // ── b2: Non-Tauri (browser) — default tauriRuntimeRef.value = false ──
    renderLearn();
    await fillAndCompile("Graph Traversal", "Understand traversal");

    // Wait for compile to settle (the open-folder button appears in the results).
    await waitFor(() => {
      expect(screen.getByTestId("learn-open-folder")).toBeTruthy();
    });

    const btnBrowser = screen.getByTestId("learn-open-folder") as HTMLButtonElement;

    // Must be DISABLED in non-Tauri environment — no crash.
    expect(btnBrowser.disabled).toBe(true);

    // A hint explaining it requires the desktop app must be visible.
    expect(screen.getByTestId("learn-open-folder-hint")).toBeTruthy();
    expect(screen.getByTestId("learn-open-folder-hint").textContent).toContain(
      "desktop app",
    );

    // Clicking a disabled button must not call openPath.
    fireEvent.click(btnBrowser);
    expect(openPathMock).not.toHaveBeenCalled();

    // Clean up and re-render in Tauri mode.
    cleanup();
    compileFn.mockReset();
    compileFn.mockResolvedValue(RESPONSE_A);

    // ── b1: Tauri runtime ─────────────────────────────────────────────────
    tauriRuntimeRef.value = true;
    renderLearn();
    await fillAndCompile("Graph Traversal", "Understand traversal");

    await waitFor(() => {
      expect(screen.getByTestId("learn-open-folder")).toBeTruthy();
    });

    const btnTauri = screen.getByTestId("learn-open-folder") as HTMLButtonElement;

    // Must be ENABLED in Tauri mode when cache_dir is present.
    expect(btnTauri.disabled).toBe(false);

    // Hint must NOT appear in Tauri mode.
    expect(screen.queryByTestId("learn-open-folder-hint")).toBeNull();

    // Clicking must invoke openPath with the correct cache_dir.
    openPathMock.mockResolvedValue(undefined);
    fireEvent.click(btnTauri);

    await waitFor(() => {
      expect(openPathMock).toHaveBeenCalledOnce();
    });
    expect(openPathMock).toHaveBeenCalledWith("/cache/alpha111");
  });

  it("open-export-folder disabled when cache_dir is null — no crash regardless of Tauri runtime", async () => {
    compileFn.mockResolvedValue(RESPONSE_NO_CACHE);
    tauriRuntimeRef.value = true; // even in Tauri, null path → disabled

    renderLearn();
    await fillAndCompile("Null Cache", "Test null path");

    await waitFor(() => {
      expect(screen.getByTestId("learn-open-folder")).toBeTruthy();
    });

    const btn = screen.getByTestId("learn-open-folder") as HTMLButtonElement;
    // Button must be disabled (null cache_dir means no path to open).
    expect(btn.disabled).toBe(true);

    // No crash on click.
    fireEvent.click(btn);
    expect(openPathMock).not.toHaveBeenCalled();
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case (c): help panel renders the source-provenance explanation
  // ──────────────────────────────────────────────────────────────────────────
  it("help panel renders source-provenance explanation — initially collapsed, expands on click", async () => {
    renderLearn();

    // Help panel is always present (not gated on a compile).
    await waitFor(() => {
      expect(screen.getByTestId("learn-help-panel")).toBeTruthy();
    });

    // Toggle button exists.
    const toggle = screen.getByTestId("learn-help-toggle");
    expect(toggle).toBeTruthy();
    // Initially collapsed — content not visible.
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("learn-help-content")).toBeNull();

    // Click to expand.
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    // Content is now visible.
    const content = screen.getByTestId("learn-help-content");
    expect(content).toBeTruthy();

    // Must explain the actual provenance fields from LearnProvenanceSummary.
    const text = content.textContent ?? "";
    expect(text).toContain("request_hash");
    expect(text).toContain("graph_hash");
    expect(text).toContain("node_count");
    expect(text).toContain("edge_count");
    expect(text).toContain("generation_option");
    expect(text).toContain("artifact_id");

    // Must explain where artifacts live (cache directory).
    expect(text).toContain("cache");

    // Must explain the generated-content policy (llm_expanded).
    expect(text).toContain("llm_expanded");
    expect(text).toContain("generated_content");

    // Provenance fields table rendered.
    expect(screen.getByTestId("learn-help-provenance-fields")).toBeTruthy();

    // Click again to collapse.
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("learn-help-content")).toBeNull();
  });
});
