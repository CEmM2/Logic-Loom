// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Learn tab mode-specific forms for all 8 mapped modes.
 *
 * Contract: each capability has its mapped UI element with conservative defaults.
 *
 * UI mode mapping:
 *   pedagogical_template → mini-textbook card (granularity + layout options)
 *   derivation_first     → derivation path toggle
 *   implementation_first → code-first / concept-first selector
 *   multi_granularity    → overview / standard / deep-dive selector
 *   notebook_source      → notebook export + no-execute checkbox
 *   assessment_first     → public/hidden assessment panel
 *   llm_expanded         → source-lock policy selector + generated-content warnings
 *   adaptive_path        → learner profile form + conservative-mode toggle
 *
 * Conservative defaults (REQUIRED by spec):
 *   llm_expanded  — source-lock policy = "source_locked" (real akms_learn value)
 *   adaptive_path — conservative-mode toggle = ON
 *   notebook_source — no-execute checkbox = ON
 *
 * These tests render the real LearnTab (via LearnRoute) against realistic
 * /profiles fixtures. The wireable mode options (granularity / policy /
 * learner_profile / include_code_links) now flow into the COMPILE REQUEST — see
 * the "wires options into the compile request" describe block below, which is
 * the whole point of the wiring. The remaining mapped controls (layout, notebook
 * no-execute, assessment sections, derivation show-full) are display-only and
 * carry an honest inline note — they are deliberately NOT sent to the compiler.
 */

// ── Mocks ──────────────────────────────────────────────────────────────────
vi.mock("../api/learn", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/learn")>();
  return {
    ...actual,
    getLearnStatus: vi.fn(),
    getLearnProfiles: vi.fn(),
    getLearnExporters: vi.fn(),
    compileLearningSource: vi.fn(),
    learnArtifactUrl: vi.fn(
      async (id: string, seg: string) =>
        `https://test.local/api/learn/artifacts/${encodeURIComponent(id)}/${encodeURIComponent(seg)}`,
    ),
  };
});

import {
  compileLearningSource,
  getLearnExporters,
  getLearnProfiles,
  getLearnStatus,
} from "../api/learn";
import type { LearnExporter, LearnProfile, LearnStatus } from "../api/learn";
import LearnRoute from "../routes/akms/learn";

const getStatus = getLearnStatus as unknown as ReturnType<typeof vi.fn>;
const getProfiles = getLearnProfiles as unknown as ReturnType<typeof vi.fn>;
const getExporters = getLearnExporters as unknown as ReturnType<typeof vi.fn>;
const compileFn = compileLearningSource as unknown as ReturnType<typeof vi.fn>;

// ── Fixtures ────────────────────────────────────────────────────────────────

const AVAILABLE_STATUS: LearnStatus = {
  akms_learn_available: true,
  capabilities: [
    "learning_source_packet",
    "deterministic_outline",
    "node_anthology",
    "pedagogical_template",
    "derivation_first",
    "implementation_first",
    "multi_granularity",
    "notebook_source",
    "assessment_first",
    "llm_expanded",
    "adaptive_path",
    "markdown_export",
    "bundle_export",
  ],
  generation_modes: [
    "deterministic_outline",
    "pedagogical_template",
    "derivation_first",
    "implementation_first",
    "multi_granularity",
    "notebook_source",
    "assessment_first",
    "llm_expanded",
    "adaptive_path",
  ],
  version: "0.1.0",
  plugin_api: "akms-learn-plugin/v1",
};

/**
 * Full mapped profiles: all 8 mapped modes available + 2 unmapped
 * available modes (deterministic_outline, node_anthology) as the initial
 * selection candidates, so the default doesn't land on a mapped mode.
 */
