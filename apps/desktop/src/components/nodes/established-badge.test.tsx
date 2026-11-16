// @vitest-environment jsdom
/**
 * Tests for the Established vs Draft/Generated badge + report-an-error path.
 *
 * Covers:
 *   1. Established (Tier A) vs Draft/Generated (Tier B) badge across node
 *      lists + graph node detail — the two tiers are never visually ambiguous,
 *   2. a "report an error" affordance is present on established nodes only.
 *
 * Mirrors the StarterBadge testable-seam pattern: exercises the pure
 * EstablishedBadge component and helpers directly — no Tauri runtime, no
 * React Query wiring, no mocks, no process spawning, no network, no file I/O.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import {
  EstablishedBadge,
  ReportIssueLink,
  isEstablished,
  nodeTier,
  reportIssueHref,
} from "./EstablishedBadge";

// Testing-library auto-cleanup is not globally registered in this repo's
// vitest config, so unmount between tests to avoid DOM leaking.
afterEach(cleanup);

// ── Minimal node fixtures ──────────────────────────────────────────────────────

const tierANode = {
  is_established: true as const,
  tier: "A" as const,
  source_ref: "github:CEmM2/Logic-Loom/verified_nodes/node_A.md",
  id: "node_A",
};

const tierBNode = {
  is_established: false as const,
  tier: null,
  source_ref: null,
  id: "node_B",
};

const undefinedEstablishedNode = {
  // is_established deliberately absent — older API payload shape.
  tier: null,
  source_ref: null,
  id: "node_C",
} as { is_established?: boolean; tier: "A" | null; source_ref: string | null; id?: string };

// ── EstablishedBadge render tests ─────────────────────────────────────────────

describe("Established vs Draft/Generated badge", () => {
  it("renders an Established badge for a Tier-A (shipped, is_established:true) node", () => {
    // Tier-A nodes carry a distinct Established badge.
    render(<EstablishedBadge node={tierANode} />);
    const badge = screen.getByTestId("established-badge");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toContain("Established");
    // Tier-B badge must NOT be present — tiers cannot co-mingle.
    expect(screen.queryByTestId("draft-badge")).toBeNull();
  });

  it("renders a Draft/Generated badge for a Tier-B (user-generated) node", () => {
    // Tier-B nodes carry a distinct Draft/Generated badge — never ambiguous.
    render(<EstablishedBadge node={tierBNode} />);
    const badge = screen.getByTestId("draft-badge");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toMatch(/draft/i);
    // Established badge must NOT be present.
    expect(screen.queryByTestId("established-badge")).toBeNull();
  });

  it("renders Draft/Generated badge when is_established is undefined (older payload)", () => {
    // Invariant: undefined → Tier B, never silently ambiguous.
    render(<EstablishedBadge node={undefinedEstablishedNode} />);
    expect(screen.getByTestId("draft-badge")).toBeTruthy();
    expect(screen.queryByTestId("established-badge")).toBeNull();
  });

  it("shows a report-an-error affordance on established nodes only", () => {
    // Report affordance present on Tier-A, absent on Tier-B.
    const { unmount } = render(<ReportIssueLink node={tierANode} />);
    const link = screen.getByTestId("report-issue-link");
    expect(link).toBeTruthy();
    // Link text must be meaningful.
    expect(link.textContent?.toLowerCase()).toMatch(/report/);
    // Must carry a real href seeded with context.
    const href = link.getAttribute("href") ?? "";
    expect(href.length).toBeGreaterThan(0);
    expect(href).toContain("node_A");

    unmount();
    cleanup();

    // Tier-B: affordance must be absent.
    const { container } = render(<ReportIssueLink node={tierBNode} />);
    expect(container.firstChild).toBeNull();
  });
});

// ── Pure helper tests ─────────────────────────────────────────────────────────

describe("isEstablished helper", () => {
  it("returns true only for is_established:true nodes", () => {
    expect(isEstablished(tierANode)).toBe(true);
    expect(isEstablished(tierBNode)).toBe(false);
    expect(isEstablished(undefinedEstablishedNode)).toBe(false);
  });
});

describe("nodeTier helper", () => {
  it("returns the correct human-readable tier label", () => {
    expect(nodeTier(tierANode)).toBe("Established");
    expect(nodeTier(tierBNode)).toBe("Draft / Generated");
    expect(nodeTier(undefinedEstablishedNode)).toBe("Draft / Generated");
  });
});

describe("reportIssueHref helper", () => {
  it("returns a GitHub new-issue URL seeded with node id and source_ref for Tier-A", () => {
    const href = reportIssueHref(tierANode);
    expect(href).not.toBeNull();
    expect(href).toContain("github.com");
    expect(href).toContain("issues/new");
    expect(href).toContain("node_A");
  });

  it("returns null when source_ref is absent (Tier-B / no provenance)", () => {
    expect(reportIssueHref(tierBNode)).toBeNull();
    expect(reportIssueHref(undefinedEstablishedNode)).toBeNull();
  });
});
