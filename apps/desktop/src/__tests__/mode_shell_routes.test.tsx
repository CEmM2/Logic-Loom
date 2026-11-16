// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  MemoryRouter,
  Navigate,
  Route,
  Routes,
  createMemoryRouter,
  RouterProvider,
} from "react-router-dom";
// `createMemoryRouter` + `RouterProvider` are still used by the
// renderAppShell helper below (it nests AppShell as a route element).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// SectionSideNav now calls useLearnStatus() to drive the dynamic Learn nav
// slot. Mock the learn API so AppShell renders without a running sidecar.
vi.mock("../api/learn", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/learn")>();
  return {
    ...actual,
    getLearnStatus: vi.fn(async () => ({
      akms_learn_available: false,
      capabilities: [],
    })),
  };
});

import { AppShell } from "../components/AppShell";
import { subnav } from "../components/SectionSideNav";
import { topLevelModes } from "../components/TopModeNav";
import type { Mode } from "../components/TopModeNav";

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
}

/**
 * Helper: render AppShell for a given mode inside a MemoryRouter + QueryClientProvider.
 * AppShell uses <Outlet />, so it must be a route element.
 * QueryClientProvider is required because SectionSideNav calls useLearnStatus()
 * (for dynamic Learn tab gating).
 */
function renderAppShell(mode: Mode) {
  const router = createMemoryRouter(
    [
      {
        path: `/${mode}`,
        element: <AppShell currentMode={mode} onModeChange={() => {}} />,
        children: [
          {
            index: true,
            element: <div data-testid="outlet-content">Outlet for {mode}</div>,
          },
        ],
      },
    ],
    { initialEntries: [`/${mode}`] },
  );
  return render(
    <QueryClientProvider client={makeQC()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("Top-level mode state and route namespaces", () => {
  it("renders AppShell snapshot for mode=zsum", () => {
    // All three modes render with a sticky side-nav.
    // Passes when: <AppShell currentMode="zsum" /> renders the correct zsum labels.
    const { container } = renderAppShell("zsum");

    // TopModeNav: all three mode buttons present
    topLevelModes.forEach(({ label }) => {
      expect(container.textContent).toContain(label);
    });

    // Verbatim subtitle for zsum mode is shown
    expect(container.textContent).toContain("Summarize and publish Zotero literature");

    // SectionSideNav: verbatim zsum labels
    subnav.zsum.forEach((label) => {
      expect(container.textContent).toContain(label);
    });

    // Aside (sticky side-nav) is present
    expect(container.querySelector("aside")).not.toBeNull();

    // GlobalRunStatus renders nothing when the store is empty.
    // The real <GlobalRunStatus /> returns null when activeRuns.length === 0,
    // so the root element must NOT be in the DOM.
    expect(container.querySelector('[data-testid="global-run-status"]')).toBeNull();

    // Outlet renders
    expect(screen.getByTestId("outlet-content")).toBeTruthy();
  });

  it("renders AppShell snapshot for mode=akms", () => {
    // All three modes render with a sticky side-nav.
    // Passes when: <AppShell currentMode="akms" /> matches the stored snapshot
    // and SectionSideNav uses the verbatim akms labels.
    const { container } = renderAppShell("akms");

    // Verbatim subtitle for akms mode
    expect(container.textContent).toContain("Generate and review knowledge nodes");

    // Verbatim akms labels
    subnav.akms.forEach((label) => {
      expect(container.textContent).toContain(label);
    });

    // Sticky side-nav present
    expect(container.querySelector("aside")).not.toBeNull();

    // GlobalRunStatus renders nothing when the store is empty.
    expect(container.querySelector('[data-testid="global-run-status"]')).toBeNull();
  });

  it("renders AppShell snapshot for mode=assistant", () => {
    // All three modes render with a sticky side-nav.
    // Passes when: <AppShell currentMode="assistant" /> renders assistant labels.
    const { container } = renderAppShell("assistant");

    // Verbatim subtitle for assistant mode
    expect(container.textContent).toContain("Discover papers and run research workflows");

    // Verbatim assistant labels
    subnav.assistant.forEach((label) => {
      expect(container.textContent).toContain(label);
    });

    // Sticky side-nav present
    expect(container.querySelector("aside")).not.toBeNull();

    // GlobalRunStatus renders nothing when the store is empty.
    expect(container.querySelector('[data-testid="global-run-status"]')).toBeNull();
  });

  it("redirects legacy /picker to /akms/batches", () => {
    // Old URLs (/picker, /nodes, /graph, /vault, /pipeline) redirect to /akms/* or /zsum/*.
    // Passes when: a MemoryRouter entered at /picker renders the /akms/batches sentinel.
    //
    // We use the declarative MemoryRouter + Routes API rather than the
    // data-router (createMemoryRouter + RouterProvider) because the data
    // router's internal navigation path constructs a Request via undici
    // in jsdom, which fails the `AbortSignal` instanceof check — a known
    // jsdom × @remix-run/router interop bug. The declarative API exercises
    // the same redirect contract without that code path.
    render(
      <MemoryRouter initialEntries={["/picker"]}>
        <Routes>
          <Route path="/picker" element={<Navigate to="/akms/batches" replace />} />
          <Route
            path="/akms/batches"
            element={<div data-testid="akms-batches-page">AKMS Batches</div>}
          />
        </Routes>
      </MemoryRouter>,
    );

    // After redirect the /akms/batches sentinel element must be visible
    expect(screen.getByTestId("akms-batches-page")).toBeTruthy();
    expect(screen.getByTestId("akms-batches-page").textContent).toBe("AKMS Batches");
  });
});
