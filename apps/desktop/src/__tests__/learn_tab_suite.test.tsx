// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Consolidated Learn-tab frontend suite.
 *
 * Five self-contained test cases:
 *   1. hidden/gated when unavailable   (reuses the learn_tab_visibility SectionSideNav idiom)
 *   2. visible when available          (reuses the learn_tab_visibility SectionSideNav idiom)
 *   3. mode selector baseline-only     (reuses the learn_tab_ui LearnRoute + capabilities idiom)
 *   4. compile renders preview+warnings (reuses the learn_tab_ui compile idiom)
 *   5. safe artifact paths             (GAP — new coverage: ExportArtifactList)
 */

// ── Mocks ────────────────────────────────────────────────────────────────────
// Partial-mock: stub network fns + learnArtifactUrl (avoids client.ts / Tauri runtime).
// learnArtifactUrl is a spy so Case 5 can assert WHAT path segment it receives.

vi.mock("../api/learn", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/learn")>();
  return {
    ...actual,
    getLearnStatus: vi.fn(),
    compileLearningSource: vi.fn(),
    // LearnTab sources the mode/exporter registry from /profiles +
    // /exporters. These cases exercise the degrade-gracefully fallback to
    // status.capabilities / generation_modes, so the discovery endpoints return
    // empty here (learn_mode_registry.test.tsx covers the dynamic-registry primary path).
    getLearnProfiles: vi.fn(async () => ({ profiles: [] })),
    getLearnExporters: vi.fn(async () => ({ exporters: [] })),
    // Faithful mock: mirrors real learnArtifactUrl scoping — base/api/learn/artifacts/{id}/{encodeURIComponent(seg)}.
    // This way href assertions test real namespace scoping, not a trivially-true tautology.
    learnArtifactUrl: vi.fn(async (id: string, seg: string) =>
      `https://test.local/api/learn/artifacts/${encodeURIComponent(id)}/${encodeURIComponent(seg)}`,
    ),
  };
});

import { getLearnStatus, compileLearningSource, learnArtifactUrl } from "../api/learn";
import type { LearnStatus, LearnCompileResponse } from "../api/learn";
import { SectionSideNav } from "../components/SectionSideNav";
import { LearnGate } from "../components/LearnGate";
import LearnRoute from "../routes/akms/learn";
import { ExportArtifactList } from "../routes/learn/ExportArtifactList";

const getStatus = getLearnStatus as unknown as ReturnType<typeof vi.fn>;
const compileFn = compileLearningSource as unknown as ReturnType<typeof vi.fn>;
const artifactUrlFn = learnArtifactUrl as unknown as ReturnType<typeof vi.fn>;

// ── Fixtures ─────────────────────────────────────────────────────────────────

const AVAILABLE_STATUS: LearnStatus = {
  akms_learn_available: true,
  capabilities: ["deterministic_outline", "node_anthology", "markdown_export"],
  version: "0.1.0",
  plugin_api: "akms-learn-plugin/v1",
};

const UNAVAILABLE_STATUS: LearnStatus = {
  akms_learn_available: false,
  reason: "Package akms_learn is not installed.",
  capabilities: [],
};

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
}

// ── Render helpers ────────────────────────────────────────────────────────────