const ALL_MAPPED_PROFILES: LearnProfile[] = [
  { id: "deterministic_outline", label: "Deterministic Outline", status: "available" },
  { id: "node_anthology", label: "Node Anthology", status: "available" },
  { id: "pedagogical_template", label: "Pedagogical Template", status: "available" },
  { id: "derivation_first", label: "Derivation First", status: "available" },
  { id: "implementation_first", label: "Implementation First", status: "available" },
  { id: "multi_granularity", label: "Multi Granularity", status: "available" },
  { id: "notebook_source", label: "Notebook Source", status: "available" },
  { id: "assessment_first", label: "Assessment First", status: "available" },
  { id: "llm_expanded", label: "Llm Expanded", status: "available" },
  { id: "adaptive_path", label: "Adaptive Path", status: "available" },
];

const EXPORTERS: LearnExporter[] = [
  { id: "markdown_export", label: "Markdown", status: "available" },
  { id: "bundle_export", label: "Bundle", status: "available" },
];

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

/** Select a mode by clicking its radio button and wait for the panel to appear/change. */
async function selectMode(modeId: string) {
  const radio = await screen.findByTestId(`learn-mode-${modeId}`);
  fireEvent.click(radio);
  await waitFor(() => {
    expect((screen.getByTestId(`learn-mode-${modeId}`) as HTMLInputElement).checked).toBe(true);
  });
}

beforeEach(() => {
  getStatus.mockReset();
  getProfiles.mockReset();
  getExporters.mockReset();
  compileFn.mockReset();
  getStatus.mockResolvedValue(AVAILABLE_STATUS);
  getProfiles.mockResolvedValue({ profiles: ALL_MAPPED_PROFILES });
  getExporters.mockResolvedValue({ exporters: EXPORTERS });
});

afterEach(() => {
  cleanup();
});

// ── Test suite ────────────────────────────────────────────────────────────────

