// @vitest-environment jsdom
/**
 * Tests that starter batches surface with draft/generated framing.
 *
 * Covers:
 *   1. starter batches are visually distinct from user-authored batches,
 *   2. copy communicates the run output is Tier-B content the user owns,
 *   3. no visual co-mingling of starter vs user batches.
 *
 * Invariant under test (Two-Tier Trust Model): a starter-batch run produces
 * DRAFT (Tier B) content the user owns and can edit — the UI must say so and
 * must mark starter batches distinctly.
 *
 * Tests exercise the pure StarterBadge component and its helpers directly so
 * they require no Tauri runtime, no React Query wiring, and no mocks.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { StarterBadge, isStarterBatch, starterRunCopy } from "./StarterBadge";

// Testing-library auto-cleanup is not globally registered in this repo's
// vitest config, so unmount between tests to avoid DOM leaking.
afterEach(cleanup);

describe("Starter batch badge + Tier-B framing", () => {
  it("renders a starter badge for a batch with origin === 'starter'", () => {
    // Badge present on starter-origin batches.
    render(<StarterBadge origin="starter" />);
    const badge = screen.getByTestId("starter-badge");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toContain("Starter");
  });

  it("does not render the starter badge for a user-authored batch", () => {
    // User batches carry no starter badge; the two never co-mingle.
    const { container: containerUser } = render(<StarterBadge origin="user" />);
    expect(containerUser.firstChild).toBeNull();

    cleanup();

    // undefined origin (older cached payload) also treated as user.
    const { container: containerUndef } = render(<StarterBadge />);
    expect(containerUndef.firstChild).toBeNull();
  });

  it("shows copy explaining the run produces the user's own draft (Tier B) nodes", () => {
    // Tier-B ownership copy is present on the starter affordance.
    render(<StarterBadge origin="starter" />);
    const badge = screen.getByTestId("starter-badge");
    // Copy is surfaced in the title attribute of the badge element.
    const titleAttr = badge.getAttribute("title") ?? "";
    expect(titleAttr.length).toBeGreaterThan(0);
    // Must reference draft/user-owned nature — key Tier-B words.
    expect(titleAttr.toLowerCase()).toMatch(/draft/);
    expect(titleAttr.toLowerCase()).toMatch(/yours|your own/);
  });
});

describe("isStarterBatch helper", () => {
  it("returns true only for starter origin", () => {
    expect(isStarterBatch("starter")).toBe(true);
    expect(isStarterBatch("user")).toBe(false);
    expect(isStarterBatch(undefined)).toBe(false);
  });
});

describe("starterRunCopy helper", () => {
  it("returns a non-empty Tier-B ownership string", () => {
    const copy = starterRunCopy();
    expect(copy.length).toBeGreaterThan(0);
    expect(copy.toLowerCase()).toMatch(/draft/);
    expect(copy.toLowerCase()).toMatch(/yours|your own/);
  });
});
