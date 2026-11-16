// @vitest-environment jsdom
import type React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// The Assistant Batches page calls the network on mount; stub the
// API client so the overview smoke-test never hits fetch. The render
// asserts only that the <h1> mounts, which happens before the query
// resolves.
vi.mock("../../api/assistantBatches", async () => ({
  listAssistantBatches: vi.fn().mockResolvedValue({ batches: [] }),
  getAssistantBatch: vi.fn().mockResolvedValue({}),
  createAssistantBatch: vi.fn(),
  patchAssistantBatch: vi.fn(),
  archiveAssistantBatch: vi.fn(),
  duplicateAssistantBatch: vi.fn(),
  addAssistantBatchSources: vi.fn(),
  removeAssistantBatchSources: vi.fn(),
  sendAssistantBatchToAkms: vi.fn(),
}));

import AssistantOverviewRoute from "../../routes/assistant/overview";
import AssistantDiscoverRoute from "../../routes/assistant/discover";
import AssistantBatchesRoute from "../../routes/assistant/batches";
import AssistantOutputsRoute from "../../routes/assistant/outputs";
import AssistantRunsRoute from "../../routes/assistant/runs";
import {
  ASSISTANT_ROUTE_BATCHES,
  ASSISTANT_ROUTE_DISCOVER,
  ASSISTANT_ROUTE_OUTPUTS,
  ASSISTANT_ROUTE_WORKFLOWS,
} from "../../routes/assistant/routes";

afterEach(() => cleanup());

/** Shows the current `useLocation().pathname` so assertions can read it. */
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="probe-path">{loc.pathname}</div>;
}

describe("Assistant sub-routes + overview content", () => {
  it("renders Overview at /assistant with all five quick-action labels", () => {
    render(
      <MemoryRouter initialEntries={["/assistant"]}>
        <Routes>
          <Route path="/assistant" element={<AssistantOverviewRoute />} />
        </Routes>
      </MemoryRouter>,
    );

    // The five quick-action labels are verbatim from the Overview copy.
    expect(screen.getByText("Create a source batch")).toBeTruthy();
    expect(screen.getByText("Search for papers")).toBeTruthy();
    expect(screen.getByText("Find related papers")).toBeTruthy();
    expect(screen.getByText("Run a workflow")).toBeTruthy();
    expect(screen.getByText("Open recent outputs")).toBeTruthy();
  });

  it("renders each of the seven other sub-routes without crashing", () => {
    // These are the plain route shells; we render each component
    // standalone and assert a non-null DOM tree is produced.
    const cases: Array<[string, () => React.ReactElement]> = [
      ["Discover", AssistantDiscoverRoute],
      ["Batches", AssistantBatchesRoute],
      // Workflows + Prompts render the shared AKMS library pages —
      // exercising them requires QueryClientProvider + heavy fixtures,
      // so this smoke-test covers only the Assistant-owned shells.
      // Settings is a live form that requires QueryClientProvider;
      // assistant_settings.test.tsx owns its smoke-test.
      ["Outputs", AssistantOutputsRoute],
      ["Runs", AssistantRunsRoute],
    ];

    for (const [headingText, Component] of cases) {
      // Wrap each shell in a QueryClientProvider — the Assistant
      // Batches page consumes useQueryClient/useQuery on mount.
      // The Overview / Discover / Outputs / Runs shells don't need it
      // but providing one is a no-op for them.
      const qc = new QueryClient({
        defaultOptions: { queries: { retry: 0, gcTime: 0 } },
      });
      const { container, unmount } = render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <Component />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      expect(container.firstChild).not.toBeNull();
      // Each shell has an <h1> matching the sub-tab name.
      expect(screen.getByRole("heading", { level: 1, name: headingText })).toBeTruthy();
      unmount();
    }
  });

  it("quick-action click navigates to the right sub-tab", () => {
    function Harness() {
      return (
        <MemoryRouter initialEntries={["/assistant"]}>
          <LocationProbe />
          <Routes>
            <Route path="/assistant" element={<AssistantOverviewRoute />} />
            <Route path="/assistant/discover" element={<div>discover-page</div>} />
            <Route path="/assistant/batches" element={<div>batches-page</div>} />
            <Route path="/assistant/workflows" element={<div>workflows-page</div>} />
            <Route path="/assistant/outputs" element={<div>outputs-page</div>} />
          </Routes>
        </MemoryRouter>
      );
    }

    const { getByText, getByTestId, unmount } = render(<Harness />);
    expect(getByTestId("probe-path").textContent).toBe("/assistant");

    // "Search for papers" → /assistant/discover
    fireEvent.click(getByText("Search for papers"));
    expect(getByTestId("probe-path").textContent).toBe(ASSISTANT_ROUTE_DISCOVER);

    unmount();

    // Each remaining mapping (one render per click to keep state clean).
    const cases: Array<[string, string]> = [
      ["Create a source batch", ASSISTANT_ROUTE_BATCHES],
      ["Find related papers", ASSISTANT_ROUTE_DISCOVER],
      ["Run a workflow", ASSISTANT_ROUTE_WORKFLOWS],
      ["Open recent outputs", ASSISTANT_ROUTE_OUTPUTS],
    ];

    for (const [label, target] of cases) {
      const { getByText: gbt, getByTestId: gbti, unmount: u } = render(<Harness />);
      fireEvent.click(gbt(label));
      expect(gbti("probe-path").textContent).toBe(target);
      u();
    }
  });
});
