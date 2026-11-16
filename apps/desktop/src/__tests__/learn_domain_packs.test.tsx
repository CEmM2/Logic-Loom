// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Learn tab domain-pack UI (status card, source-pack table, badges).
 *
 * Covers:
 *   1. UI driven entirely by the backend `domain_packs[]` payload
 *      (GET /api/learn/status: {domain_id, pack_id, status, capabilities,
 *      source_packs[]}).
 *   2. No companion package imported; no compmech concept hard-coded; no buttons
 *      for unavailable executable adapters unless marked planned.
 *
 * GENERICITY PROOF (the trap is hard-coding one domain): the primary fixtures use NON-compmech, made-up ids
 * (`foo_domain`, `foo.reference`, `foo.kit`, …). The component renders them
 * correctly with zero domain knowledge — the same code that would render a
 * compmech reference pack. The capability NAMES (`static_markdown`,
 * `executable_notebook`) are payload KEYS the UI echoes, not an enum it knows.
 */

// ── Mocks ──────────────────────────────────────────────────────────────────
// Mirror the mode-registry suite's pattern: partial-mock the learn API client, keep types real.
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
import type { LearnDomainPack, LearnStatus } from "../api/learn";
import LearnRoute from "../routes/akms/learn";

const getStatus = getLearnStatus as unknown as ReturnType<typeof vi.fn>;
const getProfiles = getLearnProfiles as unknown as ReturnType<typeof vi.fn>;
const getExporters = getLearnExporters as unknown as ReturnType<typeof vi.fn>;
const compileFn = compileLearningSource as unknown as ReturnType<typeof vi.fn>;

// ── Fixtures ─────────────────────────────────────────────────────────────────
// Deliberately NON-compmech ids to prove the UI carries zero domain knowledge.
const FOO_PACK: LearnDomainPack = {
  domain_id: "foo_domain",
  pack_id: "foo.reference",
  status: "reference",
  capabilities: {
    static_markdown: "available",
    executable_notebook: "unavailable",
  },
  source_packs: [
    { source_pack_id: "foo.kit", role: "concept_kit", status: "planned" },
    { source_pack_id: "foo.bridge", role: "executable_bridge", status: "planned" },
  ],
};

// A second, structurally-different pack: a different (unknown) pack status, an
// all-available capability set (→ no static-only warning), and no source packs.
const BAR_PACK: LearnDomainPack = {
  domain_id: "bar_domain",
  pack_id: "bar.live",
  status: "experimental", // unknown status string → renders raw, no crash
  capabilities: {
    widget_export: "available",
  },
  source_packs: [],
};

function baseStatus(domainPacks: LearnDomainPack[]): LearnStatus {
  return {
    akms_learn_available: true,
    capabilities: ["deterministic_outline", "markdown_export"],
    generation_modes: ["deterministic_outline"],
    version: "0.1.0",
    plugin_api: "akms-learn-plugin/v1",
    domain_packs: domainPacks,
  };
}

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
  // Selectors aren't under test here — keep them empty so they degrade quietly.
  getProfiles.mockResolvedValue({ profiles: [] });
  getExporters.mockResolvedValue({ exporters: [] });
});

afterEach(() => {
  cleanup();
});

