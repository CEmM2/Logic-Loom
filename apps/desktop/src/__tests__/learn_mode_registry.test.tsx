// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Learn tab dynamic mode registry driven by backend capabilities.
 *
 * Contract: modes shown are exactly those advertised by backend capabilities.
 *
 * Registry contract:
 *   - Registry sources modes from GET /api/learn/profiles and exporters from
 *     GET /api/learn/exporters — each {id, label, status} with
 *     status ∈ available | unavailable | planned.
 *   - unavailable/planned entries render DISABLED (not omitted, not submittable),
 *     so a user can never POST one and get 412 capability_unavailable.
 *   - The default selected mode is always an `available` one.
 *   - Supersedes the interim status.generation_modes partition + deriveModes fallback.
 *
 * These tests render the real LearnTab (via LearnRoute) against realistic
 * /profiles + /exporters payloads mirroring the live backend, asserting the
 * radios/checkboxes the registry actually produces — not the pure builder alone
 * (that is unit-tested in routes/learn/capabilities.test.ts).
 */

// ── Mocks ──────────────────────────────────────────────────────────────────
// Partial-mock the learn API client: stub the network discovery fns + compile,
// keep everything else (types, learnArtifactUrl) real.
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

// ── Fixtures (mirror live payloads) ──────────────────────────────────────────

const AVAILABLE_STATUS: LearnStatus = {
  akms_learn_available: true,
  // Flat capabilities list is the LEGACY fallback source — present but the
  // registry must prefer /profiles + /exporters over partitioning this.
  capabilities: [
    "learning_source_packet", // infra (would be a bogus default under the old partition)
    "deterministic_outline",
    "node_anthology",
    "markdown_export",
    "bundle_export",
  ],
  generation_modes: ["deterministic_outline", "node_anthology"],
  version: "0.1.0",
  plugin_api: "akms-learn-plugin/v1",
};

// /profiles payload: an unavailable + a planned mode appear AFTER the available
// ones — they must render disabled, and the default selection must skip them.
const PROFILES: LearnProfile[] = [
  { id: "deterministic_outline", label: "Deterministic Outline", status: "available" },
  { id: "node_anthology", label: "Node Anthology", status: "available" },
  { id: "llm_expanded", label: "Llm Expanded", status: "unavailable" },
  { id: "adaptive_path", label: "Adaptive Path", status: "planned" },
];

