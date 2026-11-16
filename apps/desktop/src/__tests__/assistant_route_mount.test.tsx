// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import AssistantOverviewRoute from "../routes/assistant/overview";
import { GlobalRunStatus } from "../components/GlobalRunStatus";

/**
 * This file originally asserted the *placeholder* copy at `/assistant`.
 * That placeholder is **superseded** by the real Assistant Overview screen
 * (the Assistant sub-routes and overview screen).
 *
 * The contract for `/assistant` now lives in `apps/desktop/src/__tests__/
 * assistant/assistant_overview.test.tsx`. This file is kept as a smoke test of the
 * mount only (route renders without crashing) plus the unchanged
 * GlobalRunStatus invariant. Detailed copy assertions belong with the
 * plan that owns the page.
 */

describe("Assistant route mounts + GlobalRunStatus stub", () => {
  it("mounts the real Assistant Overview at /assistant without crashing", () => {
    // The route renders the live Overview with five
    // quick actions. We assert only that the mount works and the page
    // surfaces (one quick-action label is enough to prove we have the
    // real component, not the old placeholder).
    const router = createMemoryRouter(
      [{ path: "/assistant", element: <AssistantOverviewRoute /> }],
      { initialEntries: ["/assistant"] },
    );
    const { container } = render(<RouterProvider router={router} />);

    expect(container.firstChild).not.toBeNull();
    // One verbatim quick-action label — proof we have the live
    // Overview, not the old placeholder.
    expect(container.textContent).toContain("Create a source batch");
  });

  it("GlobalRunStatus renders nothing when the run store is empty", () => {
    // GlobalRunStatus renders nothing when the store is empty.
    // Passes when: mounting <GlobalRunStatus /> with an empty zustand store yields
    // an empty fragment (no children in the DOM).
    const { container } = render(<GlobalRunStatus />);

    // The store starts empty → component must return null → nothing in the DOM.
    expect(container.firstChild).toBeNull();

    // Confirm the data-testid root is also absent.
    expect(container.querySelector('[data-testid="global-run-status"]')).toBeNull();
  });
});
