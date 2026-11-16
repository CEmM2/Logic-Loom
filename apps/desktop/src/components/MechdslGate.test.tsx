// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryRouter } from "react-router-dom";

import { MechdslGate } from "./MechdslGate";
import { AppShell } from "./AppShell";
import { subnav } from "./SectionSideNav";
import { topLevelModes } from "./TopModeNav";
import type { Mode } from "./TopModeNav";

// Mock the status hook so the gate's branch is deterministic without a backend.
const mockUseMechdslStatus = vi.fn();
vi.mock("../hooks/mechdsl", () => ({
  useMechdslStatus: () => mockUseMechdslStatus(),
}));

// Testing-library auto-cleanup is not globally registered in this repo's vitest
// config, so unmount between tests to avoid DOM leaking across renders.
afterEach(cleanup);

describe("MechdslGate", () => {
  it("blocks the subtree and shows the install hint when unavailable", () => {
    mockUseMechdslStatus.mockReturnValue({
      data: {
        available: false,
        package_installed: false,
        mechdsl_installed: false,
        capabilities: null,
        reason: "compmech_reference_pack is not installed (install the 'mechdsl' extra)",
      },
      isLoading: false,
    });

    render(
      <MechdslGate>
        <div data-testid="gated">SECRET</div>
      </MechdslGate>,
    );

    expect(screen.queryByTestId("gated")).toBeNull();
    expect(screen.getByText(/MechDSL not available/)).toBeTruthy();
    // The dev-setup install command names the optional extra.
    expect(screen.getByText(/\[mechdsl\]/)).toBeTruthy();
  });

  it("renders children when the plugin is available", () => {
    mockUseMechdslStatus.mockReturnValue({
      data: {
        available: true,
        package_installed: true,
        mechdsl_installed: true,
        capabilities: { backends: ["taichi"] },
        reason: null,
      },
      isLoading: false,
    });

    render(
      <MechdslGate>
        <div data-testid="gated">SECRET</div>
      </MechdslGate>,
    );

    expect(screen.getByTestId("gated").textContent).toBe("SECRET");
  });

  it("shows a loading state while status resolves", () => {
    mockUseMechdslStatus.mockReturnValue({ data: undefined, isLoading: true });

    render(
      <MechdslGate>
        <div data-testid="gated">SECRET</div>
      </MechdslGate>,
    );

    expect(screen.getByText(/Checking MechDSL availability/)).toBeTruthy();
    expect(screen.queryByTestId("gated")).toBeNull();
  });
});

describe("MechDSL mode wiring", () => {
  it("registers the mechdsl mode in the top nav and side nav", () => {
    expect(topLevelModes.some((m) => m.id === "mechdsl")).toBe(true);
    expect(subnav.mechdsl).toEqual(["Overview", "Compile", "Artifacts"]);
  });

  it("renders the mechdsl section nav inside AppShell", () => {
    const mode: Mode = "mechdsl";
    const router = createMemoryRouter(
      [
        {
          path: `/${mode}`,
          element: <AppShell currentMode={mode} onModeChange={() => {}} />,
          children: [
            { index: true, element: <div data-testid="outlet">x</div> },
          ],
        },
      ],
      { initialEntries: [`/${mode}`] },
    );

    // AppShell's tree mounts a React Query-driven child, so the render needs a
    // QueryClientProvider — otherwise the error boundary swallows the real UI.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
    const { container } = render(
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(container.textContent).toContain("Compile AKMS nodes into Taichi solvers");
    subnav.mechdsl.forEach((label) => {
      expect(container.textContent).toContain(label);
    });
    expect(container.querySelector("aside")).not.toBeNull();
  });
});
