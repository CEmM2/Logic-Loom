// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Learn tab structured preview panels.
 *
 * Contract: each panel renders its data when present and degrades gracefully when absent.
 *
 * Note: basic markdown + flat warnings already covered by learn_tab_suite.test.tsx case 4 —
 * this suite focuses on the FOUR NEW structured panels:
 *   (a) ProvenanceJsonPanel     — packet/provenance JSON via JSON.stringify/<pre>
 *   (b) NotebookMetaPanel       — cell_count + executed from exports[].metadata
 *   (c) AssessmentPresencePanel — has_public/has_answer_key presence flags only
 *   (d) GeneratedWarningsPanel  — warnings with category === "generated_content"
 *
 * Two test cases:
 *   1. Each new panel renders its data from a realistic compile response.
 *   2. Absent/empty metadata + category degrades gracefully (no crash, panels hide).
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

import { compileLearningSource, getLearnStatus } from "../api/learn";
import type { LearnCompileResponse, LearnStatus } from "../api/learn";
import LearnRoute from "../routes/akms/learn";

const getStatus = getLearnStatus as unknown as ReturnType<typeof vi.fn>;
const compileFn = compileLearningSource as unknown as ReturnType<typeof vi.fn>;

// ── Fixtures (mirroring live backend payloads) ────────────────────────────────

const AVAILABLE_STATUS: LearnStatus = {
  akms_learn_available: true,
  capabilities: ["deterministic_outline", "node_anthology", "markdown_export"],
  generation_modes: ["deterministic_outline", "node_anthology"],
  version: "0.1.0",
  plugin_api: "akms-learn-plugin/v1",
};

/**
 * Realistic compile response with ALL four new panel data sources populated:
 *   - provenance_summary + packet_path + cache_dir → ProvenanceJsonPanel
 *   - exports[0] = notebook with metadata (cell_count/executed) → NotebookMetaPanel
 *   - exports[1] = assessment.md with metadata (has_public/has_answer_key) → AssessmentPresencePanel
 *   - warnings[0] = generated_content category → GeneratedWarningsPanel
 *   - warnings[1] = source_quality (non-generated, stays only in flat list)
 */
const FULL_RESPONSE: LearnCompileResponse = {
  artifact_id: "abc123",
  markdown: "# Lesson Title\n\nSome **content** here.",
  warnings: [
    {
      severity: "info",
      code: "generated_content",
      message: "This lesson contains LLM-generated content. Review before use.",
      source_ref: null,
      category: "generated_content",
    },
    {
      severity: "warning",
      code: "missing_section",
      message: "Node is missing a teaching section.",
      source_ref: "node-42",
      category: "source_quality",
    },
  ],
  provenance_summary: {
    request_hash: "deadbeef01234567",
    graph_hash: "cafebabe89abcdef",
    topic: "Graph Algorithms",
    generation_option: "deterministic_outline",
    node_count: 5,
    edge_count: 4,
  },
  cache_dir: "/home/user/.cache/logic-loom/learning/abc123",
  packet_path: "/home/user/.cache/logic-loom/learning/abc123/deadbeef01234567.json",
  exports: [
    {
      path: "/home/user/.cache/logic-loom/learning/abc123/lesson.ipynb",
      type: "ipynb",
      metadata: {
        cell_count: 12,
        executed: false,
        generated_content: false,
      },
    },
    {
      path: "/home/user/.cache/logic-loom/learning/abc123/assessment.md",
      type: "md",
      metadata: {
        has_public: true,
        has_answer_key: true,
        generated_content: false,
      },
    },
    {
      path: "/home/user/.cache/logic-loom/learning/abc123/lesson.md",
      type: "md",
      metadata: {
        generated_content: false,
      },
    },
  ],
};

/**
 * Minimal response where all additive fields (artifact metadata, warning
 * categories) are absent — mimics
 * an older backend response or one with no notebook/assessment artifacts.
 */
