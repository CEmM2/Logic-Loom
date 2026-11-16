// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Backend-status-driven Learn tab visibility.
 *
 * Covers:
 *   1. Tab visible only when akms_learn_available is true.
 *   2. No direct Python package inspection in frontend (driven by /api/learn/status only).
 */

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock("../api/learn", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/learn")>();
  return {
    ...actual,
    getLearnStatus: vi.fn(),
  };
});

import { getLearnStatus } from "../api/learn";
import type { LearnStatus } from "../api/learn";
import { SectionSideNav } from "../components/SectionSideNav";
import { LearnGate } from "../components/LearnGate";

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

function renderSideNav(status: LearnStatus) {
  (getLearnStatus as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(status);
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter initialEntries={["/akms"]}>
        <SectionSideNav mode="akms" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderLearnGate(status: LearnStatus) {
  (getLearnStatus as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(status);
  return render(
    <QueryClientProvider client={makeQC()}>
      <LearnGate>
        <div data-testid="learn-content">Learn content</div>
      </LearnGate>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  (getLearnStatus as unknown as ReturnType<typeof vi.fn>).mockReset();
});

afterEach(() => {
  cleanup();
});

describe("Learn tab visibility (backend-status-driven)", () => {
  // Tab visible only when akms_learn_available is true.
  it("hidden/gated when unavailable", async () => {
    renderSideNav(UNAVAILABLE_STATUS);

    // Wait for the query to settle — static labels appear immediately.
    await waitFor(() => {
      expect(screen.getByText("Graph")).toBeTruthy();
    });

    // "Learn" nav item must NOT appear when unavailable.
    expect(screen.queryByText("Learn")).toBeNull();
  });

  // Tab visible only when akms_learn_available is true.
  it("visible when available", async () => {
    renderSideNav(AVAILABLE_STATUS);

    // Wait for the async query to settle and "Learn" to appear.
    await waitFor(() => {
      expect(screen.getByText("Learn")).toBeTruthy();
    });

    // Must be a NavLink pointing at /akms/learn.
    const link = screen.getByText("Learn").closest("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/akms/learn");
  });

  // LearnGate: unavailable → gated placeholder text shown.
  it("LearnGate shows gated placeholder when unavailable", async () => {
    renderLearnGate(UNAVAILABLE_STATUS);

    await waitFor(() => {
      expect(screen.getByText("Learn not available")).toBeTruthy();
    });

    // The reason from the status response is displayed.
    expect(screen.getByText(/Package akms_learn is not installed/)).toBeTruthy();

    // Children must NOT be rendered.
    expect(screen.queryByTestId("learn-content")).toBeNull();
  });

  // LearnGate: available → children rendered.
  it("LearnGate renders children when available", async () => {
    renderLearnGate(AVAILABLE_STATUS);

    await waitFor(() => {
      expect(screen.getByTestId("learn-content")).toBeTruthy();
    });

    // Gated placeholder must NOT appear.
    expect(screen.queryByText("Learn not available")).toBeNull();
  });
});