describe("Learn tab — mode-specific forms for all mapped modes", () => {
  /**
   * Case A: Each mapped mode renders its bespoke option panel when selected.
   * Also verifies unmapped modes (deterministic_outline, node_anthology) render
   * no panel, and that panel swaps correctly as the selection changes.
   */
  it("each mapped mode renders its option panel (mini-textbook / derivation / code-concept / granularity / notebook / assessment / source-lock / learner-profile)", async () => {
    renderLearn();

    // Wait for registry to settle (deterministic_outline is the first available mode).
    await waitFor(() => {
      expect(screen.getByTestId("learn-mode-deterministic_outline")).toBeTruthy();
    });

    // Default is deterministic_outline (first available) — no mapped panel.
    expect(screen.queryByTestId(/^learn-modeform-panel-/)).toBeNull();

    // --- pedagogical_template ---
    await selectMode("pedagogical_template");
    expect(screen.getByTestId("learn-modeform-pedagogical_template")).toBeTruthy();
    expect(screen.getByTestId("learn-modeform-panel-pedagogical_template")).toBeTruthy();
    // Previous panel is gone.
    expect(screen.queryByTestId("learn-modeform-panel-derivation_first")).toBeNull();

    // --- derivation_first ---
    await selectMode("derivation_first");
    expect(screen.getByTestId("learn-modeform-derivation_first")).toBeTruthy();
    expect(screen.getByTestId("learn-modeform-panel-derivation_first")).toBeTruthy();
    expect(screen.queryByTestId("learn-modeform-panel-pedagogical_template")).toBeNull();

    // --- implementation_first ---
    await selectMode("implementation_first");
    expect(screen.getByTestId("learn-modeform-implementation_first")).toBeTruthy();
    expect(screen.getByTestId("learn-modeform-panel-implementation_first")).toBeTruthy();

    // --- multi_granularity ---
    await selectMode("multi_granularity");
    expect(screen.getByTestId("learn-modeform-multi_granularity")).toBeTruthy();
    expect(screen.getByTestId("learn-modeform-panel-multi_granularity")).toBeTruthy();

    // --- notebook_source ---
    await selectMode("notebook_source");
    expect(screen.getByTestId("learn-modeform-notebook_source")).toBeTruthy();
    expect(screen.getByTestId("learn-modeform-panel-notebook_source")).toBeTruthy();

    // --- assessment_first ---
    await selectMode("assessment_first");
    expect(screen.getByTestId("learn-modeform-assessment_first")).toBeTruthy();
    expect(screen.getByTestId("learn-modeform-panel-assessment_first")).toBeTruthy();

    // --- llm_expanded ---
    await selectMode("llm_expanded");
    expect(screen.getByTestId("learn-modeform-llm_expanded")).toBeTruthy();
    expect(screen.getByTestId("learn-modeform-panel-llm_expanded")).toBeTruthy();

    // --- adaptive_path ---
    await selectMode("adaptive_path");
    expect(screen.getByTestId("learn-modeform-adaptive_path")).toBeTruthy();
    expect(screen.getByTestId("learn-modeform-panel-adaptive_path")).toBeTruthy();

    // --- unmapped mode (node_anthology) — no panel ---
    await selectMode("node_anthology");
    expect(screen.queryByTestId(/^learn-modeform-panel-/)).toBeNull();
  });

  /**
   * Case B: Conservative defaults are applied.
   *
   *   llm_expanded:   source-lock select = "locked" (conservative)
   *                   generated-content warning banner visible
   *   adaptive_path:  conservative-mode checkbox = ON (checked)
   *   notebook_source: no-execute checkbox = ON (checked)
   */
  it("conservative defaults applied (llm_expanded source-lock; adaptive_path conservative-mode; notebook no-execute)", async () => {
    renderLearn();

    await waitFor(() => {
      expect(screen.getByTestId("learn-mode-llm_expanded")).toBeTruthy();
    });

    // ── llm_expanded defaults ──────────────────────────────────────────────
    await selectMode("llm_expanded");

    // Source-lock policy defaults to "source_locked" (real akms_learn value;
    // conservative — anchors generated content to source nodes).
    const sourceLockSelect = screen.getByTestId(
      "learn-modeform-llm_expanded-source-lock",
    ) as HTMLSelectElement;
    expect(sourceLockSelect.value).toBe("source_locked");

    // Generated-content warning banner is always visible for this mode.
    expect(screen.getByTestId("learn-modeform-llm_expanded-warning")).toBeTruthy();
    expect(
      screen.getByTestId("learn-modeform-llm_expanded-warning").textContent,
    ).toMatch(/generated content/i);

    // ── adaptive_path defaults ─────────────────────────────────────────────
    await selectMode("adaptive_path");

    // Conservative-mode toggle defaults ON.
    const conservativeCheckbox = screen.getByTestId(
      "learn-modeform-adaptive_path-conservative-mode",
    ) as HTMLInputElement;
    expect(conservativeCheckbox.checked).toBe(true);

    // ── notebook_source defaults ───────────────────────────────────────────
    await selectMode("notebook_source");

    // No-execute checkbox defaults ON.
    const noExecuteCheckbox = screen.getByTestId(
      "learn-modeform-notebook_source-no-execute",
    ) as HTMLInputElement;
    expect(noExecuteCheckbox.checked).toBe(true);
  });
});

// ── Wiring suite ─────────────────────────────────────────────────────────────
//
// These tests are the WHOLE POINT of the wiring task: they assert the compile
// REQUEST carries the mode-specific options the panels collect, not just that
// the panels render. They render the real LearnTab, fill topic/goal/node, select
// a mode, optionally change a control, click compile, and inspect the payload
// passed to the mocked compileLearningSource.

/** Fill the required compile fields (topic/goal/node) and click compile. */
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

/** The single request object passed to the mocked compileLearningSource. */
function lastCompileRequest(): Record<string, unknown> {
  expect(compileFn).toHaveBeenCalledTimes(1);
  return compileFn.mock.calls[0][0] as Record<string, unknown>;
}