// /exporters payload: id is the FULL `*_export` token; bare id is used in the UI.
const EXPORTERS: LearnExporter[] = [
  { id: "markdown_export", label: "Markdown", status: "available" },
  { id: "bundle_export", label: "Bundle", status: "available" },
  { id: "notebook_export", label: "Notebook", status: "unavailable" },
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

beforeEach(() => {
  getStatus.mockReset();
  getProfiles.mockReset();
  getExporters.mockReset();
  compileFn.mockReset();
  getStatus.mockResolvedValue(AVAILABLE_STATUS);
});

afterEach(() => {
  cleanup();
});

describe("Learn tab — dynamic mode registry from backend capabilities", () => {
  it("registry reflects backend caps — modes come from /profiles, exporters from /exporters", async () => {
    getProfiles.mockResolvedValue({ profiles: PROFILES });
    getExporters.mockResolvedValue({ exporters: EXPORTERS });

    renderLearn();

    // Modes: exactly the four /profiles ids appear (available + disabled alike).
    // Wait on a DYNAMIC-ONLY id (`llm_expanded`) — it can ONLY come from
    // /profiles, never the status fallback, so its presence proves the dynamic
    // registry (not the legacy partition) drove the selector.
    await waitFor(() => {
      expect(screen.getByTestId("learn-mode-llm_expanded")).toBeTruthy();
    });
    expect(screen.getByTestId("learn-mode-deterministic_outline")).toBeTruthy();
    expect(screen.getByTestId("learn-mode-node_anthology")).toBeTruthy();
    expect(screen.getByTestId("learn-mode-adaptive_path")).toBeTruthy();

    // The infra capability from status.capabilities must NOT leak in as a mode —
    // the registry came from /profiles, not the flat-list partition (a past
    // bug leaked infra capabilities into the mode list).
    expect(screen.queryByTestId("learn-mode-learning_source_packet")).toBeNull();
    // …nor any `*_export` token as a mode.
    expect(screen.queryByTestId("learn-mode-markdown_export")).toBeNull();

    // Exporters: bare ids (suffix stripped) sourced from /exporters. `notebook`
    // is dynamic-only (not in the status fallback partition) — wait on it.
    await waitFor(() => {
      expect(screen.getByTestId("learn-exporter-notebook")).toBeTruthy();
    });
    expect(screen.getByTestId("learn-exporter-markdown")).toBeTruthy();
    expect(screen.getByTestId("learn-exporter-bundle")).toBeTruthy();

    // The default selected mode is the first AVAILABLE one (radio checked).
    const defaultRadio = screen.getByTestId(
      "learn-mode-deterministic_outline",
    ) as HTMLInputElement;
    await waitFor(() => expect(defaultRadio.checked).toBe(true));
  });

  it("no selectable modes for absent caps; unavailable/planned render disabled, not submittable", async () => {
    getProfiles.mockResolvedValue({ profiles: PROFILES });
    getExporters.mockResolvedValue({ exporters: EXPORTERS });

    renderLearn();

    await waitFor(() => {
      expect(screen.getByTestId("learn-mode-llm_expanded")).toBeTruthy();
    });

    // Unavailable / planned modes render DISABLED (visible but not selectable),
    // so a user can never submit one and get 412 capability_unavailable.
    const unavailableMode = screen.getByTestId(
      "learn-mode-llm_expanded",
    ) as HTMLInputElement;
    const plannedMode = screen.getByTestId(
      "learn-mode-adaptive_path",
    ) as HTMLInputElement;
    expect(unavailableMode.disabled).toBe(true);
    expect(plannedMode.disabled).toBe(true);
    // Status badges surface WHY they're disabled.
    expect(screen.getByTestId("learn-mode-status-llm_expanded").textContent).toBe(
      "unavailable",
    );
    expect(screen.getByTestId("learn-mode-status-adaptive_path").textContent).toBe(
      "planned",
    );

    // The default selection is NOT one of the disabled modes.
    expect(unavailableMode.checked).toBe(false);
    expect(plannedMode.checked).toBe(false);

    // Unavailable exporter is disabled too (wait for the /exporters query).
    await waitFor(() => {
      expect(screen.getByTestId("learn-exporter-notebook")).toBeTruthy();
    });
    const unavailableExporter = screen.getByTestId(
      "learn-exporter-notebook",
    ) as HTMLInputElement;
    expect(unavailableExporter.disabled).toBe(true);
    // …and it is NOT pre-selected (only available exporters seed the selection).
    expect(unavailableExporter.checked).toBe(false);
    expect(
      (screen.getByTestId("learn-exporter-markdown") as HTMLInputElement).checked,
    ).toBe(true);
  });

  it("degrades gracefully — empty /profiles + /exporters fall back to status (no crash, no infra mode)", async () => {
    // Endpoint-empty path: the dynamic registry yields nothing, so the UI falls
    // back to status.generation_modes (NOT the lossy capabilities partition that
    // would surface the infra capability as the default mode).
    getProfiles.mockResolvedValue({ profiles: [] });
    getExporters.mockResolvedValue({ exporters: [] });

    renderLearn();

    await waitFor(() => {
      expect(screen.getByTestId("learn-mode-selector")).toBeTruthy();
    });

    // Fallback modes come from generation_modes — the infra string never appears.
    expect(screen.getByTestId("learn-mode-deterministic_outline")).toBeTruthy();
    expect(screen.getByTestId("learn-mode-node_anthology")).toBeTruthy();
    expect(screen.queryByTestId("learn-mode-learning_source_packet")).toBeNull();

    // Fallback exporters come from the capabilities _export partition.
    expect(screen.getByTestId("learn-exporter-markdown")).toBeTruthy();
    expect(screen.getByTestId("learn-exporter-bundle")).toBeTruthy();

    // Default selection is still an available mode (fallback entries are available).
    const defaultRadio = screen.getByTestId(
      "learn-mode-deterministic_outline",
    ) as HTMLInputElement;
    await waitFor(() => expect(defaultRadio.checked).toBe(true));
  });
});