describe("Learn tab — domain-pack UI", () => {
  it("renders the domain-pack status card + source-pack table from status.domain_packs payload only (no hard-coded compmech)", async () => {
    getStatus.mockResolvedValue(baseStatus([FOO_PACK, BAR_PACK]));

    renderLearn();

    // Both packs render — keyed by their payload pack_id, generically.
    await waitFor(() => {
      expect(screen.getByTestId("domain-pack-card-foo.reference")).toBeTruthy();
    });
    expect(screen.getByTestId("domain-pack-card-bar.live")).toBeTruthy();

    // Pack-level fields come straight from the payload.
    expect(screen.getByTestId("domain-pack-id-foo.reference").textContent).toBe(
      "foo.reference",
    );
    expect(screen.getByTestId("domain-pack-domain-foo.reference").textContent).toBe(
      "foo_domain",
    );
    expect(screen.getByTestId("domain-pack-status-foo.reference").textContent).toContain(
      "reference",
    );

    // Source-pack table renders one row per payload source pack, with role+status.
    const table = screen.getByTestId("domain-pack-sources-foo.reference");
    const kitRow = within(table).getByTestId(
      "domain-pack-source-row-foo.reference-foo.kit",
    );
    expect(within(kitRow).getByText("concept_kit")).toBeTruthy();
    expect(
      screen.getByTestId("domain-pack-source-status-foo.reference-foo.kit").textContent,
    ).toBe("planned");
    expect(
      screen.getByTestId("domain-pack-source-row-foo.reference-foo.bridge"),
    ).toBeTruthy();

    // The second pack has no source packs → explicit empty state, not a crash.
    expect(screen.getByTestId("domain-pack-sources-empty-bar.live")).toBeTruthy();

    // An unknown pack-level status string renders verbatim (no crash, raw text).
    expect(screen.getByTestId("domain-pack-status-bar.live").textContent).toContain(
      "experimental",
    );
  });

  it("capability badges reflect per-capability status; NO action affordance for an unavailable executable adapter, but a planned one may render (non-interactive)", async () => {
    // A pack with one available, one unavailable, and one planned capability —
    // each drives badge + button purely by STATUS (never by capability name).
    const pack: LearnDomainPack = {
      domain_id: "foo_domain",
      pack_id: "foo.reference",
      status: "reference",
      capabilities: {
        static_markdown: "available",
        executable_notebook: "unavailable",
        executable_slides: "planned",
      },
      source_packs: [],
    };
    getStatus.mockResolvedValue(baseStatus([pack]));

    renderLearn();

    await waitFor(() => {
      expect(
        screen.getByTestId("domain-pack-cap-foo.reference-static_markdown"),
      ).toBeTruthy();
    });

    // Badges echo each capability's status from the payload.
    expect(
      screen
        .getByTestId("domain-pack-cap-foo.reference-static_markdown")
        .textContent,
    ).toContain("available");
    expect(
      screen
        .getByTestId("domain-pack-cap-foo.reference-executable_notebook")
        .textContent,
    ).toContain("unavailable");
    expect(
      screen
        .getByTestId("domain-pack-cap-foo.reference-executable_slides")
        .textContent,
    ).toContain("planned");

    // NO action button for the UNAVAILABLE capability (no buttons for
    // unavailable executable adapters unless planned).
    expect(
      screen.queryByTestId(
        "domain-pack-cap-action-foo.reference-executable_notebook",
      ),
    ).toBeNull();

    // The AVAILABLE executable adapter shows an availability affordance — but it
    // is a NON-interactive indicator, not a fake clickable "Use" action (there is
    // no backend invoke endpoint, so a real action would be dishonest).
    const availableAff = screen.getByTestId(
      "domain-pack-cap-action-foo.reference-static_markdown",
    );
    expect(availableAff.tagName).not.toBe("BUTTON");
    expect(availableAff.getAttribute("data-available")).toBe("true");

    // The PLANNED capability ALSO shows the affordance (NOT suppressed the way
    // unavailable is), marked as not-yet-available.
    const plannedAff = screen.getByTestId(
      "domain-pack-cap-action-foo.reference-executable_slides",
    );
    expect(plannedAff).toBeTruthy();
    expect(plannedAff.tagName).not.toBe("BUTTON");
    expect(plannedAff.getAttribute("data-available")).toBe("false");
  });

  it("shows a static-only export warning when a pack can only export statically; none when all capabilities are available", async () => {
    // FOO_PACK: static_markdown available + executable_notebook unavailable → warn.
    // BAR_PACK: single available capability → no warning.
    getStatus.mockResolvedValue(baseStatus([FOO_PACK, BAR_PACK]));

    renderLearn();

    await waitFor(() => {
      expect(
        screen.getByTestId("domain-pack-static-warning-foo.reference"),
      ).toBeTruthy();
    });

    // The warning names the not-available capability from the payload (generic).
    const warning = screen.getByTestId("domain-pack-static-warning-foo.reference");
    expect(warning.textContent).toContain("executable_notebook");
    expect(warning.textContent).toContain("unavailable");

    // The all-available pack has NO static-only warning.
    expect(
      screen.queryByTestId("domain-pack-static-warning-bar.live"),
    ).toBeNull();
  });
});
