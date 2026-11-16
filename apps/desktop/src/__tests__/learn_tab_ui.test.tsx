// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Minimal Learn tab UI (capability-driven).
 *
 * Covers:
 *   1. Mode selector exposes ONLY the baseline capabilities derived from backend
 *      status (proves capability-derived, not hard-coded).
 *   2. Compile renders preview + warnings; artifact list shows exported artifacts.
 */

// ── Mocks ────────────────────────────────────────────────────────────────────
// Partial-mock the learn client: stub the two network fns and the (async)
// artifact-URL builder so the tab never reaches into client.ts / Tauri runtime.
vi.mock("../api/learn", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/learn")>();
  return {
    ...actual,
    getLearnStatus: vi.fn(),
    compileLearningSource: vi.fn(),
    learnArtifactUrl: vi.fn(async (id: string, path: string) => `mock://${id}/${path}`),
  };
});

import { getLearnStatus, compileLearningSource } from "../api/learn";
import type { LearnStatus, LearnCompileResponse } from "../api/learn";
import LearnRoute from "../routes/akms/learn";

const getStatus = getLearnStatus as unknown as ReturnType<typeof vi.fn>;
const compileFn = compileLearningSource as unknown as ReturnType<typeof vi.fn>;

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
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

beforeEach(() => {
  getStatus.mockReset();
  compileFn.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("Minimal Learn tab UI", () => {
  // Mode selector exposes only the baseline capabilities (driven by status).
  it("mode selector shows only available caps", async () => {
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

    // Wait for the status-driven render to settle.
    await waitFor(() => {
      expect(screen.getByTestId("learn-mode-selector")).toBeTruthy();
    });

    const selector = screen.getByTestId("learn-mode-selector");

    // Offers EXACTLY the two non-export caps (title-cased labels).
    expect(screen.getByTestId("learn-mode-deterministic_outline")).toBeTruthy();
    expect(screen.getByTestId("learn-mode-node_anthology")).toBeTruthy();
    expect(selector.textContent).toContain("Deterministic Outline");
    expect(selector.textContent).toContain("Node Anthology");

    // Does NOT offer the omitted baseline mode...
    expect(screen.queryByTestId("learn-mode-pitfall_driven")).toBeNull();
    // ...nor any `*_export` token as a generation mode.
    expect(screen.queryByTestId("learn-mode-markdown_export")).toBeNull();
    expect(screen.queryByTestId("learn-mode-bundle_export")).toBeNull();
    expect(selector.textContent).not.toContain("Export");

    // Exporters appear in their own selector with `_export` stripped to bare ids.
    expect(screen.getByTestId("learn-exporter-markdown")).toBeTruthy();
    expect(screen.getByTestId("learn-exporter-bundle")).toBeTruthy();
  });

  // Compile renders preview + warnings; artifact list shows exported artifacts.
  it("compile renders preview+warnings", async () => {
    const status: LearnStatus = {
      akms_learn_available: true,
      capabilities: ["deterministic_outline", "node_anthology", "markdown_export"],
      version: "0.1.0",
      plugin_api: "akms-learn-plugin/v1",
    };
    getStatus.mockResolvedValue(status);

    const response: LearnCompileResponse = {
      artifact_id: "a1",
      markdown: "# Hello",
      warnings: [
        { severity: "warning", code: "generated_content", message: "w1", source_ref: null },
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

    // Fill the required fields (topic / goal / node).
    fireEvent.change(screen.getByTestId("learn-topic"), {
      target: { value: "Graphs" },
    });
    fireEvent.change(screen.getByTestId("learn-goal"), {
      target: { value: "Understand ego slices" },
    });
    fireEvent.change(screen.getByTestId("learn-node"), {
      target: { value: "node-123" },
    });

    // Compile.
    const button = screen.getByTestId("learn-compile") as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);

    // Preview shows the rendered markdown; warnings panel shows the warning message.
    await waitFor(() => {
      expect(screen.getByTestId("learn-preview").textContent).toContain("Hello");
    });
    expect(screen.getByTestId("learn-warnings").textContent).toContain("w1");

    // Provenance panel surfaces real structured fields; exported artifact path shows.
    expect(screen.getByTestId("learn-provenance").textContent).toContain(
      "deterministic_outline",
    );
    expect(screen.getByTestId("learn-provenance").textContent).toContain("3");
    expect(screen.getByTestId("learn-export-list").textContent).toContain(
      "/c/a1/lesson.md",
    );

    // The compile request carried the required topic/goal/node fields.
    expect(compileFn).toHaveBeenCalledTimes(1);
    const sentReq = compileFn.mock.calls[0][0];
    expect(sentReq).toMatchObject({
      topic: "Graphs",
      goal: "Understand ego slices",
      node: "node-123",
      generation_option: "deterministic_outline",
    });
  });
});