describe("Learn tab wiring — mode options flow into the compile request", () => {
  beforeEach(() => {
    // The compile mutation needs a resolved response to settle cleanly.
    compileFn.mockResolvedValue({
      artifact_id: "abc",
      markdown: "# ok",
      warnings: [],
      provenance_summary: {},
      cache_dir: null,
      packet_path: null,
      exports: [],
    });
  });

  it("multi_granularity → request carries granularity (deep_dive)", async () => {
    renderLearn();
    await selectMode("multi_granularity");

    // Change granularity from the conservative default (standard) to deep_dive.
    fireEvent.click(screen.getByTestId("learn-modeform-multi_granularity-deep_dive"));
    await fillAndCompile();

    const req = lastCompileRequest();
    expect(req.generation_option).toBe("multi_granularity");
    expect(req.granularity).toBe("deep_dive");
    // Fields for other modes are NOT included.
    expect(req).not.toHaveProperty("policy");
    expect(req).not.toHaveProperty("learner_profile");
    expect(req).not.toHaveProperty("include_code_links");
  });

  it("pedagogical_template → request carries granularity (changed value flows)", async () => {
    renderLearn();
    await selectMode("pedagogical_template");

    fireEvent.change(
      screen.getByTestId("learn-modeform-pedagogical_template-granularity"),
      { target: { value: "overview" } },
    );
    await fillAndCompile();

    const req = lastCompileRequest();
    expect(req.generation_option).toBe("pedagogical_template");
    expect(req.granularity).toBe("overview");
  });

  it("llm_expanded → request carries policy = source_locked by default; changed value flows", async () => {
    // First compile: conservative default policy.
    renderLearn();
    await selectMode("llm_expanded");
    await fillAndCompile();

    let req = lastCompileRequest();
    expect(req.generation_option).toBe("llm_expanded");
    expect(req.policy).toBe("source_locked");
    expect(req).not.toHaveProperty("granularity");

    // Second compile (fresh render): a changed policy flows through.
    cleanup();
    compileFn.mockReset();
    compileFn.mockResolvedValue({
      artifact_id: "abc",
      markdown: "# ok",
      warnings: [],
      provenance_summary: {},
      cache_dir: null,
      packet_path: null,
      exports: [],
    });
    renderLearn();
    await selectMode("llm_expanded");
    fireEvent.change(
      screen.getByTestId("learn-modeform-llm_expanded-source-lock"),
      { target: { value: "no_new_claims" } },
    );
    await fillAndCompile();

    req = lastCompileRequest();
    expect(req.policy).toBe("no_new_claims");
  });

  it("adaptive_path → request carries learner_profile with conservative_mode = true by default", async () => {
    renderLearn();
    await selectMode("adaptive_path");

    // Provide a 'knows' list to prove the form feeds the profile, leaving the
    // conservative_mode toggle at its default ON.
    fireEvent.change(screen.getByTestId("learn-modeform-adaptive_path-knows"), {
      target: { value: "node-a, node-b" },
    });
    await fillAndCompile();

    const req = lastCompileRequest();
    expect(req.generation_option).toBe("adaptive_path");
    expect(req.learner_profile).toEqual({
      knows: ["node-a", "node-b"],
      weak: [],
      goals: [],
      conservative_mode: true,
    });
  });

  it("implementation_first → concept-first sends include_code_links = false (code-first ⇒ true)", async () => {
    renderLearn();
    await selectMode("implementation_first");

    // Default is code-first (include_code_links=true); switch to concept-first.
    fireEvent.click(
      screen.getByTestId("learn-modeform-implementation_first-concept-first"),
    );
    await fillAndCompile();

    const req = lastCompileRequest();
    expect(req.generation_option).toBe("implementation_first");
    expect(req.include_code_links).toBe(false);
  });

  it("implementation_first default (code-first) sends include_code_links = true", async () => {
    renderLearn();
    await selectMode("implementation_first");
    await fillAndCompile();

    const req = lastCompileRequest();
    expect(req.include_code_links).toBe(true);
  });

  it("deterministic_outline (no options) → request carries none of the mode fields", async () => {
    renderLearn();
    // deterministic_outline is the first available mode (default selection); be
    // explicit so the test is robust to fixture ordering.
    await selectMode("deterministic_outline");
    await fillAndCompile();

    const req = lastCompileRequest();
    expect(req.generation_option).toBe("deterministic_outline");
    expect(req).not.toHaveProperty("granularity");
    expect(req).not.toHaveProperty("policy");
    expect(req).not.toHaveProperty("learner_profile");
    expect(req).not.toHaveProperty("include_code_links");
  });
});