function renderSideNav(status: LearnStatus) {
  getStatus.mockResolvedValue(status);
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter initialEntries={["/akms"]}>
        <SectionSideNav mode="akms" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderLearnGate(status: LearnStatus) {
  getStatus.mockResolvedValue(status);
  return render(
    <QueryClientProvider client={makeQC()}>
      <LearnGate>
        <div data-testid="learn-content">Learn content</div>
      </LearnGate>
    </QueryClientProvider>,
  );
}

function renderLearn(initialState?: unknown) {
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter initialEntries={[{ pathname: "/akms/learn", state: initialState }]}>
        <LearnRoute />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderExportArtifactList(
  artifactId: string,
  exports: { path: string; type: string }[],
) {
  return render(
    <MemoryRouter>
      <ExportArtifactList artifactId={artifactId} exports={exports} />
    </MemoryRouter>,
  );
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

beforeEach(() => {
  getStatus.mockReset();
  compileFn.mockReset();
  artifactUrlFn.mockReset();
  // Default: faithful mock mirrors real learnArtifactUrl scoping (base/api/learn/artifacts/{id}/{encodeURIComponent(seg)}).
  artifactUrlFn.mockImplementation(
    async (id: string, seg: string) =>
      `https://test.local/api/learn/artifacts/${encodeURIComponent(id)}/${encodeURIComponent(seg)}`,
  );
});

afterEach(() => {
  cleanup();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Learn tab — consolidated frontend suite", () => {
  // ──────────────────────────────────────────────────────────────────────────
  // Case 1: hidden/gated when unavailable
  // Consolidated from learn_tab_visibility.test.tsx "hidden/gated when unavailable".
  // Additional: LearnGate shows gated placeholder — not just SectionSideNav.
  // ──────────────────────────────────────────────────────────────────────────
  it("hidden/gated when unavailable", async () => {
    // 1a. SectionSideNav: "Learn" nav item must NOT appear when unavailable.
    renderSideNav(UNAVAILABLE_STATUS);
    await waitFor(() => {
      // Other static AKMS nav items appear immediately.
      expect(screen.getByText("Graph")).toBeTruthy();
    });
    expect(screen.queryByText("Learn")).toBeNull();

    cleanup();

    // 1b. LearnGate: gate placeholder must appear; children must NOT render.
    renderLearnGate(UNAVAILABLE_STATUS);
    await waitFor(() => {
      expect(screen.getByText("Learn not available")).toBeTruthy();
    });
    expect(screen.queryByTestId("learn-content")).toBeNull();
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case 2: visible when available
  // Consolidated from learn_tab_visibility.test.tsx "visible when available".
  // Additional: LearnGate renders children when available.
  // ──────────────────────────────────────────────────────────────────────────
  it("visible when available", async () => {
    // 2a. SectionSideNav: "Learn" NavLink appears pointing at /akms/learn.
    renderSideNav(AVAILABLE_STATUS);
    await waitFor(() => {
      expect(screen.getByText("Learn")).toBeTruthy();
    });
    const link = screen.getByText("Learn").closest("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/akms/learn");

    cleanup();

    // 2b. LearnGate: children render; gate placeholder must NOT appear.
    renderLearnGate(AVAILABLE_STATUS);
    await waitFor(() => {
      expect(screen.getByTestId("learn-content")).toBeTruthy();
    });
    expect(screen.queryByText("Learn not available")).toBeNull();
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case 3: mode selector baseline-only (capability-derived, not hard-coded)
  // Consolidated from learn_tab_ui.test.tsx "mode selector shows only available caps".
  // Assertion: omitting "pitfall_driven" makes it absent; *_export tokens are NOT modes.
  // ──────────────────────────────────────────────────────────────────────────
  it("mode selector shows only available capabilities", async () => {
    // Deliberately omit `pitfall_driven`; include two exporters.
    const status: LearnStatus = {
      akms_learn_available: true,
      capabilities: [
        "deterministic_outline",
        "node_anthology",
        "markdown_export",
        "bundle_export",
      ],
      version: "0.1.0",
      plugin_api: "akms-learn-plugin/v1",
    };
    getStatus.mockResolvedValue(status);

    renderLearn();

    await waitFor(() => {
      expect(screen.getByTestId("learn-mode-selector")).toBeTruthy();
    });

    const selector = screen.getByTestId("learn-mode-selector");

    // Offers EXACTLY the two non-export caps (title-cased labels).
    expect(screen.getByTestId("learn-mode-deterministic_outline")).toBeTruthy();
    expect(screen.getByTestId("learn-mode-node_anthology")).toBeTruthy();
    expect(selector.textContent).toContain("Deterministic Outline");
    expect(selector.textContent).toContain("Node Anthology");

    // Does NOT offer the omitted baseline mode…
    expect(screen.queryByTestId("learn-mode-pitfall_driven")).toBeNull();
    // …nor any `*_export` token as a generation mode.
    expect(screen.queryByTestId("learn-mode-markdown_export")).toBeNull();
    expect(screen.queryByTestId("learn-mode-bundle_export")).toBeNull();
    expect(selector.textContent).not.toContain("Export");

    // Exporters appear in their own selector with `_export` stripped to bare ids.
    expect(screen.getByTestId("learn-exporter-markdown")).toBeTruthy();
    expect(screen.getByTestId("learn-exporter-bundle")).toBeTruthy();
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case 4: compile renders preview + warnings
  // Consolidated from learn_tab_ui.test.tsx "compile renders preview+warnings".
  // Adds: provenance structured fields and compile-request contract assertions.
  // ──────────────────────────────────────────────────────────────────────────
  it("compile renders preview and warnings", async () => {
    getStatus.mockResolvedValue(AVAILABLE_STATUS);

    const response: LearnCompileResponse = {
      artifact_id: "a1",
      markdown: "# Hello",
      warnings: [
        {
          severity: "warning",
          code: "generated_content",
          message: "w1",
          source_ref: null,
        },
      ],
      provenance_summary: {
        topic: "T",
        generation_option: "deterministic_outline",
        node_count: 3,
        edge_count: 2,
        request_hash: "rh",
        graph_hash: "gh",
      },
      cache_dir: "/c",
      packet_path: "/p",
      exports: [{ path: "/c/a1/lesson.md", type: "markdown" }],
    };
    compileFn.mockResolvedValue(response);

    renderLearn();

    await waitFor(() => {
      expect(screen.getByTestId("learn-compile")).toBeTruthy();
    });

    // Fill required fields.
    fireEvent.change(screen.getByTestId("learn-topic"), {
      target: { value: "Graphs" },
    });
    fireEvent.change(screen.getByTestId("learn-goal"), {
      target: { value: "Understand ego slices" },
    });
    fireEvent.change(screen.getByTestId("learn-node"), {
      target: { value: "node-123" },
    });

    // Compile button should now be enabled.
    const button = screen.getByTestId("learn-compile") as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);

    // Preview shows rendered markdown; warnings panel shows the warning.
    await waitFor(() => {
      expect(screen.getByTestId("learn-preview").textContent).toContain("Hello");
    });
    expect(screen.getByTestId("learn-warnings").textContent).toContain("w1");

    // Provenance panel surfaces structured fields.
    expect(screen.getByTestId("learn-provenance").textContent).toContain(
      "deterministic_outline",
    );
    expect(screen.getByTestId("learn-provenance").textContent).toContain("3");

    // Export list shows artifact path text.
    expect(screen.getByTestId("learn-export-list").textContent).toContain(
      "/c/a1/lesson.md",
    );

    // Compile request carried required topic/goal/node.
    expect(compileFn).toHaveBeenCalledTimes(1);
    const sentReq = compileFn.mock.calls[0][0];
    expect(sentReq).toMatchObject({
      topic: "Graphs",
      goal: "Understand ego slices",
      node: "node-123",
      generation_option: "deterministic_outline",
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Case 5: safe artifact paths (GAP — primary new coverage)
  //
  // Pins ExportArtifactList's flat-layout basename() contract:
  //   • Absolute paths → learnArtifactUrl called with basename only (not raw abs path).
  //   • Traversal paths (e.g. /abs/../evil) → basename reduces to last segment ("evil").
  //   • Hostile-looking paths (javascript:-style) → basename strips everything; href
  //     is scoped under /api/learn/artifacts/<id>/... — never escapes artifact namespace.
  //   • Component NEVER crashes regardless of path content.
  //   • Raw path text IS rendered (for human readability); href is the safe URL.
  //   • A path that causes learnArtifactUrl to reject → falls back to plain text
  //     (no href rendered), component still does not crash.
  // ──────────────────────────────────────────────────────────────────────────
  it("safe artifact paths render without path injection", async () => {
    const ARTIFACT_ID = "a1";

    // ── Sub-case A: absolute path ─────────────────────────────────────────
    // /abs/cache/learning/a1/lesson.md → basename = "lesson.md"
    // learnArtifactUrl must be called with ("a1", "lesson.md"), NOT the raw absolute path.
    {
      const { unmount } = renderExportArtifactList(ARTIFACT_ID, [
        { path: "/abs/cache/learning/a1/lesson.md", type: "markdown" },
      ]);

      // Raw path text is always displayed (human-readable).
      await waitFor(() => {
        expect(screen.getByTestId("learn-export-list").textContent).toContain(
          "/abs/cache/learning/a1/lesson.md",
        );
      });

      // learnArtifactUrl is called with the BASENAME, not the absolute path.
      await waitFor(() => {
        expect(artifactUrlFn).toHaveBeenCalledWith(ARTIFACT_ID, "lesson.md");
      });
      expect(artifactUrlFn).not.toHaveBeenCalledWith(
        ARTIFACT_ID,
        "/abs/cache/learning/a1/lesson.md",
      );

      // The rendered href is the faithful mock URL (basename-scoped, not raw absolute).
      // findByRole is unconditional — if the link never renders, the test fails here.
      const link = await screen.findByRole("link");
      const href = link.getAttribute("href") ?? "";
      expect(href).toBe(
        `https://test.local/api/learn/artifacts/${encodeURIComponent(ARTIFACT_ID)}/${encodeURIComponent("lesson.md")}`,
      );
      // Regression probes: if the component wrote artifact.path into href instead of
      // calling learnArtifactUrl, these would all fail.
      expect(href).not.toBe("/abs/cache/learning/a1/lesson.md");
      expect(href).not.toContain("/abs/");
      expect(href).not.toContain("../");

      unmount();
      artifactUrlFn.mockReset();
      artifactUrlFn.mockImplementation(
        async (id: string, seg: string) =>
          `https://test.local/api/learn/artifacts/${encodeURIComponent(id)}/${encodeURIComponent(seg)}`,
      );
    }

    // ── Sub-case B: traversal path ────────────────────────────────────────
    // /safe/../../evil/payload.md → basename = "payload.md" (last non-empty segment).
    // The href must be scoped to the artifact id + basename, not traverse upward.
    {
      const RAW_TRAVERSAL = "/safe/../../evil/payload.md";

      const { unmount } = renderExportArtifactList(ARTIFACT_ID, [
        { path: RAW_TRAVERSAL, type: "bundle" },
      ]);

      // learnArtifactUrl must be called with the reduced basename, not the traversal path.
      await waitFor(() => {
        expect(artifactUrlFn).toHaveBeenCalledWith(ARTIFACT_ID, "payload.md");
      });
      expect(artifactUrlFn).not.toHaveBeenCalledWith(ARTIFACT_ID, RAW_TRAVERSAL);

      // findByRole is UNCONDITIONAL — if the link never renders the test fails here,
      // preventing silent pass-through when async timing hides missing assertions.
      const link = await screen.findByRole("link");
      const href = link.getAttribute("href") ?? "";

      // Href is the faithful mock URL scoped to basename — traversal sequences stripped.
      expect(href).toBe(
        `https://test.local/api/learn/artifacts/${encodeURIComponent(ARTIFACT_ID)}/${encodeURIComponent("payload.md")}`,
      );
      // Regression probes: if the component bypassed learnArtifactUrl and wrote
      // artifact.path into href, these assertions would catch it.
      expect(href).not.toBe(RAW_TRAVERSAL);
      expect(href).not.toContain("..");
      expect(href).not.toContain("/safe/");
      expect(href).not.toContain("/evil/");

      unmount();
      artifactUrlFn.mockReset();
      artifactUrlFn.mockImplementation(
        async (id: string, seg: string) =>
          `https://test.local/api/learn/artifacts/${encodeURIComponent(id)}/${encodeURIComponent(seg)}`,
      );
    }

    // ── Sub-case C: hostile-looking segment (javascript:-style) ─────────
    // A path whose last segment looks like a script injection attempt.
    // basename() reduces it to that last segment; learnArtifactUrl encodes it.
    // The component must NOT crash and the href must stay artifact-namespace-scoped.
    {
      const hostilePath = "/some/dir/javascript:alert(1)";
      const hostileBasename = "javascript:alert(1)";
      const { unmount } = renderExportArtifactList(ARTIFACT_ID, [
        { path: hostilePath, type: "bundle" },
      ]);

      // Component renders without crashing — raw text appears.
      await waitFor(() => {
        expect(screen.getByTestId("learn-export-list").textContent).toContain(
          hostilePath,
        );
      });

      // learnArtifactUrl is called with the basename segment (the hostile segment).
      await waitFor(() => {
        expect(artifactUrlFn).toHaveBeenCalledWith(ARTIFACT_ID, hostileBasename);
      });
      // Must NOT be called with the full raw path.
      expect(artifactUrlFn).not.toHaveBeenCalledWith(ARTIFACT_ID, hostilePath);

      // findByRole is UNCONDITIONAL — no if-guard; if the link is absent, the test fails.
      // The faithful mock encodes the segment, so the rendered href is artifact-scoped.
      const link = await screen.findByRole("link");
      const href = link.getAttribute("href") ?? "";

      // Href is scoped under /api/learn/artifacts/{id}/... — never a bare javascript: URL.
      expect(href).toBe(
        `https://test.local/api/learn/artifacts/${encodeURIComponent(ARTIFACT_ID)}/${encodeURIComponent(hostileBasename)}`,
      );
      // Regression probes: these would fire if the component wrote artifact.path into href,
      // or if learnArtifactUrl's encoding was bypassed.
      expect(href).not.toBe(hostilePath);
      expect(href).not.toBe(hostileBasename);
      // The encoded href starts with https://test.local — NOT with "javascript:".
      // (If the component bypassed the URL builder and set href=hostileBasename directly,
      // startsWith("javascript:") would be true and this assertion would catch it.)
      expect(href.startsWith("javascript:")).toBe(false);
      expect(href).toContain("/api/learn/artifacts/");

      unmount();
      artifactUrlFn.mockReset();
    }

    // ── Sub-case D: learnArtifactUrl rejects → plain text fallback ───────
    // When the URL resolver throws, the item renders as a plain <span> (no <a>),
    // the component does NOT crash, and the raw path text is still visible.
    {
      artifactUrlFn.mockRejectedValue(new Error("API base unavailable"));

      const { unmount } = renderExportArtifactList(ARTIFACT_ID, [
        { path: "/abs/cache/a1/lesson.md", type: "markdown" },
      ]);

      // Raw path text is shown (fallback span).
      await waitFor(() => {
        expect(screen.getByTestId("learn-export-list").textContent).toContain(
          "/abs/cache/a1/lesson.md",
        );
      });

      // No <a> link rendered when URL resolution fails.
      expect(screen.queryByRole("link")).toBeNull();

      unmount();
    }
  });
});