const MINIMAL_RESPONSE: LearnCompileResponse = {
  artifact_id: "min1",
  markdown: "# Minimal",
  warnings: [
    {
      severity: "warning",
      code: "missing_section",
      message: "A warning without category.",
      source_ref: null,
      // category intentionally absent — tests tolerant degradation
    },
  ],
  provenance_summary: {
    request_hash: "aabbccdd",
    graph_hash: "11223344",
    topic: "Min Topic",
    generation_option: "node_anthology",
    node_count: 1,
    edge_count: 0,
  },
  cache_dir: "/cache/min1",
  packet_path: null,
  exports: [
    {
      path: "/cache/min1/lesson.md",
      type: "md",
      // metadata intentionally absent — tests tolerant degradation
    },
  ],
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

/** Fill the required compile form fields and click compile. */
async function fillAndCompile() {
  await waitFor(() => {
    expect(screen.getByTestId("learn-compile")).toBeTruthy();
  });
  fireEvent.change(screen.getByTestId("learn-topic"), {
    target: { value: "Graph Algorithms" },
  });
  fireEvent.change(screen.getByTestId("learn-goal"), {
    target: { value: "Understand traversal" },
  });
  fireEvent.change(screen.getByTestId("learn-node"), {
    target: { value: "node-1" },
  });
  const btn = screen.getByTestId("learn-compile") as HTMLButtonElement;
  await waitFor(() => expect(btn.disabled).toBe(false));
  fireEvent.click(btn);
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

beforeEach(() => {
  getStatus.mockReset();
  compileFn.mockReset();
  getStatus.mockResolvedValue(AVAILABLE_STATUS);
});

afterEach(() => {
  cleanup();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Learn tab — structured preview panels", () => {
  // ──────────────────────────────────────────────────────────────────────────
  // Case 1: each new panel renders its data from a realistic compile response
  // ──────────────────────────────────────────────────────────────────────────
  it("each preview panel renders its data: provenance JSON, notebook metadata, assessment public prompts, generated-section warnings", async () => {
    compileFn.mockResolvedValue(FULL_RESPONSE);
    renderLearn();

    await fillAndCompile();

    // ── (a) Provenance JSON panel ──────────────────────────────────────────
    // Panel must appear; the <pre> must contain JSON-stringified provenance.
    await waitFor(() => {
      expect(screen.getByTestId("learn-provenance-json")).toBeTruthy();
    });

    const jsonPre = screen.getByTestId("learn-provenance-json-pre");
    const jsonText = jsonPre.textContent ?? "";

    // Must contain key provenance fields.
    expect(jsonText).toContain("deadbeef01234567"); // request_hash
    expect(jsonText).toContain("cafebabe89abcdef"); // graph_hash
    expect(jsonText).toContain("Graph Algorithms");  // topic
    expect(jsonText).toContain("deterministic_outline"); // generation_option
    // packet_path and cache_dir must also be in the JSON.
    expect(jsonText).toContain("packet_path");
    expect(jsonText).toContain("cache_dir");
    // Must be valid JSON (parseable without throwing).
    expect(() => JSON.parse(jsonText)).not.toThrow();
    // Security: must NOT contain any HTML tags (no dangerouslySetInnerHTML).
    expect(jsonText).not.toMatch(/<[^>]+>/);

    // ── (b) Notebook metadata panel ───────────────────────────────────────
    // Panel appears because exports[0] has cell_count in metadata.
    expect(screen.getByTestId("learn-notebook-meta")).toBeTruthy();

    const cellCount = screen.getByTestId("learn-notebook-cell-count-0");
    expect(cellCount.textContent).toBe("12");

    const executed = screen.getByTestId("learn-notebook-executed-0");
    // executed = false → should NOT say "Yes"
    expect(executed.textContent).not.toContain("Yes");
    expect(executed.textContent).toContain("No");

    // ── (c) Assessment public prompts panel ───────────────────────────────
    // Panel appears because exports[1] has has_public/has_answer_key in metadata.
    expect(screen.getByTestId("learn-assessment-presence")).toBeTruthy();

    const hasPublic = screen.getByTestId("learn-assessment-has-public");
    expect(hasPublic.textContent).toContain("Present");

    const hasAnswerKey = screen.getByTestId("learn-assessment-has-answer-key");
    // has_answer_key=true → shows "Present (not shown)" to signal rubric exists but isn't rendered.
    expect(hasAnswerKey.textContent).toContain("Present");
    expect(hasAnswerKey.textContent).toContain("not shown");

    // CRITICAL: raw answer-key/rubric content must NEVER appear anywhere in the DOM.
    // The panel shows presence flags only, so "rubric" content text should be absent.
    // (We can't test actual rubric content not appearing since we never have it in
    // the fixture, but we CAN assert the panel label itself says "not shown".)

    // ── (d) Generated-section warnings panel ──────────────────────────────
    // Panel appears because warnings[0] has category === "generated_content".
    expect(screen.getByTestId("learn-generated-warnings")).toBeTruthy();

    const genWarningsList = screen.getByTestId("learn-generated-warnings-list");
    // The generated_content warning message appears in this panel.
    expect(genWarningsList.textContent).toContain("LLM-generated content");
    expect(genWarningsList.textContent).toContain("Review before use");

    // The non-generated warning (source_quality) must NOT appear in the generated panel.
    expect(genWarningsList.textContent).not.toContain("missing_section");
    expect(genWarningsList.textContent).not.toContain("teaching section");

    // The flat warnings panel still shows ALL warnings (both categories).
    const flatWarnings = screen.getByTestId("learn-warnings");
    expect(flatWarnings.textContent).toContain("LLM-generated content");
    expect(flatWarnings.textContent).toContain("teaching section");

    // ── Markdown preview still present (not broken by new panels) ─────────
    await waitFor(() => {
      expect(screen.getByTestId("learn-preview").textContent).toContain("Lesson Title");
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case 2: absent/empty metadata + category degrades gracefully
  // ──────────────────────────────────────────────────────────────────────────
  it("absent/empty metadata and category degrades gracefully — panels hide, no crash", async () => {
    compileFn.mockResolvedValue(MINIMAL_RESPONSE);
    renderLearn();

    await fillAndCompile();

    // Wait for the response to appear (provenance panel must be present
    // because provenance_summary is non-null even in minimal response).
    await waitFor(() => {
      expect(screen.getByTestId("learn-provenance-json")).toBeTruthy();
    });

    // ── NotebookMetaPanel: hidden when no export carries cell_count ────────
    // exports[0] has no metadata → no notebook panel.
    expect(screen.queryByTestId("learn-notebook-meta")).toBeNull();

    // ── AssessmentPresencePanel: hidden when no export carries assessment keys
    // exports[0] has no metadata → no assessment panel.
    expect(screen.queryByTestId("learn-assessment-presence")).toBeNull();

    // ── GeneratedWarningsPanel: hidden when no warning has category=generated_content
    // warnings[0] has no category field → panel must not appear.
    expect(screen.queryByTestId("learn-generated-warnings")).toBeNull();

    // ── ProvenanceJsonPanel: present (provenance_summary is not null) ──────
    // The JSON output must still be valid and not crash.
    const jsonPre = screen.getByTestId("learn-provenance-json-pre");
    const jsonText = jsonPre.textContent ?? "";
    expect(() => JSON.parse(jsonText)).not.toThrow();
    expect(jsonText).toContain("Min Topic");
    // packet_path is null → must appear as null in JSON, not crash.
    expect(jsonText).toContain('"packet_path": null');

    // ── Flat warnings panel: still shows the single warning ───────────────
    // Even without category, the warning appears in the flat list.
    expect(screen.getByTestId("learn-warnings").textContent).toContain(
      "A warning without category.",
    );

    // ── Markdown preview: intact ──────────────────────────────────────────
    await waitFor(() => {
      expect(screen.getByTestId("learn-preview").textContent).toContain("Minimal");
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case 3: ProvenanceJsonPanel always renders (provenance_summary is required)
  // ──────────────────────────────────────────────────────────────────────────
  it("ProvenanceJsonPanel renders an empty provenance_summary object as {} without crashing", async () => {
    // Edge case: response with an empty provenance_summary object.
    // provenance_summary is a REQUIRED LearnCompileResponse field, so the panel
    // always renders; an empty object {} simply serializes to "{}". Verify no
    // crash and sensible JSON output.
    const responseWithEmptyProv: LearnCompileResponse = {
      ...MINIMAL_RESPONSE,
      provenance_summary: {},
      exports: [],
      warnings: [],
    };
    compileFn.mockResolvedValue(responseWithEmptyProv);
    renderLearn();

    await fillAndCompile();

    // Panel appears (provenance_summary is truthy / non-null).
    await waitFor(() => {
      expect(screen.getByTestId("learn-provenance-json")).toBeTruthy();
    });

    const jsonPre = screen.getByTestId("learn-provenance-json-pre");
    expect(() => JSON.parse(jsonPre.textContent ?? "")).not.toThrow();

    // No notebook, assessment, or generated warnings panels (empty exports/warnings).
    expect(screen.queryByTestId("learn-notebook-meta")).toBeNull();
    expect(screen.queryByTestId("learn-assessment-presence")).toBeNull();
    expect(screen.queryByTestId("learn-generated-warnings")).toBeNull();

    // Markdown preview shows empty content without crashing.
    await waitFor(() => {
      expect(screen.getByTestId("learn-preview")).toBeTruthy();
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case 4: warnings with unknown category don't appear in generated panel
  // ──────────────────────────────────────────────────────────────────────────
  it("warnings with unknown/missing category don't appear in generated panel", async () => {
    const responseWithMixedWarnings: LearnCompileResponse = {
      ...MINIMAL_RESPONSE,
      warnings: [
        {
          severity: "warning",
          code: "some_future_code",
          message: "A warning with an unknown future category.",
          source_ref: null,
          category: "generic",
        },
        {
          severity: "warning",
          code: "another_code",
          message: "A warning with no category field at all.",
          source_ref: null,
          // category absent
        },
      ],
    };
    compileFn.mockResolvedValue(responseWithMixedWarnings);
    renderLearn();

    await fillAndCompile();

    // Wait for compile to settle.
    await waitFor(() => {
      expect(screen.getByTestId("learn-warnings")).toBeTruthy();
    });

    // Neither "generic" nor missing-category warnings trigger the generated panel.
    expect(screen.queryByTestId("learn-generated-warnings")).toBeNull();

    // Both appear in the flat warnings list.
    const flat = screen.getByTestId("learn-warnings");
    expect(flat.textContent).toContain("unknown future category");
    expect(flat.textContent).toContain("no category field");
  });
});
